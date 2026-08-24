#if !defined(WIFI_PORTAL_H)
#define WIFI_PORTAL_H

//? ------------------------------------------------------------------------------------------
//*! WiFi portal — web server served over the local network in WiFi mode.
//*!
//*!  Routes:
//*!    GET  /              → Media player + SD card file manager page
//*!    GET  /api/status    → JSON { ip, sd, playing, pos, dur, track, volume }  (pos/dur in seconds)
//*!    GET  /api/files     → ?path=/Echoes[/...] → { path, parent, entries:[{name,path,isDir,size,playable}] }
//*!    POST /api/play      → body: path=<full SD path under /Echoes/>
//*!    POST /api/stop      → stop playback
//*!    POST /api/pause     → toggle pause/resume; optional body: state=pause|resume
//*!    POST /api/next      → next track in current folder
//*!    POST /api/prev      → previous track in current folder
//*!    POST /api/volume    → body: v=<0-100>
//*!    POST /api/delete    → body: path=<file under /Echoes/>
//*!    POST /api/rmdir     → body: path=<empty dir under /Echoes/>
//*!    POST /api/mkdir     → body: path=<new dir under /Echoes/>
//*!    POST /api/rename    → body: from=<...>&to=<...>
//*!    POST /api/upload    → multipart file upload to ?dir=/Echoes[/...] (field "file")
//? ------------------------------------------------------------------------------------------

// FTP server is off by default (the portal covers file management; FTP eats
// heap + SD bandwidth the audio path needs). Enable with
// -DPORTAL_ENABLE_FTP=1 in platformio.ini build_flags.
#ifndef PORTAL_ENABLE_FTP
#define PORTAL_ENABLE_FTP 0
#endif

#include "shared/dependencies.h"
#include <WebServer.h>
#include <vector>

static WebServer portal_server(80);

// ---------------------------------------------------------------------------
// MP3 player state
// ---------------------------------------------------------------------------

// The ESP32-audioI2S `Audio` constructor installs I2S_NUM_0 immediately, which
// would clash with the A2DP sink's I2S_NUM_0 in Bluetooth mode. It is therefore
// created lazily, only when the portal (WiFi mode) starts.
static Audio *player = nullptr;
static bool portal_paused = false;   // explicit pause state (drives the lightshow playhead)
static bool portal_running = false;  // portal server started (avoid double start)

static uint8_t player_volume = 21;          // 0..21 (ESP32-audioI2S volume steps)
static std::vector<String> playlist;        // MP3 paths in the current folder
static size_t playlist_index = 0;
static String current_track = "";           // full SD path currently selected
static volatile bool player_eof = false;

// Called by ESP32-audioI2S when an MP3 finishes. Only set a flag here — the
// actual track advance happens after loop() returns, to avoid re-entrancy.
void audio_eof_mp3(const char *info)
{
    player_eof = true;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static String _json_escape(const String &s)
{
    String out;
    out.reserve(s.length() + 8);
    for (unsigned i = 0; i < s.length(); i++)
    {
        char c = s[i];
        switch (c)
        {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        default:
            if (c < 0x20)
            {
                char b[8];
                snprintf(b, sizeof(b), "\\u%04x", (unsigned char)c);
                out += b;
            }
            else
                out += c;
        }
    }
    return out;
}

static String _base_name(const String &path)
{
    int slash = path.lastIndexOf('/');
    return slash < 0 ? path : path.substring(slash + 1);
}

static String _parent_path(const String &path)
{
    if (path == "/Echoes" || path == "/")
        return "/Echoes";
    int slash = path.lastIndexOf('/');
    if (slash <= 0)
        return "/Echoes";
    return path.substring(0, slash);
}

// Strictly a child of /Echoes (a file/dir inside it, never /Echoes itself).
static bool _valid_echoes_child(const String &path)
{
    if (!path.startsWith("/Echoes/"))
        return false;
    if (path.indexOf("..") >= 0)
        return false;
    return true;
}

// /Echoes or anything inside it (for directory navigation).
static bool _valid_echoes_path(const String &path)
{
    if (path == "/Echoes")
        return true;
    return _valid_echoes_child(path);
}

static bool _ends_with_ci(const String &name, const char *ext)
{
    int el = (int)strlen(ext);
    return name.length() >= (unsigned)el &&
           name.substring(name.length() - el).equalsIgnoreCase(ext);
}

// Formats the ESP32-audioI2S decoder can actually play back.
// NOTE: it has NO OPUS/OGG-Vorbis decoder — .opus and .ogg are not playable.
static bool _is_playable(const String &name)
{
    return _ends_with_ci(name, ".mp3") || _ends_with_ci(name, ".aac") ||
           _ends_with_ci(name, ".m4a") || _ends_with_ci(name, ".wav") ||
           _ends_with_ci(name, ".flac") || _ends_with_ci(name, ".raw");
}

// Audio formats the player can stream (and that belong in /Music)
static bool _is_audio(const String &name)
{
    return _ends_with_ci(name, ".mp3") || _ends_with_ci(name, ".wav") ||
           _ends_with_ci(name, ".flac") || _ends_with_ci(name, ".aac") ||
           _ends_with_ci(name, ".m4a") || _ends_with_ci(name, ".ogg") ||
           _ends_with_ci(name, ".opus");
}

// Lightshow scripts (text instructions for the LED engine)
static bool _is_lightshow(const String &name)
{
    return _ends_with_ci(name, ".echoes");
}

static uint8_t _volume_step(uint8_t percent)
{
    uint16_t step = ((uint16_t)percent * 21) / 100;
    if (step > 21)
        step = 21;
    return (uint8_t)step;
}

static uint8_t _volume_percent()
{
    return (uint8_t)(((uint16_t)player_volume * 100) / 21);
}

// ---------------------------------------------------------------------------
// Playlist
// ---------------------------------------------------------------------------

static void _sort_playlist()
{
    for (size_t i = 1; i < playlist.size(); i++)
    {
        String key = playlist[i];
        int j = (int)i - 1;
        while (j >= 0 && playlist[j] > key)
        {
            playlist[j + 1] = playlist[j];
            j--;
        }
        playlist[j + 1] = key;
    }
}

static void _build_playlist(const String &dir)
{
    playlist.clear();
    if (!sdcard)
        return;

    File root = SD_MMC.open(dir.c_str());
    if (!root || !root.isDirectory())
    {
        if (root)
            root.close();
        return;
    }

    File f = root.openNextFile();
    while (f)
    {
        if (!f.isDirectory())
        {
            String path = String(f.path());
            if (_is_playable(_base_name(path)))
                playlist.push_back(path);
        }
        f = root.openNextFile();
    }
    root.close();
    _sort_playlist();
}

// Forward declaration — defined below (attaches the lightshow for a track)
static void _sync_lightshow(const String &audio_path);

static void _play_index(int idx)
{
    int n = (int)playlist.size();
    if (n == 0 || player == nullptr)
        return;
    if (idx < 0)
        idx = n - 1;
    if (idx >= n)
        idx = 0;
    playlist_index = (size_t)idx;
    current_track = playlist[playlist_index];
    portal_paused = false;
    player->connecttoFS(SD_MMC, current_track.c_str());
    player->setVolume(player_volume);
    Serial.printf("[PORTAL] Playing: %s\n", current_track.c_str());
    _sync_lightshow(current_track);
}

static void _play_next()
{
    if (playlist.empty())
        return;
    _play_index((int)playlist_index + 1);
}

static void _play_prev()
{
    if (playlist.empty())
        return;
    _play_index((int)playlist_index - 1);
}

static void _play_path(const String &path)
{
    if (player == nullptr)
        return;
    _build_playlist(_parent_path(path));
    for (size_t i = 0; i < playlist.size(); i++)
    {
        if (playlist[i] == path)
        {
            playlist_index = i;
            break;
        }
    }
    current_track = path;
    portal_paused = false;
    player->connecttoFS(SD_MMC, path.c_str());
    player->setVolume(player_volume);
    Serial.printf("[PORTAL] Playing: %s\n", path.c_str());
    _sync_lightshow(path);
}

// ---------------------------------------------------------------------------
// Lightshow <-> audio sync
// ---------------------------------------------------------------------------

// Returns a best-effort ms position of the audio player. getAudioCurrentTime()
// only reports whole seconds, so the value is interpolated with millis() to
// sub-second resolution. Freezes while paused/stopped (portal_paused).
static uint32_t _audio_position_ms()
{
    static uint32_t last_sec = 0;
    static uint32_t last_at_ms = 0;
    static uint32_t frozen_pos_ms = 0;
    if (player == nullptr)
        return frozen_pos_ms;
    if (portal_paused || !player->isRunning())
        return frozen_pos_ms;   // frozen playhead while paused / stopped
    uint32_t sec = player->getAudioCurrentTime();
    if (sec != last_sec)
    {
        last_sec = sec;
        last_at_ms = millis();
        frozen_pos_ms = sec * 1000UL;
    }
    else
    {
        frozen_pos_ms = sec * 1000UL + (millis() - last_at_ms);
    }
    return frozen_pos_ms;
}

// Finds the .echoes lightshow that belongs to an audio file in a Lightshows
// project folder. Prefers a stem match (Beethoven.mp3 -> Beethoven.echoes),
// then the folder's .echoes only when it is unambiguous.
static String _find_lightshow_for(const String &audio_path)
{
    if (!audio_path.startsWith("/Echoes/Lightshows/"))
        return "";
    String dir = _parent_path(audio_path);
    String stem = _base_name(audio_path);
    int dot = stem.lastIndexOf('.');
    if (dot > 0)
        stem = stem.substring(0, dot);

    String candidate = dir + "/" + stem + ".echoes";
    if (SD_MMC.exists(candidate.c_str()))
        return candidate;

    String found = "";
    File d = SD_MMC.open(dir.c_str());
    if (d && d.isDirectory())
    {
        File f = d.openNextFile();
        while (f)
        {
            String name = _base_name(f.name());
            if (!f.isDirectory() && _is_lightshow(name))
            {
                if (found.length() == 0)
                    found = dir + "/" + name;
                else
                {
                    found = "";   // ambiguous — do not guess
                    break;
                }
            }
            f = d.openNextFile();
        }
        d.close();
    }
    return found;
}

// Loads and attaches the lightshow for *audio_path* (or stops any running one).
static void _sync_lightshow(const String &audio_path)
{
    String show = _find_lightshow_for(audio_path);
    if (show.length() == 0)
    {
        Serial.printf("[Lightshow] No .echoes found for %s\n", audio_path.c_str());
        lightshow_stop();
        return;
    }
    Serial.printf("[Lightshow] Attaching %s\n", show.c_str());
    if (load_lightshow(show.c_str()))
        lightshow_play();
    else
        lightshow_stop();
}

// ---------------------------------------------------------------------------
// Embedded HTML page
// ---------------------------------------------------------------------------

static const char PORTAL_HTML[] = R"rawliteral(
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Echoes of Termina</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0d0d0d;color:#ddd;padding:20px;max-width:640px;margin:auto}
h1{color:#88ccee;margin-bottom:20px;font-size:1.4rem;letter-spacing:.04em}
.card{background:#161616;border:1px solid #252525;border-radius:8px;padding:16px;margin-bottom:14px}
h2{color:#99ddff;font-size:0.8rem;margin-bottom:12px;text-transform:uppercase;letter-spacing:.08em}
.row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1e1e1e}
.row:last-child{border-bottom:none}
.fname{font-size:0.85rem;word-break:break-all;cursor:pointer}
.dirname{color:#aadcff}
.fsize{color:#555;font-size:0.75rem;margin-left:6px;white-space:nowrap}
.btn{border:1px solid;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.78rem;background:transparent;transition:background .15s;margin-left:6px;color:#ccc;border-color:#444}
.btn:hover{background:#ffffff14}
.play{color:#4c4;border-color:#4c4}
.play:hover{background:#4c4a18}
.del{color:#cc3355;border-color:#cc3355}
.del:hover{background:#cc335518}
label{color:#aaa;font-size:0.85rem;display:block;margin-bottom:6px}
input[type=range]{width:100%;accent-color:#88ccee;cursor:pointer}
input[type=text],input[type=file]{width:100%;background:#0d0d0d;border:1px solid #333;border-radius:4px;color:#ddd;padding:6px;margin-bottom:8px;font-size:0.85rem}
.stat{font-size:0.82rem;color:#888;line-height:2}
.empty{color:#444;font-style:italic;font-size:0.85rem}
b{color:#bbb}
.ok{color:#4c4}
.err{color:#c44}
.ctl{display:flex;gap:6px;flex-wrap:wrap}
.ctl .btn{margin-left:0}
.crumb{font-size:0.85rem;color:#99ddff;word-break:break-all}
.note{font-size:0.75rem;color:#555;margin-top:6px}
</style></head>
<body>
<h1>&#9670; Echoes of Termina</h1>

<div class="card">
  <h2>Device Status</h2>
  <div id="status" class="stat">Loading&hellip;</div>
</div>

<div class="card">
  <h2>Volume</h2>
  <label>Level: <span id="vol-val">100</span>%</label>
  <input type="range" id="vol" min="0" max="100" value="100">
</div>

<div class="card">
  <h2>Now Playing</h2>
  <div id="np" class="stat"><span class="empty">Stopped</span></div>
  <div class="ctl" style="margin-top:10px">
    <button class="btn" id="prev">&lt;&lt; Prev</button>
    <button class="btn" id="pp">Pause</button>
    <button class="btn" id="stop">Stop</button>
    <button class="btn" id="next">Next &gt;&gt;</button>
  </div>
</div>

<div class="card">
  <h2>SD Card</h2>
  <div id="crumb" class="crumb">/Echoes</div>
  <div class="ctl" style="margin:8px 0">
    <button class="btn" id="nav-music">Music</button>
    <button class="btn" id="nav-lightshows">Lightshows</button>
  </div>
  <div class="ctl" style="margin:10px 0">
    <input type="file" id="upfile" multiple>
    <button class="btn" id="upbtn">Upload</button>
  </div>
  <div class="ctl">
    <input type="text" id="upfolder" placeholder="Song folder name (optional) &mdash; bundles all files into one folder">
  </div>
  <div class="ctl">
    <input type="text" id="newdir" placeholder="New folder name">
    <button class="btn" id="mkdirbtn">New Folder</button>
  </div>
  <div id="files" style="margin-top:10px"><span class="empty">Loading&hellip;</span></div>
  <div class="note">Select multiple files for a full song pack (audio, .chart, .echoes, .ini, art). With a folder name they're bundled together; without, music goes to /Music and .echoes to /Lightshows.</div>
</div>

<script>
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function base(p){return p.split('/').pop();}
function fmt(b){if(b<1024)return b+'B';if(b<1048576)return(b/1024).toFixed(1)+'KB';return(b/1048576).toFixed(1)+'MB';}
function post(path,body){return fetch(path,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body});}

var curPath='/Echoes';

function refreshStatus(){
  fetch('/api/status').then(r=>r.json()).then(function(d){
    document.getElementById('status').innerHTML=
      'IP: <b>'+esc(d.ip)+'</b><br>'+
      'SD Card: '+(d.sd?'<span class="ok">Connected</span>':'<span class="err">Not found</span>')+'<br>'+
      'Mode: <b>WiFi</b>';
    var np=document.getElementById('np');
    if(d.track){
      np.innerHTML='<b>'+esc(base(d.track))+'</b> '+(d.playing?'<span class="ok">playing</span>':'<span class="empty">paused/stopped</span>');
    } else {
      np.innerHTML='<span class="empty">Stopped</span>';
    }
    document.getElementById('vol').value=d.volume;
    document.getElementById('vol-val').textContent=d.volume;
  }).catch(function(){document.getElementById('status').textContent='Unavailable';});
}

function loadFiles(){
  fetch('/api/files?path='+encodeURIComponent(curPath)).then(r=>r.json()).then(function(d){
    var el=document.getElementById('files');
    document.getElementById('crumb').textContent=d.path||'/Echoes';
    var html='';
    if(d.parent && d.parent!==d.path){
      html+='<div class="row"><span class="fname dirname" data-path="'+esc(d.parent)+'">&uarr; ..</span><span></span></div>';
    }
    if(!d.entries||!d.entries.length){
      el.innerHTML=html+'<span class="empty">Empty folder</span>';
      return;
    }
    var es=d.entries.slice().sort(function(a,b){
      if(a.isDir!==b.isDir)return a.isDir?-1:1;
      return a.name.toLowerCase()<b.name.toLowerCase()?-1:1;
    });
    es.forEach(function(f){
      if(f.isDir){
        html+='<div class="row"><span class="fname dirname" data-path="'+esc(f.path)+'">&#128193; '+esc(f.name)+'</span>'+
          '<span><button class="btn" data-act="rn" data-path="'+esc(f.path)+'">Ren</button>'+
          '<button class="btn del" data-act="rmdir" data-path="'+esc(f.path)+'">Del</button></span></div>';
      } else {
        html+='<div class="row"><span><span class="fname">'+esc(f.name)+'</span><span class="fsize">'+fmt(f.size)+'</span></span>'+
          '<span>'+(f.playable?'<button class="btn play" data-act="play" data-path="'+esc(f.path)+'">Play</button>':'')+
          '<button class="btn" data-act="down" data-path="'+esc(f.path)+'">Down</button>'+
          '<button class="btn" data-act="rn" data-path="'+esc(f.path)+'">Ren</button>'+
          '<button class="btn del" data-act="del" data-path="'+esc(f.path)+'">Del</button></span></div>';
      }
    });
    el.innerHTML=html;
  }).catch(function(){document.getElementById('files').innerHTML='<span class="empty">Failed to load files</span>';});
}

document.getElementById('files').addEventListener('click',function(e){
  var t=e.target;
  if(!t.getAttribute)return;
  var path=t.getAttribute('data-path');
  if(!path)return;
  if(t.classList.contains('dirname')){curPath=path;loadFiles();}
  else if(t.getAttribute('data-act')==='play'){post('/api/play','path='+encodeURIComponent(path)).then(ok);}
  else if(t.getAttribute('data-act')==='del'){del(path,false);}
  else if(t.getAttribute('data-act')==='rmdir'){del(path,true);}
  else if(t.getAttribute('data-act')==='rn'){ren(path);}
  else if(t.getAttribute('data-act')==='down'){window.location.href='/api/download?path='+encodeURIComponent(path);}
});

function ok(r){if(!r.ok)r.text().then(function(t){alert('Error: '+t);});else refreshStatus();}

function del(path,isDir){
  if(!confirm('Delete '+(isDir?'folder ':'')+base(path)+'?'))return;
  post('/api/'+(isDir?'rmdir':'delete'),'path='+encodeURIComponent(path)).then(function(r){
    if(r.ok){loadFiles();refreshStatus();}
    else r.text().then(function(t){alert('Error: '+t);});
  });
}

function ren(path){
  var n=prompt('Rename '+base(path)+' to:',base(path));
  if(!n||n===base(path))return;
  var to=path.slice(0,path.lastIndexOf('/')+1)+n;
  post('/api/rename','from='+encodeURIComponent(path)+'&to='+encodeURIComponent(to)).then(function(r){
    if(r.ok)loadFiles();
    else r.text().then(function(t){alert('Error: '+t);});
  });
}

document.getElementById('prev').addEventListener('click',function(){post('/api/prev','').then(ok);});
document.getElementById('pp').addEventListener('click',function(){post('/api/pause','').then(ok);});
document.getElementById('stop').addEventListener('click',function(){post('/api/stop','').then(ok);});
document.getElementById('next').addEventListener('click',function(){post('/api/next','').then(ok);});

document.getElementById('nav-music').addEventListener('click',function(){curPath='/Echoes/Music';loadFiles();});
document.getElementById('nav-lightshows').addEventListener('click',function(){curPath='/Echoes/Lightshows';loadFiles();});

function uploadFiles(files,dir,i){
  if(i>=files.length){
    document.getElementById('upfile').value='';
    document.getElementById('upfolder').value='';
    loadFiles();
    return;
  }
  var fd=new FormData();
  fd.append('file',files[i]);
  fetch('/api/upload?dir='+encodeURIComponent(dir),{method:'POST',body:fd}).then(function(r){
    if(r.ok)uploadFiles(files,dir,i+1);
    else r.text().then(function(t){alert('Upload failed: '+t);});
  });
}

document.getElementById('upbtn').addEventListener('click',function(){
  var files=document.getElementById('upfile').files;
  if(!files.length){alert('Choose file(s) first');return;}
  var folder=document.getElementById('upfolder').value.trim();
  if(folder){
    var target=curPath+'/'+folder;
    post('/api/mkdir','path='+encodeURIComponent(target)).then(function(r){
      if(!r.ok){alert('Could not create folder "'+folder+'"');return;}
      uploadFiles(files,target,0);
    });
  } else {
    uploadFiles(files,curPath,0);
  }
});

document.getElementById('mkdirbtn').addEventListener('click',function(){
  var n=document.getElementById('newdir').value.trim();
  if(!n){alert('Enter a folder name');return;}
  post('/api/mkdir','path='+encodeURIComponent(curPath+'/'+n)).then(function(r){
    if(r.ok){document.getElementById('newdir').value='';loadFiles();}
    else r.text().then(function(t){alert('Error: '+t);});
  });
});

var volTimer;
document.getElementById('vol').addEventListener('input',function(){
  document.getElementById('vol-val').textContent=this.value;
  clearTimeout(volTimer);var v=this.value;
  volTimer=setTimeout(function(){post('/api/volume','v='+v).then(ok);},250);
});

loadFiles();
refreshStatus();
setInterval(refreshStatus,3000);
</script>
</body></html>
)rawliteral";

// ---------------------------------------------------------------------------
// Request handlers
// ---------------------------------------------------------------------------

static void _portal_root()
{
    portal_server.send(200, "text/html", PORTAL_HTML);
}

static void _portal_status()
{
    bool running = (player != nullptr) && player->isRunning();
    uint32_t pos = 0, dur = 0;
    if (player != nullptr)
    {
        pos = player->getAudioCurrentTime();    // seconds
        dur = player->getAudioFileDuration();   // seconds
    }
    String json = "{\"ip\":\"" + WiFi.localIP().toString() + "\""
                  ",\"sd\":" + (sdcard ? "true" : "false") +
                  ",\"playing\":" + (running ? "true" : "false") +
                  ",\"show\":" + (is_lightshow_playing() ? "true" : "false") +
                  ",\"pos\":" + String(pos) +
                  ",\"dur\":" + String(dur) +
                  ",\"heap\":" + String(ESP.getFreeHeap()) +
                  ",\"track\":\"" + _json_escape(current_track) + "\"" +
                  ",\"volume\":" + String(_volume_percent()) + "}";
    portal_server.send(200, "application/json", json);
}

static void _portal_files()
{
    String path = portal_server.hasArg("path") ? portal_server.arg("path") : String("/Echoes");
    if (!_valid_echoes_path(path))
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }

    File root = SD_MMC.open(path.c_str());
    if (!root || !root.isDirectory())
    {
        if (root)
            root.close();
        portal_server.send(404, "text/plain", "Not a directory");
        return;
    }

    String json = "{\"path\":\"" + _json_escape(path) + "\","
                  "\"parent\":\"" + _json_escape(_parent_path(path)) + "\","
                  "\"entries\":[";
    bool first = true;
    File f = root.openNextFile();
    while (f)
    {
        String fpath = String(f.path());
        String name = _base_name(fpath);
        if (name.length() == 0)
        {
            f = root.openNextFile();
            continue;
        }
        bool isDir = f.isDirectory();
        if (!first)
            json += ",";
        json += "{\"name\":\"" + _json_escape(name) + "\"";
        json += ",\"path\":\"" + _json_escape(fpath) + "\"";
        json += ",\"isDir\":" + String(isDir ? "true" : "false");
        json += ",\"size\":" + String(isDir ? 0 : f.size());
        json += ",\"playable\":" + String((!isDir && _is_playable(name)) ? "true" : "false");
        json += "}";
        first = false;
        f = root.openNextFile();
    }
    root.close();
    json += "]}";
    portal_server.send(200, "application/json", json);
}

static void _portal_play()
{
    if (!portal_server.hasArg("path"))
    {
        portal_server.send(400, "text/plain", "Missing path");
        return;
    }
    String path = portal_server.arg("path");
    if (!_valid_echoes_child(path) || !_is_playable(_base_name(path)))
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }
    _play_path(path);
    portal_server.send(200, "text/plain", "OK");
}

static void _portal_stop()
{
    if (player != nullptr)
        player->stopSong();
    current_track = "";
    portal_paused = false;
    lightshow_stop();
    portal_server.send(200, "text/plain", "OK");
}

// Byte position captured at pause — restored on resume so the decoder
// restarts cleanly (the library's plain pauseResume leaves the stream choppy).
static uint32_t _pause_file_pos = 0;

static void _audio_pause()
{
    if (player == nullptr)
        return;
    _pause_file_pos = player->getFilePos();
    player->pauseResume();
    portal_paused = true;
}

static void _audio_resume()
{
    if (player == nullptr)
        return;
    // Re-seek to the paused byte: setFilePos() frame-aligns and re-syncs the
    // decoder (see processLocalFile's m_resumeFilePos handling).
    if (_pause_file_pos > 0)
        player->setFilePos(_pause_file_pos);
    player->pauseResume();
    portal_paused = false;
}

static void _portal_pause()
{
    // Optional ?state=pause|resume for explicit control; without it, toggle
    // (backwards compatible with the portal UI which used the toggle).
    String state = portal_server.hasArg("state") ? portal_server.arg("state") : "";
    bool running = (player != nullptr) && player->isRunning();
    if (state == "pause")
    {
        if (running)
            _audio_pause();
        else
            portal_paused = true;
    }
    else if (state == "resume")
    {
        if (!running && current_track.length() > 0)
            _audio_resume();
        else
            portal_paused = false;
    }
    else
    {
        if (portal_paused)
            _audio_resume();
        else
            _audio_pause();
    }
    portal_server.send(200, "text/plain", "OK");
}

static void _portal_next()
{
    _play_next();
    portal_server.send(200, "text/plain", "OK");
}

static void _portal_prev()
{
    _play_prev();
    portal_server.send(200, "text/plain", "OK");
}

static void _portal_volume()
{
    if (!portal_server.hasArg("v"))
    {
        portal_server.send(400, "text/plain", "Missing v");
        return;
    }
    int v = portal_server.arg("v").toInt();
    if (v < 0)
        v = 0;
    if (v > 100)
        v = 100;
    player_volume = _volume_step((uint8_t)v);
    if (player != nullptr)
        player->setVolume(player_volume);
    Serial.printf("[PORTAL] Volume set to %d%%\n", v);
    portal_server.send(200, "text/plain", "OK");
}

static void _portal_delete()
{
    if (!portal_server.hasArg("path"))
    {
        portal_server.send(400, "text/plain", "Missing path");
        return;
    }
    String path = portal_server.arg("path");
    if (!_valid_echoes_child(path))
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }
    if (SD_MMC.remove(path.c_str()))
    {
        if (current_track == path)
        {
            if (player != nullptr)
                player->stopSong();
            current_track = "";
        }
        Serial.printf("[PORTAL] Deleted: %s\n", path.c_str());
        portal_server.send(200, "text/plain", "OK");
    }
    else
    {
        portal_server.send(500, "text/plain", "Delete failed");
    }
}

static void _portal_rmdir()
{
    if (!portal_server.hasArg("path"))
    {
        portal_server.send(400, "text/plain", "Missing path");
        return;
    }
    String path = portal_server.arg("path");
    if (!_valid_echoes_child(path))
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }
    if (SD_MMC.rmdir(path.c_str()))
    {
        Serial.printf("[PORTAL] Removed dir: %s\n", path.c_str());
        portal_server.send(200, "text/plain", "OK");
    }
    else
    {
        portal_server.send(500, "text/plain", "Remove failed (folder not empty?)");
    }
}

static void _portal_mkdir()
{
    if (!portal_server.hasArg("path"))
    {
        portal_server.send(400, "text/plain", "Missing path");
        return;
    }
    String path = portal_server.arg("path");
    if (!_valid_echoes_child(path))
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }
    if (SD_MMC.mkdir(path.c_str()))
    {
        Serial.printf("[PORTAL] Created dir: %s\n", path.c_str());
        portal_server.send(200, "text/plain", "OK");
    }
    else
    {
        portal_server.send(500, "text/plain", "mkdir failed");
    }
}

static void _portal_rename()
{
    if (!portal_server.hasArg("from") || !portal_server.hasArg("to"))
    {
        portal_server.send(400, "text/plain", "Missing from/to");
        return;
    }
    String from = portal_server.arg("from");
    String to = portal_server.arg("to");
    if (!_valid_echoes_child(from) || !_valid_echoes_child(to))
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }
    if (SD_MMC.rename(from.c_str(), to.c_str()))
    {
        if (current_track == from)
            current_track = to;
        Serial.printf("[PORTAL] Renamed %s -> %s\n", from.c_str(), to.c_str());
        portal_server.send(200, "text/plain", "OK");
    }
    else
    {
        portal_server.send(500, "text/plain", "Rename failed");
    }
}

static void _portal_download()
{
    if (!portal_server.hasArg("path"))
    {
        portal_server.send(400, "text/plain", "Missing path");
        return;
    }
    String path = portal_server.arg("path");
    if (!_valid_echoes_child(path))
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }

    File file = SD_MMC.open(path.c_str(), FILE_READ);
    if (!file || file.isDirectory())
    {
        if (file)
            file.close();
        portal_server.send(404, "text/plain", "Not found");
        return;
    }

    Serial.printf("[PORTAL] Download: %s (%u bytes)\n", path.c_str(), (unsigned)file.size());

    portal_server.sendHeader("Content-Disposition", "attachment; filename=\"" + _base_name(path) + "\"");
    portal_server.setContentLength(file.size());
    portal_server.send(200, "application/octet-stream", "");

    uint8_t buf[1024];
    while (file.available())
    {
        size_t n = file.read(buf, sizeof(buf));
        portal_server.client().write(buf, n);
        esp_task_wdt_reset();
    }
    file.close();
}

// ---------------------------------------------------------------------------
// File upload
// ---------------------------------------------------------------------------

static File _upload_file;
static String _upload_path;
// File::operator bool() is false once the file is closed, so track the open
// result separately: it tells _portal_upload_done whether the write ever
// started (a null file at START means the target could not be opened).
static bool _upload_file_open = false;

static void _portal_upload_handler()
{
    HTTPUpload &upload = portal_server.upload();

    if (upload.status == UPLOAD_FILE_START)
    {
        String dir = portal_server.hasArg("dir") ? portal_server.arg("dir") : String("/Echoes");
        if (!_valid_echoes_path(dir))
            dir = "/Echoes";

        String fname = upload.filename;
        int cut = -1;
        int slash = fname.lastIndexOf('/');
        int bslash = fname.lastIndexOf('\\');
        if (slash > cut)
            cut = slash;
        if (bslash > cut)
            cut = bslash;
        if (cut >= 0)
            fname = fname.substring(cut + 1);
        if (fname.length() == 0 || fname.indexOf("..") >= 0)
            fname = "upload.bin";

        // Route files into dedicated folders when uploading from the top level.
        // (When browsing a subfolder, the file is placed there as-is.)
        if (dir == "/Echoes")
        {
            if (_is_lightshow(fname))
                dir = "/Echoes/Lightshows";
            else if (_is_audio(fname))
                dir = "/Echoes/Music";
        }

        if (_upload_file)
            _upload_file.close();
        _upload_path = dir + "/" + fname;
        _upload_file = SD_MMC.open(_upload_path.c_str(), FILE_WRITE);
        _upload_file_open = (bool)_upload_file;
        if (!_upload_file)
            Serial.printf("[PORTAL] Upload open failed: %s\n", _upload_path.c_str());
    }
    else if (upload.status == UPLOAD_FILE_WRITE)
    {
        if (_upload_file)
            _upload_file.write(upload.buf, upload.currentSize);
        esp_task_wdt_reset();
    }
    else if (upload.status == UPLOAD_FILE_END)
    {
        if (_upload_file)
        {
            _upload_file.close();
            Serial.printf("[PORTAL] Uploaded: %s (%u bytes)\n", _upload_path.c_str(), (unsigned)upload.totalSize);
        }
    }
}

static void _portal_upload_done()
{
    // _upload_file_open remembers whether the target file was opened at START
    // (File::operator bool() is false after it is closed at END). A failed
    // open means the folder is missing or the SD rejected the write — report
    // it instead of a fake OK.
    if (_upload_file_open)
        portal_server.send(200, "text/plain", "OK");
    else
        portal_server.send(500, "text/plain", "Upload failed (SD write error)");
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void start_wifi_portal()
{
    // Instantiate the MP3 player (I2S_NUM_0) only in WiFi mode, where the A2DP
    // sink does not own the I2S peripheral.
    if (player == nullptr)
    {
        player = new Audio();
        player->setPinout(I2C_BCLK, I2C_LRC, I2C_DOUT);
        player->setVolume(player_volume);
    }

    portal_server.on("/", HTTP_GET, _portal_root);
    portal_server.on("/api/status", HTTP_GET, _portal_status);
    portal_server.on("/api/files", HTTP_GET, _portal_files);
    portal_server.on("/api/play", HTTP_POST, _portal_play);
    portal_server.on("/api/stop", HTTP_POST, _portal_stop);
    portal_server.on("/api/pause", HTTP_POST, _portal_pause);
    portal_server.on("/api/next", HTTP_POST, _portal_next);
    portal_server.on("/api/prev", HTTP_POST, _portal_prev);
    portal_server.on("/api/volume", HTTP_POST, _portal_volume);
    portal_server.on("/api/delete", HTTP_POST, _portal_delete);
    portal_server.on("/api/rmdir", HTTP_POST, _portal_rmdir);
    portal_server.on("/api/mkdir", HTTP_POST, _portal_mkdir);
    portal_server.on("/api/rename", HTTP_POST, _portal_rename);
    portal_server.on("/api/download", HTTP_GET, _portal_download);
    portal_server.on("/api/upload", HTTP_POST, _portal_upload_done, _portal_upload_handler);
    portal_server.begin();

    // Advertise the portal via mDNS so it is reachable at http://echoes.local
    if (MDNS.begin("echoes"))
    {
        MDNS.addService("http", "tcp", 80);
        Serial.println("[PORTAL] Started at http://echoes.local (or http://" + WiFi.localIP().toString() + "/)");
    }
    else
    {
        Serial.println("[PORTAL] mDNS failed — use http://" + WiFi.localIP().toString() + "/");
    }

    // FTP server for power-user file management (serves the SD card).
    // Disabled by default: the portal covers file management, and FTP eats
    // heap + SD bandwidth that the audio path needs. Enable with
    // -DPORTAL_ENABLE_FTP=1 in platformio.ini build_flags.
#if PORTAL_ENABLE_FTP
    if (sdcard)
    {
        ftpSrv.begin("echoes", "termina");
        Serial.println("[FTP] Started on port 21 — user: echoes, pass: termina");
    }
#endif
    portal_running = true;
}

void handle_wifi_portal()
{
    portal_server.handleClient();
#if PORTAL_ENABLE_FTP
    if (sdcard)
        ftpSrv.handleFTP();
#endif
}

// ---------------------------------------------------------------------------
// Standalone show — WiFi off (memory-test workaround)
// ---------------------------------------------------------------------------

// Audio + lightshow pump, independent of the portal/WiFi state. Called from
// the main loop so a show keeps running even after WiFi is torn down.
void run_audio_lightshow()
{
    if (player != nullptr)
    {
        player->loop();
        if (player_eof)
        {
            player_eof = false;
            _play_next();
        }
        // Drive the lightshow from the audio playhead
        if (is_lightshow_playing())
            lightshow_set_playhead_ms(_audio_position_ms());

        // Periodic audio-info probe — confirms the decoder configured the
        // I2S output (rate/channels/bits) and how much RAM is left.
        static uint32_t last_info = 0;
        if (millis() - last_info > 10000)
        {
            last_info = millis();
            Serial.printf("[AUDIO] rate=%u ch=%u bits=%u running=%d heap=%u KB\n",
                          player->getSampleRate(),
                          player->getChannels(),
                          player->getBitsPerSample(),
                          player->isRunning() ? 1 : 0,
                          (unsigned)(ESP.getFreeHeap() / 1024));
        }
    }
    run_lightshow();
}

// Frees the WiFi stack's heap so the audio path can allocate what it needs.
void wifi_disable_for_show()
{
    Serial.printf("[MEM] Free heap before WiFi off: %u KB\n",
                  (unsigned)(ESP.getFreeHeap() / 1024));
    portal_server.close();
    portal_running = false;
    MDNS.end();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
    delay(300);
    Serial.printf("[MEM] Free heap after WiFi off: %u KB\n",
                  (unsigned)(ESP.getFreeHeap() / 1024));
}

// Toggles pause/resume of the current track (button short-press).
void audio_toggle_pause()
{
    if (player == nullptr)
    {
        Serial.println("[BUTTON] No track to pause");
        return;
    }
    if (portal_paused)
    {
        // Resume only when WiFi is off — playing with WiFi on is choppy
        // (the 2-press WiFi action pauses playback for the same reason).
        if (WiFi.status() == WL_CONNECTED)
        {
            Serial.println("[BUTTON] Resume blocked — WiFi is on");
            return;
        }
        _audio_resume();
    }
    else
    {
        _audio_pause();
    }
    Serial.printf("[BUTTON] %s\n", portal_paused ? "Paused" : "Resumed");
}

// Re-enables WiFi + the portal after a standalone show, so uploads resume
// without rebooting. Uses the saved credentials from NVS.
// NOTE: only call this in WiFi mode — in Bluetooth mode the radio belongs to
// the A2DP stack (the button handler guards this).
void wifi_enable()
{
    if (WiFi.status() == WL_CONNECTED && portal_running)
    {
        Serial.println("[WIFI] Already up");
        return;
    }

    Serial.println("[WIFI] Re-enabling WiFi...");
    char ssid[33] = "", pass[33] = "";
    loadCredentials(ssid, pass);

    // Clean driver re-init after the standalone teardown
    WiFi.mode(WIFI_OFF);
    delay(100);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(ssid, pass);

    int tries = 0;
    while (WiFi.status() != WL_CONNECTED && tries < 40)
    {
        delay(250);
        tries++;
    }

    if (WiFi.status() == WL_CONNECTED)
    {
        Serial.printf("[WIFI] Reconnected: %s\n", WiFi.localIP().toString().c_str());
        if (!portal_running)
            start_wifi_portal();   // re-registers routes, mDNS, FTP
        Serial.printf("[MEM] Free heap after WiFi on: %u KB\n",
                      (unsigned)(ESP.getFreeHeap() / 1024));
    }
    else
    {
        Serial.println("[WIFI] Reconnect failed — check saved credentials");
    }
}

// Long-press trigger: picks a random Lightshows project, tears WiFi down and
// plays its audio with the synced lightshow — all from the SD card.
void trigger_standalone_show()
{
    if (!sdcard)
    {
        Serial.println("[SHOW] No SD card");
        return;
    }

    // 1) Collect Lightshow project folders
    std::vector<String> projects;
    File root = SD_MMC.open("/Echoes/Lightshows");
    if (root && root.isDirectory())
    {
        File d = root.openNextFile();
        while (d)
        {
            if (d.isDirectory())
                projects.push_back(String(d.path()));
            d = root.openNextFile();
        }
        root.close();
    }
    if (projects.empty())
    {
        Serial.println("[SHOW] No projects in /Echoes/Lightshows");
        return;
    }
    String dir = projects[esp_random() % projects.size()];

    // 2) Pick the project's audio and .echoes
    String audio = "", show = "";
    File pf = SD_MMC.open(dir.c_str());
    if (pf && pf.isDirectory())
    {
        File f = pf.openNextFile();
        while (f)
        {
            String name = _base_name(f.name());
            if (!f.isDirectory())
            {
                if (_is_playable(name) && audio.length() == 0)
                    audio = String(f.path());
                else if (_is_lightshow(name) && show.length() == 0)
                    show = String(f.path());
            }
            f = pf.openNextFile();
        }
        pf.close();
    }
    if (audio.length() == 0 || show.length() == 0)
    {
        Serial.printf("[SHOW] Project %s needs an audio file and a .echoes\n",
                      dir.c_str());
        return;
    }

    // 3) Free RAM, then play audio + attach the lightshow (SD only)
    wifi_disable_for_show();
    _play_path(audio);
    if (!is_lightshow_playing())
    {
        if (load_lightshow(show.c_str()))
            lightshow_play();
        else
            Serial.println("[SHOW] Lightshow load failed");
    }
    Serial.printf("[SHOW] Standalone: %s  +  %s\n", audio.c_str(), show.c_str());
}

#endif // WIFI_PORTAL_H
