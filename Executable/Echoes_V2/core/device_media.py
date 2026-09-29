"""
Device media browsing for the Echoes editor.

Implements the SD card layout concept:

    /Echoes/
      Music/      — music library; sub-folders are scanned as albums/playlists,
                    e.g.  /Echoes/Music/Playlist_A/Beethoven_virus.mp3
      Lightshows/ — one folder per project, each holding at least one audio
                    file and a .echoes script to play along,
                    e.g.  /Echoes/Lightshows/Storms/song.mp3  +  storms.echoes
                    (the singular /Echoes/Lightshow is also accepted)

Everything talks to the firmware's HTTP portal API
(Firmware/echoes_of_termina/src/services/wifi_portal.h):
    GET  /api/files?path=…     → {path, parent, entries:[{name,path,isDir,size,playable}]}
    GET  /api/download?path=…  → raw file bytes
    POST /api/play  (path=…)   → start playback of a file on the device

Device paths are POSIX-style (forward slashes) — the firmware validates the
"/Echoes/…" prefix — so any backslash input is normalized before going on the
wire (see _device_path).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Optional
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from core.device_discovery import probe_device

DEFAULT_PORT = 80

DEVICE_ROOT     = "/Echoes"
MUSIC_ROOT      = "/Echoes/Music"
# Primary Lightshow root is the one the firmware portal uses; the singular
# spelling from the layout concept is accepted as a fallback for cards that
# were set up that way.
LIGHT_SHOW_ROOTS = ("/Echoes/Lightshows", "/Echoes/Lightshow")

# Formats the ESP32-audioI2S decoder can actually play (firmware _is_playable).
AUDIO_EXTS = {".mp3", ".aac", ".m4a", ".wav", ".flac", ".raw"}
SCRIPT_EXTS = {".echoes"}

MAX_SCAN_DEPTH    = 4     # folder nesting limit for the Music tree
MAX_SCAN_REQUESTS = 250   # hard cap on /api/files calls per scan

_HTTP_TIMEOUT = 2.0       # per-request timeout


def _device_path(path: str) -> str:
    """
    Normalize a device path to forward slashes.

    The firmware portal only understands POSIX-style paths (it validates the
    "/Echoes/…" prefix and splits on '/'), so any Windows-style backslashes
    must be converted before the path goes onto the wire.
    """
    return path.replace("\\", "/")


def _is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO_EXTS


def _is_script(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in SCRIPT_EXTS


def _http_get(url: str, timeout: float = _HTTP_TIMEOUT) -> Optional[bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except Exception:   # noqa: BLE001 — any network hiccup means "not found"
        return None


def fetch_dir(host: str, path: str, port: int = DEFAULT_PORT) -> Optional[dict]:
    """GET /api/files for *path*; returns the parsed JSON dict or None."""
    url = (f"http://{host}:{port}/api/files?path="
           + urllib.parse.quote(_device_path(path), safe='/'))
    raw = _http_get(url)
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and "entries" in data else None


def fetch_text(host: str, file_path: str, port: int = DEFAULT_PORT) -> Optional[str]:
    """Download a text file (e.g. a .echoes script) from the device."""
    url = (f"http://{host}:{port}/api/download?path="
           + urllib.parse.quote(_device_path(file_path), safe='/'))
    raw = _http_get(url)
    if raw is None:
        return None
    return raw.decode("utf-8", "replace")


def device_url(host: str, file_path: str, port: int = DEFAULT_PORT) -> str:
    """HTTP URL for streaming a device file (used by the media player)."""
    return (f"http://{host}:{port}/api/download?path="
            + urllib.parse.quote(_device_path(file_path), safe='/'))


def post_to_device(host: str, endpoint: str, data: dict,
                   port: int = DEFAULT_PORT):
    """POST form data to a device endpoint. Returns (ok, response_text)."""
    data = {k: (_device_path(v) if k == "path" else v)
            for k, v in data.items()}
    url  = f"http://{host}:{port}{endpoint}"
    body = urllib.parse.urlencode(data, safe='/').encode("utf-8")
    req  = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace").strip() or "OK"
            return resp.status == 200, text
    except Exception as exc:   # noqa: BLE001
        return False, str(exc)


def play_on_device(host: str, file_path: str, port: int = DEFAULT_PORT):
    """Ask the device to play *file_path*. Returns (ok, message)."""
    return post_to_device(host, "/api/play", {"path": file_path}, port=port)


def pause_device(host: str, state: Optional[str] = None,
                 port: int = DEFAULT_PORT):
    """
    Pause / resume device playback.

    *state*: "pause", "resume" or None (toggle — the firmware's default).
    Returns (ok, message).
    """
    data = {"state": state} if state else {}
    return post_to_device(host, "/api/pause", data, port=port)


def ensure_dir(host: str, path: str, port: int = DEFAULT_PORT):
    """
    Create *path* on the device (and any missing parents under /Echoes).

    The firmware's SD mkdir is single-level, so this walks the path from the
    root down, ignoring "already exists" failures. Returns (ok, message).
    """
    path = _device_path(path)
    if not path.startswith("/Echoes"):
        return False, f"Path must be under /Echoes: {path}"
    parts = path[len("/Echoes"):].strip("/").split("/")
    cur = "/Echoes"
    for part in parts:
        if not part:
            continue
        cur += "/" + part
        ok, msg = post_to_device(host, "/api/mkdir", {"path": cur}, port=port)
        if not ok and fetch_dir(host, cur, port=port) is None:
            return False, f"Could not create {cur}: {msg}"
    return True, "OK"


def delete_path(host: str, path: str, port: int = DEFAULT_PORT):
    """
    Recursively delete a file or folder on the device.

    The firmware only deletes single files (/api/delete) and empty directories
    (/api/rmdir), so a folder is emptied depth-first first. Returns (ok, msg).
    """
    path = _device_path(path)
    listing = fetch_dir(host, path, port=port)
    if listing is None:
        # Not a directory — delete it as a file
        ok, msg = post_to_device(host, "/api/delete", {"path": path}, port=port)
        if not ok:
            return False, f"Could not delete {path}: {msg}"
        return True, f"Deleted {path}"

    # Directory: remove children (files, then sub-folders), then the folder
    for entry in listing.get("entries", []):
        child = entry.get("path") or (path.rstrip("/") + "/" + entry.get("name", ""))
        if entry.get("isDir"):
            ok, msg = delete_path(host, child, port=port)
        else:
            ok, msg = post_to_device(host, "/api/delete", {"path": child}, port=port)
        if not ok:
            return False, f"Could not delete {child}: {msg}"

    ok, msg = post_to_device(host, "/api/rmdir", {"path": path}, port=port)
    if not ok:
        return False, f"Could not remove folder {path}: {msg}"
    return True, f"Deleted {path}"


def upload_file(host: str, local_path: str, dest_dir: str,
                port: int = DEFAULT_PORT):
    """
    Upload a local file into *dest_dir* on the device (multipart, field "file",
    matching the firmware portal UI). Returns (ok, message).
    """
    local_path = os.path.abspath(local_path)
    if not os.path.isfile(local_path):
        return False, f"Not a file: {local_path}"
    fname = os.path.basename(local_path)

    url = (f"http://{host}:{port}/api/upload?dir="
           + urllib.parse.quote(_device_path(dest_dir), safe='/'))
    boundary = "----EchoesBoundary" + uuid4().hex

    with open(local_path, "rb") as fh:
        content = fh.read()
    body = b"".join([
        f"--{boundary}\r\n".encode("utf-8"),
        (f'Content-Disposition: form-data; name="file"; '
         f'filename="{fname}"\r\n').encode("utf-8"),
        b"Content-Type: application/octet-stream\r\n\r\n",
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ])

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        # Large media over Wi-Fi — allow generous time
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
            if resp.status != 200:
                return False, f"Upload failed (HTTP {resp.status})"
    except Exception as exc:   # noqa: BLE001
        return False, str(exc)

    # Verify the file actually landed on the SD card: the portal replies OK
    # even when the write silently failed (e.g. missing folder, SD error).
    listing = fetch_dir(host, dest_dir, port=port)
    if listing is None:
        return False, ("Upload reported OK but the device did not confirm it — "
                       "is the SD card mounted?")
    entry = next((e for e in listing.get("entries", [])
                  if e.get("name") == fname), None)
    if entry is None:
        return False, (f"Upload reported OK but '{fname}' was not found on the "
                       "device — the write may have failed (SD full or read-only?).")
    expected = os.path.getsize(local_path)
    if entry.get("size") != expected:
        return False, (f"Size mismatch for '{fname}': device has "
                       f"{entry.get('size')} bytes, expected {expected}.")
    return True, "OK"


# ── Scanning ──────────────────────────────────────────────────────────────────

def scan_albums(host: str, port: int = DEFAULT_PORT) -> list:
    """
    Walk /Echoes/Music (including sub-folders) and return every folder that
    contains at least one playable audio file:

        [{name, path, audio: [filenames], size}]
    """
    albums: list = []
    request_budget = {"left": MAX_SCAN_REQUESTS}

    def walk(path: str, depth: int) -> None:
        if depth > MAX_SCAN_DEPTH or request_budget["left"] <= 0:
            return
        request_budget["left"] -= 1
        data = fetch_dir(host, path, port=port)
        if not data:
            return
        audio = sorted(e["name"] for e in data["entries"]
                       if not e.get("isDir") and _is_audio(e["name"]))
        if audio:
            albums.append({
                "name":  os.path.basename(path.rstrip('/')) or "Music",
                "path":  path,
                "audio": audio,
                "size":  sum(int(e.get("size", 0)) for e in data["entries"]
                             if e["name"] in audio),
            })
        for entry in data["entries"]:
            if entry.get("isDir"):
                walk(entry["path"], depth + 1)

    walk(MUSIC_ROOT, 0)
    albums.sort(key=lambda a: a["name"].lower())
    return albums


def scan_projects(host: str, port: int = DEFAULT_PORT):
    """
    Scan the Lightshow root(s). Every sub-folder holding at least one playable
    audio file AND at least one .echoes script is a valid project.

    Returns (projects, used_root):
        projects = [{name, path, audio: [filenames], script: [filenames]}]
        used_root = the Lightshow root that exists on the device, or None
    """
    for root in LIGHT_SHOW_ROOTS:
        data = fetch_dir(host, root, port=port)
        if data is None:
            continue   # this spelling does not exist on the device
        projects: list = []
        for entry in data.get("entries", []):
            if not entry.get("isDir"):
                continue
            pdata = fetch_dir(host, entry["path"], port=port)
            if not pdata:
                continue
            audio   = sorted(e["name"] for e in pdata["entries"]
                             if not e.get("isDir") and _is_audio(e["name"]))
            scripts = sorted(e["name"] for e in pdata["entries"]
                             if not e.get("isDir") and _is_script(e["name"]))
            if audio and scripts:
                projects.append({
                    "name":   entry["name"],
                    "path":   entry["path"],
                    "audio":  audio,
                    "script": scripts,
                })
        projects.sort(key=lambda p: p["name"].lower())
        return projects, root
    return [], None


def scan_device(host: str, port: int = DEFAULT_PORT) -> dict:
    """Scan Music + Lightshow in one pass. Never raises."""
    # Probe the device root first: a missing /Echoes means the device did not
    # answer (SD not mounted / powered off), not merely "no media yet".
    if fetch_dir(host, DEVICE_ROOT, port=port) is None:
        return {"albums": [], "projects": [], "lightshow_root": None,
                "responsive": False}
    albums, projects, lightshow_root = (scan_albums(host, port=port),
                                        *scan_projects(host, port=port))
    return {"albums": albums, "projects": projects,
            "lightshow_root": lightshow_root, "responsive": True}


# ── Background scanner ────────────────────────────────────────────────────────

class DeviceMediaScanner(QThread):
    """Scans the connected device's Music/Lightshow folders off the GUI thread."""

    finished_ok = Signal(object)   # scan result dict
    failed      = Signal(str)

    def __init__(self, host: str, port: int = DEFAULT_PORT, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._port = port

    def run(self) -> None:
        result = scan_device(self._host, port=self._port)
        if not result.get("responsive"):
            self.failed.emit(
                "Device did not answer (is the SD card mounted and powered?)")
            return
        self.finished_ok.emit(result)


class DeviceStatusPoller(QThread):
    """
    Polls GET /api/status every second (while the device is paired) so the
    transport controls can show live playback progress. Emits the status dict
    (with pos/dur from the firmware) or None when the device stops answering.
    """

    status_changed = Signal(object)   # dict | None

    def __init__(self, host: str, interval_ms: int = 1000,
                 port: int = DEFAULT_PORT, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._interval_ms = interval_ms
        self._port = port
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True
        self.requestInterruption()

    def run(self) -> None:
        while not self._stopping:
            self.status_changed.emit(
                probe_device(self._host, port=self._port))
            remaining = self._interval_ms
            while remaining > 0 and not self._stopping:
                step = min(50, remaining)
                self.msleep(step)
                remaining -= step


class ProjectUploadWorker(QThread):
    """
    Uploads a list of local files into a device folder off the GUI thread.

    Emits `progress(message)`, then `succeeded(summary)` or `failed(message)`.
    """

    progress  = Signal(str)
    succeeded = Signal(str)
    failed    = Signal(str)

    def __init__(self, host: str, dest_dir: str, files: list,
                 port: int = DEFAULT_PORT, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._dest_dir = dest_dir
        self._files = [f for f in files if os.path.isfile(f)]
        self._port = port
        self._cancel = False

    def cancel(self) -> None:
        """Request cancellation (takes effect between files)."""
        self._cancel = True

    def run(self) -> None:
        total = len(self._files)
        if total == 0:
            self.failed.emit("No files to upload.")
            return
        for i, local in enumerate(self._files, 1):
            if self._cancel:
                self.failed.emit("Upload cancelled.")
                return
            self.progress.emit(
                f"Uploading {os.path.basename(local)} ({i}/{total}) \u2026")
            ok, msg = upload_file(self._host, local, self._dest_dir,
                                  port=self._port)
            if not ok:
                self.failed.emit(
                    f"Failed to upload {os.path.basename(local)}: {msg}")
                return
        self.succeeded.emit(
            f"Uploaded {total} file{'s' if total != 1 else ''} "
            f"to {self._dest_dir}")
