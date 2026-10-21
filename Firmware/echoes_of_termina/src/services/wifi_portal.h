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
//*!                           Answers immediately, then hands the track to the
//*!                           standalone player and drops Wi-Fi (see _play_standalone):
//*!                           playback cannot run with the radio up on a board
//*!                           without PSRAM, so this endpoint repurposes the same
//*!                           path the long-press show uses.
//*!    POST /api/stop      → stop playback
//*!    POST /api/pause     → toggle pause/resume; optional body: state=pause|resume
//*!    POST /api/next      → next track in current folder
//*!    POST /api/prev      → previous track in current folder
//*!    POST /api/volume    → body: v=<0-100>  (persisted to NVS, restored at boot)
//*!    POST /api/leds      → body: count=<1-200> and/or anim=<0|1|2>  (persisted;
//*!                          both applied live, but see the note in _portal_leds)
//*!    POST /api/delete    → body: path=<file under /Echoes/>
//*!    POST /api/rmdir     → body: path=<empty dir under /Echoes/>
//*!    POST /api/mkdir     → body: path=<new dir under /Echoes/>
//*!    POST /api/rename    → body: from=<...>&to=<...>
//*!    POST /api/upload    → multipart file upload to ?dir=/Echoes[/...] (field "file")
//*!    POST /api/stage-firmware → multipart upload (field "file") written to
//*!                           /firmware.bin on the SD root; applied on the next
//*!                           boot by updatefromSD(), which runs before WiFi goes up
//*!    POST /api/reboot    → restart the board (applies a staged /firmware.bin)
//? ------------------------------------------------------------------------------------------

// FTP server is off by default (the portal covers file management; FTP eats
// heap + SD bandwidth the audio path needs). Enable with
// -DPORTAL_ENABLE_FTP=1 in platformio.ini build_flags.
#ifndef PORTAL_ENABLE_FTP
#define PORTAL_ENABLE_FTP 0
#endif

// Minimum free internal RAM before re-enabling WiFi. Measured on Echoes V1: a
// WiFi off/on cycle costs ~45 KB, and the portal + mDNS need slack on top.
// Below this the radio's own allocators fail (lwIP ERR_MEM / newlib lock init),
// which aborts and reboots rather than returning an error.
#define WIFI_MIN_HEAP_KB 70

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
static uint8_t player_volume_percent = 100; // authoritative 0..100 level (see _set_volume_percent)
static std::vector<String> playlist;        // MP3 paths in the current folder
static size_t playlist_index = 0;
static String current_track = "";           // full SD path currently selected
static volatile bool player_eof = false;

// Pending "play this now" request from POST /api/play. Playback needs the radio
// down (see _play_standalone), but the HTTP reply has to get out first, so the
// handler only queues the path and the main loop acts on it a moment later.
static String pending_play_path = "";
static uint32_t pending_play_at_ms = 0;

// How long POST /api/play waits before dropping the radio. The reply is a few
// bytes on a LAN, so this only has to cover one round trip.
#define PENDING_PLAY_DELAY_MS 900

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

// The authoritative level, 0..100. NOT derived back from player_volume: that
// 0..21 step is too coarse to ramp on, because 61% and 66% both land on step 13
// (66 * 21 / 100 == 13), so a 5%-per-tick volume ramp that read its position back
// out of the step would stall at the first rounding boundary.
static uint8_t _volume_percent()
{
    return player_volume_percent;
}

// ---------------------------------------------------------------------------
// Volume persistence — NVS namespace "audio", key "volume" (0..100 percent).
// The percent is stored rather than the 0..21 player step so the saved level
// survives a change of the decoder's step range.
// ---------------------------------------------------------------------------

static uint8_t volume_persisted_percent = 0xFF; // sentinel: nothing written yet

static void _save_volume(uint8_t percent)
{
    if (percent == volume_persisted_percent)
        return; // skip redundant writes while the UI slider repeats a value
    internal_storage.begin("audio", false);
    internal_storage.putUChar("volume", percent);
    internal_storage.end();
    volume_persisted_percent = percent;
    Serial.printf("[PORTAL] Volume saved to NVS: %u%%\n", (unsigned)percent);
}

// The single writer for the local player's volume. Both the portal's slider and
// the button ramp go through here, which is what stops them from fighting: the
// percent is stored, the decoder step is derived from it, and NVS is written
// once. Safe with no player alive — the percent is kept and _ensure_player()
// applies it when the next track starts.
//
// `persist` exists for the ramp. A hold re-levels every 150 ms
// (buttons.h BTN_REPEAT_MS), and writing NVS on every tick meant ~20 flash write
// cycles for one three-second hold — measured in the log as one
// "Volume saved to NVS" line per tick. The ramp therefore persists on its first
// tick and again when the finger comes up, and not in between.
static void _set_volume_percent(uint8_t percent, bool persist = true)
{
    if (percent > 100)
        percent = 100;
    player_volume_percent = percent;
    player_volume = _volume_step(percent);
    if (player != nullptr)
        player->setVolume(player_volume);
    if (persist)
        _save_volume(percent);
}

static uint8_t _load_volume_percent()
{
    internal_storage.begin("audio", true);
    uint8_t percent = internal_storage.getUChar("volume", _volume_percent());
    internal_storage.end();
    if (percent > 100)
        percent = 100;
    volume_persisted_percent = percent;
    _set_volume_percent(percent);
    Serial.printf("[PORTAL] Volume loaded from NVS: %u%%\n", (unsigned)percent);
    return percent;
}

// ---------------------------------------------------------------------------
// Playlist
// ---------------------------------------------------------------------------

// Upper bound on how many paths one playlist may hold. Building it happens while
// the radio is down (~86 KB free), so the cap is not about the tight moment — it is
// there so a card with hundreds of projects cannot turn the list into tens of
// kilobytes of Strings for a walk that starts at the current track anyway.
// Reaching it is logged, never silent.
#define PLAYLIST_MAX_TRACKS 128

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

// The first playable file inside *dir* ("" when there is none).
//
// This is the shape of a Lightshows project: one audio file plus its .echoes, so
// "the project's song" is simply the first playable entry.
static String _first_playable_in(const String &dir)
{
    String found = "";
    File d = SD_MMC.open(dir.c_str());
    if (!d || !d.isDirectory())
    {
        if (d)
            d.close();
        return found;
    }
    File f = d.openNextFile();
    while (f)
    {
        if (!f.isDirectory())
        {
            String path = String(f.path());
            if (_is_playable(_base_name(path)))
            {
                found = path;
                break;
            }
        }
        f = d.openNextFile();
    }
    d.close();
    return found;
}

// True for a directory that is itself a Lightshows project folder — a direct child
// of /Echoes/Lightshows, i.e. exactly the folder shape that holds one song.
static bool _is_lightshows_project_dir(const String &dir)
{
    const char *kBase = "/Echoes/Lightshows/";
    size_t base_len = strlen(kBase);
    if (!dir.startsWith(kBase) || dir.length() <= base_len)
        return false;
    return dir.indexOf('/', (unsigned)base_len) < 0;
}

// One track per Lightshows project, sorted by path.
//
// Why this exists: a project folder holds exactly ONE audio file, so a per-folder
// playlist is a one-entry list — and _play_next()/_play_prev() wrap a one-entry
// list straight back onto itself. That is the "next song restarts the same song"
// bug, and the same wrap is why every track looped forever at its natural end: the
// transport was right, the playlist it walked was too narrow. When the current
// folder IS a project folder, the playlist therefore becomes the collection.
static void _build_lightshows_playlist()
{
    playlist.clear();
    File root = SD_MMC.open("/Echoes/Lightshows");
    if (!root || !root.isDirectory())
    {
        if (root)
            root.close();
        return;
    }

    File d = root.openNextFile();
    while (d)
    {
        if (d.isDirectory())
        {
            String track = _first_playable_in(String(d.path()));
            if (track.length() > 0)
            {
                if (playlist.size() >= PLAYLIST_MAX_TRACKS)
                {
                    Serial.printf("[PORTAL] Playlist capped at %u tracks — the rest of the collection is not walked\n",
                                  (unsigned)PLAYLIST_MAX_TRACKS);
                    break;
                }
                playlist.push_back(track);
            }
        }
        d = root.openNextFile();
    }
    root.close();
    _sort_playlist();
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

    // A single track in a project folder is not a playlist of one; it is one entry
    // of the Lightshows collection. Widen it so "next" has somewhere to go.
    if (playlist.size() <= 1 && _is_lightshows_project_dir(dir))
        _build_lightshows_playlist();
    else
        _sort_playlist();
}

// Forward declaration — defined below (attaches the lightshow for a track)
static void _sync_lightshow(const String &audio_path);

// Forward declaration — defined far below, once the WiFi teardown helpers exist.
static void _service_pending_play();

// Reads the file through the app's own SD handle, independently of the audio
// library, so a missing/empty/corrupt file can be told apart from the library
// failing to consume a good one. Also prints the first bytes: "ID3" or an MP3
// frame sync (0xFFEx) is what the library's parser looks for.
static void _log_sd_file(const char *path)
{
    File f = SD_MMC.open(path, FILE_READ);
    if (!f || f.isDirectory())
    {
        Serial.printf("[AUDIO] SD open FAILED: %s\n", path);
        if (f)
            f.close();
        return;
    }

    uint8_t magic[4] = {0, 0, 0, 0};
    f.read(magic, sizeof(magic));
    bool id3 = (magic[0] == 'I' && magic[1] == 'D' && magic[2] == '3');
    bool sync = (magic[0] == 0xFF && (magic[1] & 0xE0) == 0xE0);
    Serial.printf("[AUDIO] SD file %u bytes, first4=%02X %02X %02X %02X (ID3=%d MP3sync=%d)\n",
                  (unsigned)f.size(), magic[0], magic[1], magic[2], magic[3], id3, sync);
    f.close();
}

// Reports the decoder's state right after a file was handed to it. The audio
// library logs its failures with log_e(), which CORE_DEBUG_LEVEL=0 compiles out,
// so when a decoder silently refuses to start this is the only visible trace:
// a decoder that never initialised keeps the library's defaults (16 kHz, mono
// bookkeeping) and isRunning() stays false.
//
// NOTE: those defaults on their own do NOT prove a failure. Measured on a
// successful play, the call right after connecttoFS() still reported
// rate=16000 dur=0 because the header had not been parsed yet; the probe 10 s
// later showed rate=48000 running=1 t=4. Judge by isRunning() and getFilePos().
static void _log_player_state(const char *tag)
{
    if (player == nullptr)
    {
        Serial.println("[AUDIO] player not created");
        return;
    }

    uint32_t heap_kb = ESP.getFreeHeap() / 1024;
    Serial.printf("[AUDIO] %s -> running=%d rate=%u ch=%u bits=%u dur=%u s size=%u heap=%u KB\n",
                  tag,
                  player->isRunning() ? 1 : 0,
                  (unsigned)player->getSampleRate(),
                  (unsigned)player->getChannels(),
                  (unsigned)player->getBitsPerSample(),
                  (unsigned)player->getAudioFileDuration(),
                  (unsigned)player->getFileSize(),
                  (unsigned)heap_kb);

    // The DecodePath needs ~32 KB of DMA-capable internal RAM for its I2S DMA
    // buffers alone (16 x 512 frames), plus the decoder's own buffers, and DMA
    // memory must come from internal RAM even when PSRAM is fitted. Measured
    // headroom: ~45 KB in the old configuration where the portal played with the
    // radio up (the one that produced no audio), 86 KB after wifi_disable_for_show().
    if (!player->isRunning() && heap_kb < 64)
        Serial.println("[AUDIO] NOTE: only " + String(heap_kb) +
                       " KB internal RAM left — the I2S DMA + decoder buffers may not fit. "
                       "The play path should have torn WiFi down first; check the [MEM] lines.");
}

// Internal-RAM census, including the figure that actually decides whether the
// decoder can start: the LARGEST free DMA-capable block. The I2S driver asks for
// 16 x 512 frames x 2ch x 16bit = 32 KB contiguous out of that pool, so a
// healthy total with a fragmented pool still fails the install.
static void _log_mem(const char *tag)
{
    Serial.printf("[MEM] %-20s free=%uK int=%uK dma=%uK dma_largest=%uK\n",
                  tag,
                  (unsigned)(ESP.getFreeHeap() / 1024),
                  (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
                  (unsigned)(heap_caps_get_free_size(MALLOC_CAP_DMA) / 1024),
                  (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_DMA) / 1024));
}

// Creates the player if it does not exist yet.
//
// The Audio constructor is the ONLY place ESP32-audioI2S installs I2S_NUM_0, and
// i2s_driver_install() is the only consumer of that 32 KB DMA block. Measured on
// Echoes V1, that install DOES succeed with the radio up — 95 KB of contiguous
// DMA-capable RAM is still free that early in setup — but it leaves the whole
// system on a ~45 KB budget, which is not enough to then play anything.
// Rebuilding after wifi_disable_for_show() puts the same install on a 132 KB heap.
static void _ensure_player()
{
    if (player != nullptr)
        return;

    _log_mem("new Audio: before");
    player = new Audio();
    player->setPinout(I2C_BCLK, I2C_LRC, I2C_DOUT);
    // Seeds player_volume_percent and player_volume from NVS, then hands the
    // derived step to the freshly built decoder.
    _load_volume_percent();
    player->setVolume(player_volume);
    _log_mem("new Audio: after");
}

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
    _log_sd_file(current_track.c_str());
    if (!player->connecttoFS(SD_MMC, current_track.c_str()))
        Serial.printf("[AUDIO] connecttoFS FAILED for %s\n", current_track.c_str());
    player->setVolume(player_volume);
    Serial.printf("[PORTAL] Playing: %s  (track %u/%u)\n", current_track.c_str(),
                  (unsigned)(playlist_index + 1), (unsigned)playlist.size());
    _log_player_state("play_index");
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
    playlist_index = 0;
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
    _log_sd_file(path.c_str());
    if (!player->connecttoFS(SD_MMC, path.c_str()))
        Serial.printf("[AUDIO] connecttoFS FAILED for %s\n", path.c_str());
    player->setVolume(player_volume);
    Serial.printf("[PORTAL] Playing: %s  (track %u/%u)\n", path.c_str(),
                  (unsigned)(playlist_index + 1), (unsigned)playlist.size());
    _log_player_state("play_path");
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
    static uint32_t last_sec = 0xFFFFFFFF; // sentinel: no reading taken yet
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
        // Interpolate, but only briefly. If the decoder stops advancing —
        // stalled, seeking, or never started — the playhead must FREEZE, not
        // run away with millis(). The old unbounded version did exactly that:
        // getAudioCurrentTime() sat at 0, and because last_sec started at 0 the
        // first comparison matched, so the playhead became raw uptime (1,032,234 ms
        // in the log). The engine then fired the entire show at once.
        uint32_t since = millis() - last_at_ms;
        if (since < 1000)
            frozen_pos_ms = sec * 1000UL + since;
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
input[type=text],input[type=file],select{width:100%;background:#0d0d0d;border:1px solid #333;border-radius:4px;color:#ddd;padding:6px;margin-bottom:8px;font-size:0.85rem}
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
  <h2>LED Strip</h2>
  <label>LEDs: <span id="led-val">144</span></label>
  <input type="range" id="leds" min="1" max="200" value="144">
  <div class="note" id="led-cap">&nbsp;</div>
  <label style="margin-top:10px">Animation for songs without a lightshow</label>
  <select id="anim">
    <option value="0">None</option>
    <option value="1">Audio envelope</option>
    <option value="2">FFT spread</option>
  </select>
  <div class="note">A song's own .echoes lightshow always takes priority. Changes apply from the next Play &mdash; pressing Play drops Wi-Fi so the decoder can get the RAM.</div>
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
// Set once a Play request has been accepted: the device drops Wi-Fi to free the
// RAM the decoder needs, so polling would only produce failed requests.
var stopPolling=false;

function refreshStatus(){
  if(stopPolling)return;
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
    if(d.leds!==undefined){
      // Don't fight the user mid-drag: only echo the device's value when the
      // slider isn't the thing being changed right now.
      var ls=document.getElementById('leds');
      if(document.activeElement!==ls) ls.value=d.leds;
      document.getElementById('led-val').textContent=d.leds;
      var pct=Math.round(((d.cap+1)/256)*100);
      document.getElementById('led-cap').innerHTML=
        'Brightness capped at <b>'+d.cap+'</b>/255 (~'+pct+'%), roughly <b>'+d.ma+' mA</b> worst case.';
    }
    if(d.anim!==undefined) document.getElementById('anim').value=d.anim;
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
  else if(t.getAttribute('data-act')==='play'){playFile(path);}
  else if(t.getAttribute('data-act')==='del'){del(path,false);}
  else if(t.getAttribute('data-act')==='rmdir'){del(path,true);}
  else if(t.getAttribute('data-act')==='rn'){ren(path);}
  else if(t.getAttribute('data-act')==='down'){window.location.href='/api/download?path='+encodeURIComponent(path);}
});

function ok(r){if(!r.ok)r.text().then(function(t){alert('Error: '+t);});else refreshStatus();}

// Play is a two-step affair: the device answers, then switches its radio off so
// the decoder can allocate the I2S DMA buffers. Report that instead of leaving
// the page looking like the click did nothing.
function playFile(path){
  var msg='<b>'+esc(base(path))+'</b> <span class="ok">starting&hellip;</span><br>'+
    '<span class="empty">Wi-Fi is switching off so the decoder can claim the RAM. '+
    'This page goes offline &mdash; the track keeps playing.</span>';
  stopPolling=true;
  post('/api/play','path='+encodeURIComponent(path)).then(function(r){
    if(!r.ok){r.text().then(function(t){stopPolling=false;alert('Error: '+t);});return;}
    document.getElementById('np').innerHTML=msg;
  }).catch(function(){document.getElementById('np').innerHTML=msg;});
}

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

var ledTimer;
document.getElementById('leds').addEventListener('input',function(){
  document.getElementById('led-val').textContent=this.value;
  clearTimeout(ledTimer);var v=this.value;
  // Debounced longer than the volume: each change reallocates the pixel buffer.
  ledTimer=setTimeout(function(){post('/api/leds','count='+v).then(function(r){
    if(r.ok)refreshStatus(); else r.text().then(function(t){alert('Error: '+t);});
  });},400);
});

document.getElementById('anim').addEventListener('change',function(){
  post('/api/leds','anim='+this.value).then(function(r){
    if(!r.ok) r.text().then(function(t){alert('Error: '+t);});
  });
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
                  ",\"volume\":" + String(_volume_percent()) +
                  // LED strip state. `cap` and `ma` are derived from the count, so
                  // the UI can show the user why changing the count changes the
                  // brightness ceiling (and what it costs in current).
                  ",\"leds\":" + String((unsigned)majora_led_count) +
                  ",\"anim\":" + String((unsigned)led_anim_mode_get()) +
                  ",\"cap\":" + String((unsigned)led_brightness_ceiling()) +
                  ",\"ma\":" + String((unsigned)led_worst_case_ma()) + "}";
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

// Answers the request, then hands the track to the standalone player.
//
// Playback cannot start here: the I2S DMA buffers only fit once the radio has
// given its heap back (see _play_standalone), and that teardown closes the very
// socket this reply is travelling on. The path is therefore queued and the main
// loop acts on it ~1 s from now — long enough for the browser to receive the
// response, short enough to feel immediate.
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

    pending_play_path = path;
    pending_play_at_ms = millis() + PENDING_PLAY_DELAY_MS;

    portal_server.send(200, "text/plain",
                       "Starting playback - Wi-Fi will drop so the decoder can claim the RAM");
    Serial.printf("[PORTAL] /api/play queued: %s (dropping Wi-Fi in %d ms)\n",
                  path.c_str(), PENDING_PLAY_DELAY_MS);
}

// True while the decoder is actually producing audio. Because playback is routed
// through _play_standalone, this is false whenever the portal is reachable — so
// the transport endpoints below report that instead of answering a silent OK.
static bool _playback_live()
{
    return player != nullptr && player->isRunning();
}

static const char PORTAL_IDLE_MSG[] =
    "No playback session - the portal is idle. Press Play to start a standalone show.";

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
    if (!_playback_live())
    {
        portal_server.send(409, "text/plain", PORTAL_IDLE_MSG);
        return;
    }
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
    if (!_playback_live())
    {
        portal_server.send(409, "text/plain", PORTAL_IDLE_MSG);
        return;
    }
    _play_next();
    portal_server.send(200, "text/plain", "OK");
}

static void _portal_prev()
{
    if (!_playback_live())
    {
        portal_server.send(409, "text/plain", PORTAL_IDLE_MSG);
        return;
    }
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
    // Same writer the button ramp uses, so a slider move and a button hold can
    // never leave two different ideas of the current level behind.
    _set_volume_percent((uint8_t)v);
    Serial.printf("[PORTAL] Volume set to %d%%\n", v);
    portal_server.send(200, "text/plain", "OK");
}

// POST /api/leds — body: count=<1-200> and/or anim=<0|1|2>
//
// Both values are validated, persisted to NVS and applied immediately (the strip is
// resized and the ceiling recomputed on the spot). The one caveat worth knowing:
// pressing Play tears Wi-Fi down so the decoder can claim the RAM
// (see _play_standalone), so the portal is unreachable while a song is playing.
// A change made here therefore takes effect from the NEXT Play.
static void _portal_leds()
{
    bool changed = false;

    if (portal_server.hasArg("count"))
    {
        int n = portal_server.arg("count").toInt();
        if (n < 1 || n > (int)MAJORA_MAX_LEDS)
        {
            portal_server.send(400, "text/plain", "count must be 1..200");
            return;
        }
        set_led_count((uint16_t)n);
        changed = true;
    }

    if (portal_server.hasArg("anim"))
    {
        int a = portal_server.arg("anim").toInt();
        if (a < 0 || a > (int)LED_ANIM_FFT)
        {
            portal_server.send(400, "text/plain", "anim must be 0..2");
            return;
        }
        set_led_anim_mode((uint8_t)a);
        changed = true;
    }

    if (!changed)
    {
        portal_server.send(400, "text/plain", "Missing count or anim");
        return;
    }

    Serial.printf("[PORTAL] LEDs: count=%u anim=%s ceiling=%u/255 (~%u mA)\n",
                  (unsigned)majora_led_count, led_anim_mode_name(),
                  (unsigned)led_brightness_ceiling(),
                  (unsigned)led_worst_case_ma());
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
// Firmware staging — put a new image on the SD root for updatefromSD() to apply
// ---------------------------------------------------------------------------
//
// POST /api/stage-firmware (multipart, field "file") writes straight to
// /firmware.bin and nothing else. Unlike /api/upload there is no `dir` argument,
// so it cannot be pointed anywhere else on the card.
//
// The image is deliberately NOT applied here. updatefromSD() runs from setup(),
// before the radio exists, so staging and applying are separate steps: upload now
// (radio up — the only time the portal is reachable at all), reboot, and the flash
// then happens with WiFi, I2S and the decoder all absent. That is the point: a
// board playing a standalone show has its radio off and espota cannot reach it.
static File _stage_file;
static size_t _stage_bytes = 0;

static void _portal_stage_fw_handler()
{
    HTTPUpload &upload = portal_server.upload();

    if (upload.status == UPLOAD_FILE_START)
    {
        _stage_bytes = 0;
        if (_stage_file)
            _stage_file.close();
        _stage_file = SD_MMC.open("/firmware.bin", FILE_WRITE);
        Serial.printf("[PORTAL] Staging firmware to /firmware.bin (%s)\n",
                      _stage_file ? "open" : "OPEN FAILED");
    }
    else if (upload.status == UPLOAD_FILE_WRITE)
    {
        if (_stage_file)
        {
            _stage_file.write(upload.buf, upload.currentSize);
            _stage_bytes += upload.currentSize;
        }
        esp_task_wdt_reset(); // a 2 MB upload must not look like a hung loop
    }
    else if (upload.status == UPLOAD_FILE_END)
    {
        if (_stage_file)
        {
            _stage_file.close();
            Serial.printf("[PORTAL] Firmware staged: %u bytes\n", (unsigned)_stage_bytes);
        }
    }
}

static void _portal_stage_fw_done()
{
    // A truncated image is worse than none: updatefromSD() would flash it and the
    // board would reboot into something broken. An ESP32 app image here is ~2 MB,
    // so anything under 256 KB is a failed upload — delete it, do not leave a trap.
    if (_stage_bytes < 262144)
    {
        SD_MMC.remove("/firmware.bin");
        Serial.printf("[PORTAL] Rejected staged firmware: %u bytes (removed)\n",
                      (unsigned)_stage_bytes);
        portal_server.send(400, "text/plain",
                           "Upload too small (" + String((unsigned)_stage_bytes) +
                           " bytes) - removed. Expected a ~2 MB firmware image.");
        return;
    }

    portal_server.send(200, "text/plain",
                       "Staged " + String((unsigned)_stage_bytes) +
                       " bytes at /firmware.bin - POST /api/reboot to apply");
}

// Restarts the board. Paired with staging above: updatefromSD() only runs during
// setup(), so a staged image needs a reboot before it takes effect.
static void _portal_reboot()
{
    Serial.println("[PORTAL] Reboot requested");
    portal_server.send(200, "text/plain",
                       "Rebooting - updatefromSD() will apply /firmware.bin if it is staged");
    delay(750); // let the reply drain; the reset is about to kill the radio
    ESP.restart();
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void start_wifi_portal()
{
    // NOTE: no player is created here. It used to be, purely so /api/status and the
    // volume slider had something to talk to, and it cost ~46 KB of DMA-capable
    // internal RAM to sit idle (measured: 105 KB free before it, 59 KB after, with
    // the radio already up). Playback goes through _play_standalone, which builds
    // its own player once the radio is down — so an idle one bought nothing and
    // left the whole portal on a 45 KB budget.
    //
    // Restore the saved volume here rather than in _ensure_player(): the player no
    // longer exists while the portal idles, but /api/status must still report the
    // stored level or the slider would snap back to 100% on every page load.
    player_volume = _volume_step(_load_volume_percent());

    portal_server.on("/", HTTP_GET, _portal_root);
    portal_server.on("/api/status", HTTP_GET, _portal_status);
    portal_server.on("/api/files", HTTP_GET, _portal_files);
    portal_server.on("/api/play", HTTP_POST, _portal_play);
    portal_server.on("/api/stop", HTTP_POST, _portal_stop);
    portal_server.on("/api/pause", HTTP_POST, _portal_pause);
    portal_server.on("/api/next", HTTP_POST, _portal_next);
    portal_server.on("/api/prev", HTTP_POST, _portal_prev);
    portal_server.on("/api/volume", HTTP_POST, _portal_volume);
    portal_server.on("/api/leds", HTTP_POST, _portal_leds);
    portal_server.on("/api/delete", HTTP_POST, _portal_delete);
    portal_server.on("/api/rmdir", HTTP_POST, _portal_rmdir);
    portal_server.on("/api/mkdir", HTTP_POST, _portal_mkdir);
    portal_server.on("/api/rename", HTTP_POST, _portal_rename);
    portal_server.on("/api/download", HTTP_GET, _portal_download);
    portal_server.on("/api/upload", HTTP_POST, _portal_upload_done, _portal_upload_handler);
    portal_server.on("/api/stage-firmware", HTTP_POST, _portal_stage_fw_done, _portal_stage_fw_handler);
    portal_server.on("/api/reboot", HTTP_POST, _portal_reboot);
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
    // A queued POST /api/play is acted on here, outside the request handler, so
    // the Wi-Fi teardown happens after the HTTP reply has drained.
    _service_pending_play();

    if (player != nullptr)
    {
        // Keep the animation tap's view of the stream format current. The library
        // hook only receives the buffer and a frame count, so it cannot work out
        // how the samples are interleaved on its own.
        led_pcm_set_format(player->getChannels(), player->getBitsPerSample(),
                           player->getSampleRate());
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
            // t = the decoder's own position in whole seconds; file = bytes read.
            // Both freeze if the decode/I2S pipeline stalls, whereas the lightshow
            // playhead keeps moving either way because it interpolates with millis().
            Serial.printf("[AUDIO] rate=%u ch=%u bits=%u running=%d t=%u s file=%u heap=%u KB\n",
                          player->getSampleRate(),
                          player->getChannels(),
                          player->getBitsPerSample(),
                          player->isRunning() ? 1 : 0,
                          (unsigned)player->getAudioCurrentTime(),
                          (unsigned)player->getFilePos(),
                          (unsigned)(ESP.getFreeHeap() / 1024));
        }
    }
    else if (portal_running)
    {
        // Portal up, no player — the normal idle state. With no [AUDIO] probe to
        // print there would be no periodic trace at all, so report the headroom a
        // Play press will have to work with. This is the figure the change to
        // start_wifi_portal() was meant to lift (45 KB -> ~91 KB).
        static uint32_t last_idle = 0;
        if (millis() - last_idle > 10000)
        {
            last_idle = millis();
            _log_mem("idle (portal)");
        }
    }
    run_lightshow();

    // Generated animations. They yield to an authored show (checked inside) and
    // detect audio from the PCM tap rather than from the player object, so this
    // needs no SD/Bluetooth split — in Bluetooth mode the A2DP stream reader is
    // filling the same ring buffer.
    run_led_animation();
}

// Frees the WiFi stack's heap so the audio path can allocate what it needs.
void wifi_disable_for_show()
{
    Serial.printf("[MEM] Free heap before WiFi off: %u KB\n",
                  (unsigned)(ESP.getFreeHeap() / 1024));
    // OTA's UDP socket lives on the netif that is about to disappear. Close it
    // first — handle() would otherwise poll a socket with no TCP/IP stack
    // behind it, which is the "Invalid mbox" assert all over again.
    stop_OTA();
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

// ---------------------------------------------------------------------------
// Control facade — the small surface the button UI (services/controls.h) drives.
//
// Everything here is about the LOCAL SD PLAYER only. Mode-specific branching
// (Bluetooth AVRCP vs this player) belongs in controls.h, because this header is
// compiled before mode_manager.h and cannot see `active_mode`.
// ---------------------------------------------------------------------------

// True once a track is loaded in the local player (playing or paused). False in
// Bluetooth mode, and false in WiFi mode while the portal idles with no track.
bool audio_has_track()
{
    return player != nullptr && current_track.length() > 0;
}

// True while the decoder is actually producing audio (not paused, not stopped).
bool audio_is_playing()
{
    return player != nullptr && player->isRunning();
}

uint8_t audio_volume_percent()
{
    return _volume_percent();
}

// The persisted percent from NVS (also seeds _save_volume's redundant-write guard).
uint8_t audio_load_volume_percent()
{
    return _load_volume_percent();
}

// Sets and persists the local player volume (percent, 0..100). Safe with no
// player: the value is stored and _ensure_player() picks it up on the next play.
// persist=false is for the middle of a ramp — see _set_volume_percent.
void audio_set_volume_percent(uint8_t percent, bool persist = true)
{
    _set_volume_percent(percent, persist);
}

void audio_next_track()
{
    if (playlist.empty())
    {
        Serial.println("[CTRL] next: no playlist loaded");
        return;
    }
    _play_next();
}

void audio_prev_track()
{
    if (playlist.empty())
    {
        Serial.println("[CTRL] previous: no playlist loaded");
        return;
    }
    _play_prev();
}

// Starts the current track again from the top (a re-connect, which is the only
// restart the decoder handles cleanly — setFilePos(0) leaves the stream choppy).
void audio_restart_track()
{
    if (player == nullptr || playlist.empty())
    {
        Serial.println("[CTRL] restart: no playlist loaded");
        return;
    }
    _play_index((int)playlist_index);
}

// Tears the MP3 player down completely and hands its internal RAM back.
//
// This is the only thing that releases the decoder's footprint — ~35 KB measured
// (16 x 512-frame I2S DMA buffers + the decoder state). Pausing is not enough:
// pauseResume() just stops feeding samples and keeps every buffer. Audio's
// destructor is what frees them (Audio.cpp:301-317: i2s_driver_uninstall() and
// MP3Decoder_FreeBuffers()).
//
// Needed because the radio and the decoder cannot both fit: on Echoes V1 the
// portal + WiFi leave ~44 KB, and a WiFi off/on cycle is worth ~45 KB. Bringing
// WiFi back with the player still alive left 6 KB free, at which point lwIP
// could not allocate ("No more processes") and newlib could not create a stream
// lock — abort() inside the mDNS task, then a reboot.
static void _release_audio()
{
    if (player == nullptr)
        return;

    uint32_t before_kb = ESP.getFreeHeap() / 1024;

    lightshow_stop();
    delete player; // ~Audio(): frees the I2S DMA buffers and the decoder
    player = nullptr;

    portal_paused = false;
    player_eof = false;
    current_track = "";
    playlist.clear();
    delay(50); // let the I2S/decoder teardown settle before the radio comes back

    Serial.printf("[MEM] Audio path released: %u KB -> %u KB free\n",
                  (unsigned)before_kb, (unsigned)(ESP.getFreeHeap() / 1024));
}

// ---------------------------------------------------------------------------
// Standalone playback — the only playback path that works without PSRAM
// ---------------------------------------------------------------------------

// Plays *path* with the radio off, optionally attaching a specific lightshow.
//
// POST /api/play used to call _play_path() directly, from inside the HTTP request
// handler with the radio up. That produced no audio while still answering 200;
// this is the path that replaced it, and it is the one the long-press show uses.
//
// Measured on Echoes V1 (no PSRAM), WiFi mode, portal up:
//     idle (WiFi + portal + the portal's own player)  free  45 KB, dma_largest 37 KB
//     after wifi_disable_for_show()                   free  86 KB, dma_largest 37 KB
//     after _release_audio()                          free 132 KB
//     after _ensure_player()                          free  86 KB, dma_largest 43 KB
//     playing                                         free  51 KB, running=1, rate=48000
//
// So the transition is what matters, not the hardware: with the radio up the whole
// system sits on ~45 KB and the I2S install itself succeeds (95 KB of contiguous
// DMA RAM is free that early in setup). Which allocation the old in-handler call
// actually lost was never isolated — do not re-derive it from isRunning() alone.
//
// Two changes carry the fix:
//   1. wifi_disable_for_show() hands ~41 KB back before anything is allocated.
//   2. The player is rebuilt rather than reused, so the I2S install happens against
//      the post-teardown heap. This is defensive: rebuilding also guarantees no
//      state survives from a previous attempt, and _ensure_player() keeps the
//      construction in one place.
static void _play_standalone(const String &path, const char *show_path)
{
    Serial.printf("[PLAY] Standalone: %s\n", path.c_str());
    _log_mem("wifi off: before");
    wifi_disable_for_show();
    _log_mem("wifi off: after");

    _release_audio(); // drop the radio-era player and its (probably absent) I2S driver
    _ensure_player(); // rebuild now that the heap is whole

    _play_path(path);

    if (show_path != nullptr && show_path[0] != '\0' && !is_lightshow_playing())
    {
        if (load_lightshow(show_path))
            lightshow_play();
        else
            Serial.println("[SHOW] Lightshow load failed");
    }
}

// Runs a queued POST /api/play once its HTTP reply has had time to drain.
// Called from the main loop, so the teardown never happens inside a request.
static void _service_pending_play()
{
    if (pending_play_path.length() == 0)
        return;
    if ((int32_t)(millis() - pending_play_at_ms) < 0)
        return; // still letting the reply reach the browser

    String path = pending_play_path;
    pending_play_path = "";
    _play_standalone(path, nullptr);
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

    // Give the radio back the RAM the decoder is holding. Without this the
    // re-init runs with ~6 KB free and aborts inside lwIP/newlib (see
    // _release_audio above). This stops playback — which is the intent anyway.
    if (player != nullptr)
        Serial.println("[WIFI] Stopping playback to free the audio buffers");
    _release_audio();

    // Refuse rather than abort. Measured on Echoes V1: a WiFi off/on cycle costs
    // ~45 KB, and the portal + mDNS want a few KB of slack on top of that, so
    // anything below this threshold will fail inside the radio's own allocators.
    uint32_t heap_kb = ESP.getFreeHeap() / 1024;
    if (heap_kb < WIFI_MIN_HEAP_KB)
    {
        Serial.printf("[WIFI] Only %u KB free (need ~%u KB) — not re-enabling WiFi\n",
                      (unsigned)heap_kb, (unsigned)WIFI_MIN_HEAP_KB);
        return;
    }

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
        setup_OTA();               // idempotent; re-binds port 3232 on the new netif
        Serial.printf("[MEM] Free heap after WiFi on: %u KB\n",
                      (unsigned)(ESP.getFreeHeap() / 1024));
        // The gesture that got here is a 4 s hold, and until now its only answer was a
        // serial line plus a green double-flash. _release_audio() has just handed the
        // decoder's I2S back, so the wire is free and the cue announces the result.
        cue_play_now(CUE_WIFI_OK);
    }
    else
    {
        Serial.println("[WIFI] Reconnect failed — check saved credentials");
        cue_play_now(CUE_WIFI_FAIL);
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

    // 3) Free RAM, rebuild the player against the whole heap, then play the audio
    //    with its lightshow (SD only). Same path the portal's Play button uses.
    Serial.printf("[SHOW] Standalone: %s  +  %s\n", audio.c_str(), show.c_str());
    _play_standalone(audio, show.c_str());
}

#endif // WIFI_PORTAL_H
