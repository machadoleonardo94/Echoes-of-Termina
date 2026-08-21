#if !defined(WIFI_PORTAL_H)
#define WIFI_PORTAL_H

//? ------------------------------------------------------------------------------------------
//*! WiFi portal — web server served over the local network in WiFi mode.
//*!
//*!  Routes:
//*!    GET  /              → Media player + SD card management page
//*!    GET  /api/status    → JSON { ip, sd }
//*!    GET  /api/files     → JSON { files: [ { path, name, size } ] }
//*!    POST /api/delete    → body: path=<full SD path under /Echoes/>
//*!    POST /api/volume    → body: v=<0-100>
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"
#include <WebServer.h>

static WebServer portal_server(80);

// ---------------------------------------------------------------------------
// Embedded HTML page
// ---------------------------------------------------------------------------

static const char PORTAL_HTML[] = R"rawliteral(
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Echoes of Termina</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0d0d0d;color:#ddd;padding:20px;max-width:600px;margin:auto}
h1{color:#88ccee;margin-bottom:20px;font-size:1.4rem;letter-spacing:.04em}
.card{background:#161616;border:1px solid #252525;border-radius:8px;padding:16px;margin-bottom:14px}
h2{color:#99ddff;font-size:0.8rem;margin-bottom:12px;text-transform:uppercase;letter-spacing:.08em}
.row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #1e1e1e}
.row:last-child{border-bottom:none}
.fname{font-size:0.85rem;word-break:break-all}
.fsize{color:#555;font-size:0.75rem;margin-left:6px;white-space:nowrap}
.btn{border:1px solid;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:0.78rem;background:transparent;transition:background .15s}
.del{color:#cc3355;border-color:#cc3355}
.del:hover{background:#cc335518}
label{color:#aaa;font-size:0.85rem;display:block;margin-bottom:6px}
input[type=range]{width:100%;accent-color:#88ccee;cursor:pointer}
.stat{font-size:0.82rem;color:#888;line-height:2}
.empty{color:#444;font-style:italic;font-size:0.85rem}
b{color:#bbb}
.ok{color:#4c4}
.err{color:#c44}
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
  <h2>SD Card &mdash; /Echoes</h2>
  <div id="files"><span class="empty">Loading&hellip;</span></div>
</div>

<script>
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function base(p){return p.split('/').pop();}
function fmt(b){if(b<1024)return b+'B';if(b<1048576)return(b/1024).toFixed(1)+'KB';return(b/1048576).toFixed(1)+'MB';}

fetch('/api/status').then(r=>r.json()).then(d=>{
  document.getElementById('status').innerHTML=
    'IP: <b>'+esc(d.ip)+'</b><br>'+
    'SD Card: '+(d.sd?'<span class="ok">Connected</span>':'<span class="err">Not found</span>')+'<br>'+
    'Mode: <b>WiFi</b>';
}).catch(()=>{document.getElementById('status').textContent='Unavailable';});

function loadFiles(){
  fetch('/api/files').then(r=>r.json()).then(d=>{
    var el=document.getElementById('files');
    if(!d.files||!d.files.length){el.innerHTML='<span class="empty">No files in /Echoes</span>';return;}
    el.innerHTML=d.files.map(function(f){
      return '<div class="row"><span><span class="fname">'+esc(base(f.path))+'</span><span class="fsize">'+fmt(f.size)+'</span></span>'+
        '<button class="btn del" onclick="del(\''+f.path.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\')" >Delete</button></div>';
    }).join('');
  }).catch(()=>{document.getElementById('files').innerHTML='<span class="empty">Failed to load files</span>';});
}

function del(path){
  if(!confirm('Delete '+base(path)+'?'))return;
  fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'path='+encodeURIComponent(path)})
    .then(function(r){if(r.ok)loadFiles();else r.text().then(function(t){alert('Error: '+t);});});
}

var volTimer;
document.getElementById('vol').addEventListener('input',function(){
  document.getElementById('vol-val').textContent=this.value;
  clearTimeout(volTimer);var v=this.value;
  volTimer=setTimeout(function(){
    fetch('/api/volume',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'v='+v});
  },250);
});

loadFiles();
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
    String json = "{\"ip\":\"" + WiFi.localIP().toString() + "\",\"sd\":" + (sdcard ? "true" : "false") + "}";
    portal_server.send(200, "application/json", json);
}

static void _portal_files()
{
    String json = "{\"files\":[";
    bool first = true;

    File root = SD_MMC.open("/Echoes");
    if (root && root.isDirectory())
    {
        File f = root.openNextFile();
        while (f)
        {
            if (!f.isDirectory())
            {
                if (!first)
                    json += ",";
                // Escape any quotes in the path
                String path = String(f.name());
                path.replace("\"", "\\\"");
                json += "{\"path\":\"" + path + "\",\"size\":" + String(f.size()) + "}";
                first = false;
            }
            f.close();
            f = root.openNextFile();
        }
        root.close();
    }
    json += "]}";
    portal_server.send(200, "application/json", json);
}

static void _portal_delete()
{
    if (!portal_server.hasArg("path"))
    {
        portal_server.send(400, "text/plain", "Missing path");
        return;
    }
    String path = portal_server.arg("path");

    // Restrict deletions to the /Echoes directory to prevent path traversal
    if (!path.startsWith("/Echoes/") || path.indexOf("..") >= 0)
    {
        portal_server.send(403, "text/plain", "Forbidden");
        return;
    }

    if (SD_MMC.remove(path.c_str()))
    {
        Serial.printf("[PORTAL] Deleted: %s\n", path.c_str());
        portal_server.send(200, "text/plain", "OK");
    }
    else
    {
        portal_server.send(500, "text/plain", "Delete failed");
    }
}

static void _portal_volume()
{
    if (!portal_server.hasArg("v"))
    {
        portal_server.send(400, "text/plain", "Missing v");
        return;
    }
    float vol = portal_server.arg("v").toFloat() / 100.0f;
    if (vol < 0.0f)
        vol = 0.0f;
    if (vol > 1.0f)
        vol = 1.0f;
    audio.volume(vol);
    Serial.printf("[PORTAL] Volume set to %.0f%%\n", vol * 100.0f);
    portal_server.send(200, "text/plain", "OK");
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void start_wifi_portal()
{
    portal_server.on("/", HTTP_GET, _portal_root);
    portal_server.on("/api/status", HTTP_GET, _portal_status);
    portal_server.on("/api/files", HTTP_GET, _portal_files);
    portal_server.on("/api/delete", HTTP_POST, _portal_delete);
    portal_server.on("/api/volume", HTTP_POST, _portal_volume);
    portal_server.begin();
    Serial.println("[PORTAL] Started at http://" + WiFi.localIP().toString() + "/");
}

void handle_wifi_portal()
{
    portal_server.handleClient();
}

#endif // WIFI_PORTAL_H
