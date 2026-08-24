"""
Echoes — main application window.

Layout
──────
┌──────────────────────────────────────────────────────────────┐
│  info bar  (loaded file name)                                │
├──────────────────────┬───────────────────────────────────────┤
│                      │  LED canvas  (mask / future overlay)  │
│   Script editor      ├───────────────────────────────────────┤
│   (text commands)    │  Transport bar  (stop · play · seek)  │
└──────────────────────┴───────────────────────────────────────┘

The MainWindow owns the AudioPlayer and ScriptEngine and wires them together:
  audio.positionChanged → engine.evaluate_at → canvas + editor highlight
  editor.script_changed → engine.parse       → error markers
  transport.seek_requested                   → engine re-evaluation
"""
from __future__ import annotations

import datetime
import os
import re
from typing import Optional

from PySide6.QtCore    import QObject, QSettings, Qt, QSize
from PySide6.QtGui     import QAction, QCursor, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QPushButton, QSizePolicy, QSplitter,
    QToolBar, QVBoxLayout, QWidget,
)

from core.audio_converter import (
    Mp3ConvertWorker, ffmpeg_available, find_audio_files,
)
from core.audio_player  import AudioPlayer
from core.device_discovery import DEFAULT_HOSTNAME, DeviceMonitor, probe_device
from core.device_media import (
    DeviceStatusPoller, ProjectUploadWorker, device_url, ensure_dir, fetch_dir,
    fetch_text, pause_device, play_on_device,
)
from core.script_engine import ScriptEngine, format_ms, remap_hw_version, _parse_timestamp

# Default project folder (tools/echoes/projects/)
_PROJECTS = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'projects'))
if not os.path.isdir(_PROJECTS):
    os.makedirs(_PROJECTS, exist_ok=True)

# ── Chart-import audio-search helpers ────────────────────────────────────────

# Stream metadata keys to probe in order of preference
_AUDIO_STREAM_KEYS = [
    'MusicStream', 'GuitarStream', 'RhythmStream', 'BassStream',
    'KeysStream', 'DrumStream', 'VocalStream',
]
# Reserved stem names used by Clone Hero when explicit tags are absent
_AUDIO_STEMS = ['song', 'guitar', 'bass', 'rhythm', 'keys', 'drums', 'vocals', 'crowd']
_AUDIO_EXTS  = ['.ogg', '.opus', '.mp3', '.wav', '.flac']


def _find_chart_audio(chart_dir: str, song_meta: dict) -> Optional[str]:
    """
    Return the first existing audio filename in *chart_dir*.
    Search order:
      1. Explicit stream metadata tags (MusicStream, GuitarStream, …)
      2. Reserved Clone Hero stem names (song.ogg, guitar.ogg, …)
    Returns the bare filename, or None if nothing found.
    """
    # 1 – metadata tags
    for key in _AUDIO_STREAM_KEYS:
        fname = song_meta.get(key, '').strip().strip('"')
        if fname and os.path.isfile(os.path.join(chart_dir, fname)):
            return fname
    # 2 – reserved filenames
    for stem in _AUDIO_STEMS:
        for ext in _AUDIO_EXTS:
            fname = stem + ext
            if os.path.isfile(os.path.join(chart_dir, fname)):
                return fname
    return None


def _parse_song_ini(path: str) -> dict:
    """
    Parse a song.ini file and return a flat {key: value} dict.
    Strips leading/trailing quotes and whitespace from values.
    Per spec, song.ini metadata should take priority over [Song] block data.
    """
    meta: dict = {}
    try:
        with open(path, encoding='utf-8-sig', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('[') or line.startswith(';') or line.startswith('#'):
                    continue
                if '=' in line:
                    key, _, val = line.partition('=')
                    meta[key.strip()] = val.strip().strip('"')
    except OSError:
        pass
    return meta


from ui.command_toolbox  import CommandToolbox
from ui.device_browser_dialog import DeviceBrowserDialog
from ui.led_canvas       import LEDCanvas
from ui.metadata_dialog  import MetadataDialog
from ui.note_highway     import NoteHighway
from ui.script_editor    import MultiTrackEditorWidget, ScriptEditor
from ui.transport_bar    import TransportBar


# ── Upload worker lifetime ────────────────────────────────────────────────────
# A project upload may outlive the window (Wi-Fi transfer). Keep a strong
# reference so the QThread is never destroyed while running, and reap it on
# the main thread once it finishes.

_upload_workers: list = []


class _UploadReaper(QObject):
    """Lives in the main thread; safely deleteLater()-s finished uploaders."""

    def reap(self, worker=None) -> None:
        worker = worker if worker is not None else self.sender()
        if worker in _upload_workers:
            _upload_workers.remove(worker)
        worker.deleteLater()


_upload_reaper = _UploadReaper()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Echoes")
        # Default size that fits the screen: 1440x780 on large displays,
        # smaller (but never below the minimum) on compact laptops.
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        avail  = screen.availableGeometry() if screen else None
        width  = min(1440, avail.width() - 80)  if avail else 1440
        height = min(780,  avail.height() - 80) if avail else 780
        self.resize(max(900, width), max(560, height))
        self.setMinimumSize(900, 560)

        # Restore the user's window size / position when available
        saved = QSettings().value("window/geometry")
        if saved:
            self.restoreGeometry(saved)

        self._audio  = AudioPlayer(self)
        self._engines: dict[str, ScriptEngine] = {}   # one engine per track slot
        self._script_path: Optional[str] = None
        self._last_audio_dir:  str = _PROJECTS
        self._last_script_dir: str = _PROJECTS
        self._meta: dict = {}   # metadata parsed from / written to the .echoes header

        self._mp3_actions: list[QAction] = []   # toolbar + menu actions (disabled while converting)
        self._mp3_worker: Optional[Mp3ConvertWorker] = None
        self._audio_files: list = []            # multi-track stems (session state)

        # Device (hardware) discovery state
        self._device_monitor: Optional[DeviceMonitor] = None
        self._device_paired  = False
        self._device_online  = False
        self._device_info: Optional[dict] = None
        self._device_browser: Optional["DeviceBrowserDialog"] = None
        self._device_status: Optional[dict] = None       # latest /api/status
        self._status_poller: Optional[DeviceStatusPoller] = None
        self._upload_worker: Optional[ProjectUploadWorker] = None
        self._upload_dialog = None
        self._device_project_folder: Optional[str] = None

        self._build_central()
        self._build_toolbar()
        self._build_device_toolbar()
        self._build_menu()
        self._wire()
        self._set_track_display("No file loaded")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_central(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # ── Horizontal splitter ───────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)

        # Left — script editor (multi-track)
        self._editor = MultiTrackEditorWidget()
        self._editor.setPlaceholderText(
            "# Echoes lightshow script\n"
            "#\n"
            "# SET   M:SS  SET   R  G  B  LED-SPEC\n"
            "# RAMP  M:SS  RAMP  DURATION  R0 G0 B0  R1 G1 B1  LED-SPEC\n"
            "# FADE  M:SS  FADE  R  G  B  PERCENT(1-99)  LED-SPEC\n"
            "#\n"
            "# LED-SPEC:  5   1-2   1,3,5   *\n"
            "#\n"
            "# ── Examples ──────────────────────────────────────────\n"
            "#\n"
            "# 0:05 SET 255 0 0 1-2          # eyes solid red\n"
            "#\n"
            "# 0:10 RAMP 0:03 0 0 255 255 80 0 *   # all zones: blue → orange over 3 s\n"
            "#\n"
            "# 0:05 FADE 100 0 0 50 1-2      # eyes red, 50 %/s decay (~7 s to black)\n"
            "# 0:05 FADE 100 0 0 75 1-2      # eyes red, 75 %/s decay (~3 s to black)\n"
            "# 0:05 FADE 100 0 0 90 1-2      # eyes red, 90 %/s decay (~1 s to black)\n"
        )
        splitter.addWidget(self._editor)

        # Right — [highway | canvas] + toolbox + transport
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        # Horizontal splitter: note highway on the left, LED canvas on the right
        hsplit = QSplitter(Qt.Orientation.Horizontal)
        hsplit.setHandleWidth(2)

        self._highway = NoteHighway()
        hsplit.addWidget(self._highway)

        self._canvas = LEDCanvas()

        # Canvas settings strip — BG / Labels / HW V2 live on the mask area
        # itself, so they are always visible (never hidden in a menu).
        canvas_wrap = QWidget()
        cw = QVBoxLayout(canvas_wrap)
        cw.setContentsMargins(0, 0, 0, 0)
        cw.setSpacing(0)

        strip = QWidget()
        strip.setFixedHeight(26)
        strip.setStyleSheet(
            "background:#2a2a2a; border-bottom:1px solid #333333;")
        sh = QHBoxLayout(strip)
        sh.setContentsMargins(8, 0, 8, 0)
        sh.setSpacing(12)

        _check_style = "color:#aaaaaa; font:11px 'Segoe UI';"
        self._bg_check = QCheckBox("BG", strip)
        self._bg_check.setChecked(True)
        self._bg_check.setToolTip("Show / hide the background image")
        self._bg_check.setStyleSheet(_check_style)
        self._bg_check.toggled.connect(self._canvas.set_bg_visible)

        self._lbl_check = QCheckBox("Labels", strip)
        self._lbl_check.setChecked(True)
        self._lbl_check.setToolTip("Show / hide zone number labels")
        self._lbl_check.setStyleSheet(_check_style)
        self._lbl_check.toggled.connect(self._canvas.set_labels_visible)

        self._v2_check = QCheckBox("HW V2", strip)
        self._v2_check.setChecked(False)
        self._v2_check.setToolTip(
            "V1: eyes at LEDs 27-28 (default)\n"
            "V2: eyes at LEDs 1-2")
        self._v2_check.setStyleSheet(_check_style)
        self._v2_check.toggled.connect(self._canvas.set_hw_version)

        sh.addWidget(self._bg_check)
        sh.addWidget(self._lbl_check)
        sh.addWidget(self._v2_check)
        sh.addStretch(1)

        cw.addWidget(strip)
        cw.addWidget(self._canvas, stretch=1)
        hsplit.addWidget(canvas_wrap)

        hsplit.setSizes([340, 540])
        rv.addWidget(hsplit, stretch=1)

        div_cmd = QWidget()
        div_cmd.setFixedHeight(1)
        div_cmd.setStyleSheet("background:#333333;")
        rv.addWidget(div_cmd)

        self._toolbox = CommandToolbox(self._audio)
        rv.addWidget(self._toolbox, stretch=0)

        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet("background:#333333;")
        rv.addWidget(div)

        self._transport = TransportBar(self._audio)
        rv.addWidget(self._transport, stretch=0)

        splitter.addWidget(right)
        splitter.setSizes([400, 1000])
        vbox.addWidget(splitter, stretch=1)

        self.statusBar().showMessage(
            "Ready  —  File › Open Audio  to load a track, "
            "File › Open Project  to load or create a show")

    def _build_toolbar(self) -> None:
        tb = QToolBar("Quick Access", self)
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(14, 14))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        style = self.style()
        SP    = self.style().StandardPixmap

        act_mp3 = QAction(
            style.standardIcon(SP.SP_MediaVolume), "  Open Audio", self)
        act_mp3.setShortcut("Ctrl+O")
        act_mp3.setToolTip("Load an audio file  (Ctrl+O)")
        act_mp3.triggered.connect(self._open_mp3)
        tb.addAction(act_mp3)

        tb.addSeparator()

        act_import = QAction(
            style.standardIcon(SP.SP_FileDialogStart), "  Open Project", self)
        act_import.setShortcut("Ctrl+Shift+O")
        act_import.setToolTip("Open an existing .echoes project  (Ctrl+Shift+O)")
        act_import.triggered.connect(self._open_script)
        tb.addAction(act_import)

        act_export = QAction(
            style.standardIcon(SP.SP_DialogSaveButton), "  Save Script", self)
        act_export.setShortcut("Ctrl+S")
        act_export.setToolTip("Save script to file  (Ctrl+S)")
        act_export.triggered.connect(self._save_script)
        tb.addAction(act_export)

        act_tracks = QAction(
            style.standardIcon(SP.SP_FileDialogDetailedView), "  Import Tracks", self)
        act_tracks.setShortcut("Ctrl+Shift+I")
        act_tracks.setToolTip("Import tracks from a .chart or .mid file  (Ctrl+Shift+I)")
        act_tracks.triggered.connect(self._import_tracks)
        tb.addAction(act_tracks)

        act_mp3_convert = QAction(
            style.standardIcon(SP.SP_MediaSeekForward), "  Convert to .MP3", self)
        act_mp3_convert.setToolTip(
            "Convert the loaded audio to .MP3 next to the source file\n"
            "so the firmware's SD card player can use it (needs ffmpeg)")
        act_mp3_convert.triggered.connect(self._convert_to_mp3)
        tb.addAction(act_mp3_convert)
        self._mp3_actions.append(act_mp3_convert)

        # Expanding spacer so the right-side cluster stays pinned to the right
        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # ── Device status: hardware discovery / pairing ─────────────────────
        tb.addSeparator()

        self._device_dot = QLabel()
        self._device_dot.setFixedSize(12, 12)
        self._device_dot.setStyleSheet("background:#d33; border-radius:6px;")
        self._device_dot.setToolTip("Device not found")
        tb.addWidget(self._device_dot)

        self._device_label = QLabel(f"{DEFAULT_HOSTNAME} \u2014 not found")
        self._device_label.setStyleSheet(
            "color:#888888; font:11px 'Segoe UI'; padding:0 4px;")
        tb.addWidget(self._device_label)

        self._device_btn = QPushButton("Connect")
        self._device_btn.setEnabled(False)
        self._device_btn.setToolTip(
            "Find the Echoes hardware on the network and pair with it")
        self._device_btn.clicked.connect(self._toggle_device_pair)
        tb.addWidget(self._device_btn)

        self._device_media_btn = QPushButton("Device Media")
        self._device_media_btn.setEnabled(False)
        self._device_media_btn.setToolTip(
            "Browse the music albums and lightshow projects stored on the device")
        self._device_media_btn.clicked.connect(self._open_device_browser)
        tb.addWidget(self._device_media_btn)

        self._info_bar = QLabel("No file loaded")
        self._info_bar.setStyleSheet(
            "color:#888888; font:11px 'Segoe UI'; padding-right:8px;")
        tb.addWidget(self._info_bar)

    def _build_device_toolbar(self) -> None:
        """Second toolbar: live playback control for the paired hardware."""
        # The main toolbar is very wide; give the device toolbar its own row
        # so its widgets are never clipped off the right edge.
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)

        tb = QToolBar("Device", self)
        tb.setMovable(False)
        tb.setFloatable(False)
        self._device_toolbar = tb
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        style = self.style()
        SP    = style.StandardPixmap

        self._device_play_btn = QAction(
            style.standardIcon(SP.SP_MediaPlay), "  Play", self)
        self._device_play_btn.setToolTip(
            "Resume / play the current track on the device")
        self._device_play_btn.setEnabled(False)
        self._device_play_btn.triggered.connect(self._device_play)
        tb.addAction(self._device_play_btn)

        self._device_pause_btn = QAction(
            style.standardIcon(SP.SP_MediaPause), "  Pause", self)
        self._device_pause_btn.setToolTip("Pause playback on the device")
        self._device_pause_btn.setEnabled(False)
        self._device_pause_btn.triggered.connect(self._device_pause)
        tb.addAction(self._device_pause_btn)

        self._device_progress = QProgressBar()
        self._device_progress.setFixedWidth(170)
        self._device_progress.setTextVisible(True)
        self._device_progress.setFormat("%v / %m s")
        self._device_progress.setRange(0, 0)
        self._device_progress.setToolTip(
            "Playback position on the device (updates every second)")
        tb.addWidget(self._device_progress)

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        self._device_upload_btn = QPushButton("Upload Project")
        self._device_upload_btn.setToolTip(
            "Upload this project's .mp3 and .echoes to\n"
            "/Echoes/Lightshows/<project name> on the device")
        self._device_upload_btn.clicked.connect(self._device_upload_project)
        tb.addWidget(self._device_upload_btn)

        tb.setVisible(False)

    def _build_menu(self) -> None:
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        self._act(fm, "&New Project",              "Ctrl+N",        self._new_project)
        fm.addSeparator()
        self._act(fm, "&Open Audio\u2026",         "Ctrl+O",        self._open_mp3)
        self._act(fm, "Load Audio &Tracks\u2026", "Ctrl+Alt+O",    self._open_audio_tracks)
        self._mp3_actions.append(self._act(
            fm, "Convert Audio to .&MP3\u2026", "", self._convert_to_mp3))
        self._act(fm, "Upload to &Device\u2026", "", self._device_upload_project)
        fm.addSeparator()
        self._act(fm, "Open &Project\u2026",      "Ctrl+Shift+O",  self._open_script)
        self._act(fm, "&Save Script",             "Ctrl+S",        self._save_script)
        self._act(fm, "Save Script &As\u2026",    "Ctrl+Shift+S",  self._save_script_as)
        self._act(fm, "Import &Tracks\u2026",     "Ctrl+Shift+I",  self._import_tracks)
        fm.addSeparator()
        self._act(fm, "Script &Properties\u2026", "Ctrl+I",        self._show_properties)
        fm.addSeparator()
        self._act(fm, "&Quit",                    "Ctrl+Q",        self.close)

        # ── Tools: less-frequent utilities live here, not the toolbar ───────
        tm = mb.addMenu("&Tools")
        self._act(tm, "&Convert Hardware Version\u2026", "", self._convert_hw_version)

    def _act(self, menu, label: str, shortcut: str, slot) -> QAction:
        a = QAction(label, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    # ── Signal wiring ─────────────────────────────────────────────────────────

    def _wire(self) -> None:
        self._audio.position_changed.connect(self._on_position)
        self._audio.duration_changed.connect(self._on_duration)
        self._audio.error_occurred.connect(self._on_audio_error)
        self._editor.active_script_changed.connect(self._on_script_changed)
        self._editor.background_script_changed.connect(self._on_background_script_changed)
        self._transport.seek_requested.connect(self._on_seek)
        self._canvas.zone_right_clicked.connect(self._on_zone_right_clicked)
        self._toolbox.command_ready.connect(self._on_command_ready)
        self._editor.tracks_changed.connect(self._toolbox.set_track_names)
        # Keep note highway in sync with BPM changes
        self._transport._bpm.valueChanged.connect(
            lambda bpm: self._highway.set_beat_ms(round(60_000 / bpm)))
        self._highway.set_beat_ms(round(60_000 / self._transport._bpm.value()))

        # ── Hardware discovery (background poll of echoes.local) ─────────────
        self._device_monitor = DeviceMonitor(DEFAULT_HOSTNAME, parent=self)
        self._device_monitor.state_changed.connect(self._on_device_state)
        self._device_monitor.start()

    # ── Device discovery / pairing ────────────────────────────────────────────

    def _on_device_state(self, info: Optional[dict]) -> None:
        """Monitor tick: *info* is the discovery result, or None when offline."""
        self._device_online = info is not None
        self._device_info   = info
        self._update_device_ui()

    def _toggle_device_pair(self) -> None:
        """Connect ⇄ Disconnect button — pair with / unpair from the device."""
        if self._device_paired:
            self._device_paired = False
            self.statusBar().showMessage("Device disconnected")
        else:
            if self._device_info is None:
                QMessageBox.warning(
                    self, "Device not found",
                    f"No Echoes hardware found on the network ({DEFAULT_HOSTNAME}).\n"
                    "Make sure the device is powered on and on the same Wi-Fi network.",
                )
                return
            # Live re-verify so a stale monitor result can't fake a pairing
            host = self._device_info.get("host") or self._device_info.get("ip")
            if probe_device(host) is None:
                QMessageBox.warning(
                    self, "Device offline",
                    f"{DEFAULT_HOSTNAME} stopped responding.\n"
                    "Check the device power and network, then try again.",
                )
                return
            self._device_paired = True
            self.statusBar().showMessage(
                f"Paired with Echoes hardware at "
                f"{self._device_info.get('ip', '')}  \u2014  "
                f"SD: {'yes' if self._device_info['status'].get('sd') else 'no'}")
        self._update_device_ui()
        if self._device_paired:
            self._open_device_browser()

    def _device_host(self) -> str:
        """Best host string for device HTTP calls (IP preferred over mDNS)."""
        if self._device_info is not None:
            return (self._device_info.get("ip")
                    or self._device_info.get("host") or DEFAULT_HOSTNAME)
        return DEFAULT_HOSTNAME

    def _open_device_browser(self) -> None:
        """Open (or refresh) the device media browser dialog."""
        if not self._device_paired:
            QMessageBox.information(
                self, "Not connected",
                "Pair with the device first (Connect button), then browse its media.")
            return
        if self._device_browser is None:
            self._device_browser = DeviceBrowserDialog(self._device_host(), self)
            self._device_browser.load_project_requested.connect(
                self._device_load_project)
            self._device_browser.play_requested.connect(self._device_play_path)
            self._device_browser.destroyed.connect(
                lambda: setattr(self, "_device_browser", None))
        else:
            self._device_browser.set_host(self._device_host())
            self._device_browser.refresh()
        self._device_browser.show()
        self._device_browser.raise_()
        self._device_browser.activateWindow()

    def _device_play_path(self, file_path: str) -> None:
        """Play a stored audio file on the device (browser Play action). A
        Lightshows project also starts its synced .echoes lightshow."""
        ok, msg = play_on_device(self._device_host(), file_path)
        if ok:
            if "/Echoes/Lightshows/" in file_path.replace("\\", "/"):
                msg = f"{msg}  \u2014  lightshow attached"
            self.statusBar().showMessage(f"Device play: {msg}")
        else:
            self.statusBar().showMessage(f"Device play failed: {msg}")
        if ok:
            self._set_track_display(
                f"  \u25b6  {os.path.basename(file_path)}  (device)")

    def _device_load_project(self, project: dict) -> None:
        """Load a stored project (echoes script + audio) from the device."""
        host = self._device_host()
        # Device paths always use forward slashes
        script_name = project["script"][0]
        script_path = project["path"] + "/" + script_name
        text = fetch_text(host, script_path)
        if text is None:
            QMessageBox.warning(
                self, "Load failed",
                f"Could not download {script_path} from the device.")
            return
        self._apply_script_text(text, title=f"{project['name']}  (device)")
        self.statusBar().showMessage(
            f"Loaded project \u201c{project['name']}\u201d from device \u2014 "
            f"audio: {project['audio'][0]}")

        audio_path = project["path"] + "/" + project["audio"][0]
        self._audio.load_url(device_url(host, audio_path))
        self._set_track_display(
            f"  \u266b  {project['audio'][0]}  \u2014  {host}")

    def _update_device_ui(self) -> None:
        """Refresh the dot, label and Connect button from current state."""
        info   = self._device_info
        online = self._device_online
        paired = self._device_paired

        if info is not None:
            st = info.get("status") or {}
            via = {"mDNS": "mDNS (echoes.local)",
                   "ip-cache": "cached IP",
                   "subnet": "subnet scan"}.get(info.get("via"), info.get("via", ""))
            heap = st.get('heap')
            heap_txt = f"{heap // 1024} KB" if isinstance(heap, (int, float)) else '—'
            self._device_label.setText(f"{DEFAULT_HOSTNAME}  \u2022  {info.get('ip', '')}")
            self._device_label.setToolTip(
                f"Found via {via}\n"
                f"SD card: {'present' if st.get('sd') else 'missing'}\n"
                f"Playing: {'yes' if st.get('playing') else 'no'}\n"
                f"Lightshow: {'attached' if st.get('show') else 'off'}\n"
                f"Free heap: {heap_txt}\n"
                f"Track:   {st.get('track') or '—'}\n"
                f"Volume:  {st.get('volume', '—')}")
        else:
            self._device_label.setText(f"{DEFAULT_HOSTNAME} \u2014 not found")
            self._device_label.setToolTip(
                "Device not reachable on the network\n"
                "(powers on, same Wi-Fi network, mDNS/subnet scan active)")

        if paired:
            self._device_btn.setText("Disconnect")
            self._device_btn.setEnabled(True)
            color   = "#4caf50" if online else "#d33"
            dot_tip = "Paired \u2014 device online" if online else "Paired \u2014 device offline"
        else:
            self._device_btn.setText("Connect")
            self._device_btn.setEnabled(online)
            color   = "#d33"
            dot_tip = ("Device found \u2014 click Connect to pair"
                       if online else "Device not found")

        self._device_dot.setStyleSheet(f"background:{color}; border-radius:6px;")
        self._device_dot.setToolTip(dot_tip)
        self._device_media_btn.setEnabled(paired)

        # ── Device transport toolbar + status poller ─────────────────────────
        self._device_toolbar.setVisible(paired)
        if paired and self._status_poller is None:
            self._status_poller = DeviceStatusPoller(
                self._device_host(), parent=self)
            self._status_poller.status_changed.connect(self._on_device_status)
            self._status_poller.start()
        elif not paired and self._status_poller is not None:
            self._stop_status_poller()
        self._update_device_buttons()

    # ── Device transport control ──────────────────────────────────────────────

    def _stop_status_poller(self) -> None:
        poller = self._status_poller
        self._status_poller = None
        self._device_status = None
        if poller is not None:
            poller.stop()
            poller.wait(4000)          # probe timeout is 2 s — give it room
            poller.deleteLater()

    def _on_device_status(self, status: Optional[dict]) -> None:
        """1s poll tick from the device — refresh transport controls."""
        self._device_status = status
        self._update_device_buttons()

    def _update_device_buttons(self) -> None:
        st = self._device_status or {}
        playing = bool(st.get("playing"))
        track   = (st.get("track") or "").strip()
        dur = int(st.get("dur") or 0)
        pos = int(st.get("pos") or 0)

        self._device_pause_btn.setEnabled(playing)
        self._device_play_btn.setEnabled(
            not playing and
            (bool(track) or self._device_project_audio_path() is not None))
        if dur > 0:
            self._device_progress.setRange(0, dur)
            self._device_progress.setValue(max(0, min(pos, dur)))
        else:
            self._device_progress.setRange(0, 0)
            self._device_progress.setValue(0)

    def _device_project_audio_path(self) -> Optional[str]:
        """Device path of this project's audio (Lightshows/<proj>/<audio>), if known."""
        if not self._script_path:
            return None
        audio = self._meta.get('audio', '').strip()
        if not audio:
            src = self._audio.source_path
            if src and os.path.isfile(src):
                audio = os.path.basename(src)
            else:
                return None
        folder = self._device_project_folder
        if not folder:
            stem = os.path.splitext(os.path.basename(self._script_path))[0]
            folder = f"/Echoes/Lightshows/{stem}"
        return f"{folder}/{audio}"

    def _device_play(self) -> None:
        """Resume the current track, or start the project's audio on the device."""
        host = self._device_host()
        st = self._device_status or {}
        if st.get("playing"):
            return
        track = (st.get("track") or "").strip()
        if track:
            ok, msg = pause_device(host, "resume")
            if not ok:
                self.statusBar().showMessage(f"Device resume failed: {msg}")
                return
            # resume may not restart a finished song — fall back to replaying it
            st2 = probe_device(host)
            if st2 and not st2.get("playing") and (st2.get("track") or "").strip():
                play_on_device(host, st2["track"])
                self.statusBar().showMessage(f"Playing: {st2['track']}")
            else:
                self.statusBar().showMessage("Device playback resumed")
            return
        path = self._device_project_audio_path()
        if path:
            ok, msg = play_on_device(host, path)
            self.statusBar().showMessage(
                f"Device play: {msg}" if ok else f"Device play failed: {msg}")
            return
        self.statusBar().showMessage(
            "No track on the device \u2014 upload a project first")

    def _device_pause(self) -> None:
        ok, msg = pause_device(self._device_host(), "pause")
        self.statusBar().showMessage(
            "Device paused" if ok else f"Device pause failed: {msg}")

    # ── Upload project to the device ─────────────────────────────────────────

    def _ask_upload_folder_name(self, host: str, default: str) -> Optional[str]:
        """Ask for the device folder name, autocompleted from existing folders."""
        names: list = []
        data = fetch_dir(host, "/Echoes/Lightshows")
        if data:
            names = sorted(e["name"] for e in data.get("entries", [])
                           if e.get("isDir"))

        from PySide6.QtWidgets import (
            QCompleter, QDialog, QDialogButtonBox, QLineEdit,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Upload Project to Device")
        dlg.setMinimumWidth(420)
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(8)
        vbox.addWidget(QLabel(
            "Target folder on the device  (/Echoes/Lightshows/<name>):"))
        edit = QLineEdit(default)
        if names:
            edit.setCompleter(QCompleter(names, dlg))
            edit.setToolTip("Autocomplete from folders already on the device")
        vbox.addWidget(edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        vbox.addWidget(buttons)

        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            name = edit.text().strip()
            if not name:
                QMessageBox.warning(self, "Missing name", "Enter a folder name.")
                continue
            if "/" in name or "\\" in name or name in (".", ".."):
                QMessageBox.warning(
                    self, "Invalid name",
                    "The folder name must not contain '/' or '\\'.")
                continue
            return name

    def _device_upload_project(self) -> None:
        """
        Upload this project (.mp3 + .echoes) to /Echoes/Lightshows/<name>,
        asking for the folder name (autocompleted) and overwrite permission.
        """
        try:
            self._device_upload_project_impl()
        except Exception as exc:   # noqa: BLE001 — never fail silently
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, "Upload error",
                f"Unexpected error while preparing the upload:\n\n{exc}")

    def _device_upload_project_impl(self) -> None:
        if not self._device_paired:
            QMessageBox.information(
                self, "Not connected", "Pair with the device first.")
            return
        if self._upload_worker is not None:
            return
        if not self._script_path:
            QMessageBox.information(
                self, "Save first",
                "Save the project before uploading it to the device.")
            return
        audio = self._audio.source_path
        if not audio or not os.path.isfile(audio):
            QMessageBox.warning(
                self, "No audio file",
                "The project has no local audio file to upload.\n\n"
                "Use \u201cConvert to .MP3\u201d, Open Audio or "
                "Load Audio Tracks first.")
            return

        host = self._device_host()

        # Default folder name: script stem (or its parent folder for generic
        # names like script.echoes)
        default = os.path.splitext(os.path.basename(self._script_path))[0]
        parent  = os.path.basename(
            os.path.dirname(os.path.abspath(self._script_path)))
        if default.lower() in ("script", "show", "main") and parent \
                and parent.lower() not in ("projects", "echoes", "tools"):
            default = parent

        name = self._ask_upload_folder_name(host, default)
        if not name:
            return
        dest = f"/Echoes/Lightshows/{name}"

        # Overwrite confirmation when the folder already exists
        if fetch_dir(host, dest) is not None:
            answer = QMessageBox.question(
                self, "Folder exists",
                f"'{name}' already exists on the device.\n"
                "Overwrite its contents?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        ok, msg = ensure_dir(host, dest)
        if not ok:
            QMessageBox.warning(self, "Cannot create folder", msg)
            return

        # Fast reachability check so a dead device fails immediately instead
        # of hanging the upload for minutes.
        if probe_device(host) is None:
            QMessageBox.warning(
                self, "Device not reachable",
                f"{host} stopped responding.\n\n"
                "Check the device power and that it is still on the same "
                "Wi-Fi network, then try again.")
            return

        self._device_project_folder = dest
        self._device_upload_btn.setEnabled(False)
        worker = ProjectUploadWorker(host, dest, [audio, self._script_path])
        worker.progress.connect(self._on_upload_progress)
        worker.succeeded.connect(self._on_upload_succeeded)
        worker.failed.connect(self._on_upload_failed)
        worker.finished.connect(self._on_upload_finished)
        worker.finished.connect(_upload_reaper.reap)
        self._upload_worker = worker
        _upload_workers.append(worker)

        # ── Loading screen ────────────────────────────────────────────────────
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Preparing upload \u2026", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Uploading to Device")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(worker.cancel)
        dlg.show()
        self._upload_dialog = dlg

        worker.start()

    def _on_upload_progress(self, message: str) -> None:
        self.statusBar().showMessage(message)
        if self._upload_dialog is not None:
            self._upload_dialog.setLabelText(message)

    def _on_upload_succeeded(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._close_upload_dialog()
        QMessageBox.information(self, "Upload complete", message)
        if self._device_browser is not None and self._device_browser.isVisible():
            self._device_browser.refresh()

    def _on_upload_failed(self, message: str) -> None:
        self.statusBar().showMessage("Upload failed")
        self._close_upload_dialog()
        QMessageBox.warning(self, "Upload failed", message)

    def _on_upload_finished(self) -> None:
        self._upload_worker = None
        self._device_upload_btn.setEnabled(True)
        self._close_upload_dialog()

    def _close_upload_dialog(self) -> None:
        dlg = self._upload_dialog
        self._upload_dialog = None
        if dlg is not None:
            dlg.close()
            dlg.deleteLater()

    # ── Window lifecycle ──────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """Persist the window geometry, then stop background threads."""
        QSettings().setValue("window/geometry", self.saveGeometry())
        if self._device_browser is not None:
            self._device_browser.close()
            self._device_browser = None
        self._close_upload_dialog()
        self._stop_status_poller()
        if self._device_monitor is not None:
            self._device_monitor.stop()
            self._device_monitor.wait(5000)
        super().closeEvent(event)

    # ── File operations ───────────────────────────────────────────────────────

    def _new_project(self) -> None:
        """Clear all project data and start fresh (with confirmation)."""
        answer = QMessageBox.question(
            self, "New Project",
            "Start a new project?\n\n"
            "The current script, tracks and audio will be cleared.\n"
            "Save your work first if you want to keep it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._meta = {}
        self._script_path = None
        self._audio_files = []
        self._audio.unload()
        self._device_project_folder = None

        self._editor.set_tracks({})
        self._engines.clear()
        self._rebuild_engines()

        self._transport._bpm.setValue(120)
        self._transport.set_timesig(4, 4)

        self._set_track_display("No file loaded")
        self.setWindowTitle("Echoes")
        self.statusBar().showMessage("New project")

    def _open_mp3(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Audio File", "",
            "Audio Files (*.mp3 *.wav *.ogg *.opus *.flac *.aac *.m4a);;All Files (*)",
        )
        if not path:
            return
        self._audio.load(path)
        self._set_track_display(f"  \u266b  {os.path.basename(path)}")
        self.statusBar().showMessage(f"Loaded: {path}")

    def _import_tracks(self) -> None:
        """Unified import for .chart and .mid/.midi files."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Tracks", self._last_audio_dir,
            "Supported charts (*.chart *.mid *.midi)"
            ";;Clone Hero Chart (*.chart)"
            ";;MIDI Chart (*.mid *.midi)"
            ";;All Files (*)",
        )
        if not path:
            return

        self._last_audio_dir = os.path.dirname(path)
        chart_dir = os.path.dirname(path)
        ext = os.path.splitext(path)[1].lower()

        # ── Parse file ───────────────────────────────────────────────────────────────
        try:
            if ext == '.chart':
                with open(path, encoding='utf-8-sig', errors='replace') as fh:
                    _file_text = fh.read()
                from core.chart_importer import get_chart_info, convert_tracks_per_track
                song_meta, tracks, initial_bpm, ts_num, ts_den = \
                    get_chart_info(_file_text)
                res_default = '192'
                def _convert(cfgs, _t=_file_text):
                    return convert_tracks_per_track(_t, cfgs)[0]
            elif ext in ('.mid', '.midi'):
                with open(path, 'rb') as fh:
                    _file_data = fh.read()
                from core.midi_importer import get_midi_info, convert_midi_tracks_per_track
                song_meta, tracks, initial_bpm, ts_num, ts_den = \
                    get_midi_info(_file_data)
                res_default = '480'
                def _convert(cfgs, _d=_file_data):
                    return convert_midi_tracks_per_track(_d, cfgs)[0]
            else:
                QMessageBox.critical(
                    self, "Unsupported format",
                    f'Cannot import "{ext}" files.\nSupported: .chart  .mid  .midi'
                )
                return
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot open file", str(exc))
            return

        # song.ini in the same folder overrides metadata if present
        ini_path = os.path.join(chart_dir, 'song.ini')
        if os.path.isfile(ini_path):
            song_meta = {**song_meta, **_parse_song_ini(ini_path)}

        bpm_display = (f'{initial_bpm:.3f}' if initial_bpm != round(initial_bpm)
                       else str(round(initial_bpm)))
        song_meta['_Tempo']      = f'{bpm_display} BPM'
        song_meta['_TimeSig']    = f'{ts_num}/{ts_den}'
        song_meta['_Resolution'] = (
            song_meta.get('Resolution', res_default) + ' ticks/quarter-note')

        # ── Compute slot names for the dropdown ────────────────────────────────────
        from ui.script_editor import MAX_TRACKS
        existing      = self._editor.get_tracks()
        existing_names = list(existing.keys())
        slot_names     = existing_names.copy()
        for i in range(len(existing_names), MAX_TRACKS):
            slot_names.append(f'Slot {i + 1}')

        # ── Import dialog ────────────────────────────────────────────────────────────
        from ui.chart_import_dialog import ChartImportDialog
        dlg = ChartImportDialog(song_meta, tracks, self, slot_names=slot_names)
        if dlg.exec() != ChartImportDialog.DialogCode.Accepted:
            return

        # ── Convert each enabled preset and write to its slot ────────────────
        preset_assignments = dlg.get_preset_assignments()
        if not preset_assignments:
            QMessageBox.information(
                self, "Nothing to import",
                "No preset has a source track selected.  "
                "Assign at least one source track to import.",
            )
            return

        merged = dict(existing)
        for cfg, slot_name in preset_assignments:
            bodies = _convert([cfg])
            if bodies:
                merged[slot_name] = bodies[0][1]
        self._editor.set_tracks(merged)
        self._rebuild_engines()

        # ── Metadata ─────────────────────────────────────────────────────────────────
        def _mv(*keys: str) -> str:
            for k in keys:
                v = song_meta.get(k, '').strip().strip('"').strip()
                if v:
                    return v
            return ''

        bpm_int = max(20, min(300, round(initial_bpm)))

        if dlg.wants_metadata():
            self._meta = {}
            self._meta['title']   = _mv('Name',    'name')
            self._meta['author']  = _mv('Artist',  'artist')
            self._meta['charter'] = _mv('Charter', 'charter')
            self._meta['album']   = _mv('Album',   'album')
            self._meta['year']    = re.sub(r'^[,\s]+', '', _mv('Year', 'year'))
            self._meta['bpm']     = str(bpm_int)
            self._meta['timesig'] = f'{ts_num}/{ts_den}'
            if self._audio.duration > 0:
                self._meta['duration'] = format_ms(self._audio.duration)
            self._transport._bpm.setValue(bpm_int)
            self._transport.set_timesig(ts_num, ts_den)

        # ── Audio ───────────────────────────────────────────────────────────────────
        audio_file = _find_chart_audio(chart_dir, song_meta)
        if audio_file:
            self._audio.load(os.path.join(chart_dir, audio_file))
            self._set_track_display(f'  \u266b  {audio_file}')
            self.statusBar().showMessage(
                f'Imported: {os.path.basename(path)}  \u2014  '
                f'audio: {audio_file}  \u2014  {bpm_int} BPM  {ts_num}/{ts_den}'
            )
        else:
            self.statusBar().showMessage(
                f'Imported: {os.path.basename(path)}  \u2014  '
                f'{bpm_int} BPM  {ts_num}/{ts_den}'
            )

    def _open_mp3(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Audio File", "",
            "Audio Files (*.mp3 *.wav *.ogg *.opus *.flac *.aac *.m4a);;All Files (*)",
        )
        if not path:
            return
        self._set_audio_files([path])
        self.statusBar().showMessage(f"Loaded: {path}")

    def _open_audio_tracks(self) -> None:
        """
        Load up to 6 audio files (e.g. guitar/bass/drums/vocals stems) for a
        multi-track project. The first file is played for preview; use
        Convert to .MP3 to mix the selected tracks into a single file.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Load Audio Tracks (up to 6)", self._last_audio_dir,
            "Audio Files (*.mp3 *.wav *.ogg *.opus *.flac *.aac *.m4a);;All Files (*)",
        )
        if not paths:
            return
        if len(paths) > 6:
            paths = paths[:6]
            self.statusBar().showMessage(
                "Audio tracks limited to 6 \u2014 first 6 kept")
        self._last_audio_dir = os.path.dirname(paths[0])
        self._set_audio_files(paths)
        self.statusBar().showMessage(
            "Loaded " + ", ".join(os.path.basename(p) for p in paths))

    def _set_audio_files(self, paths) -> None:
        """Adopt *paths* as the project's audio-track list and preview the first."""
        self._audio_files = [os.path.abspath(p) for p in paths if os.path.isfile(p)]
        if self._audio_files:
            self._audio.load(self._audio_files[0])
            n = len(self._audio_files)
            self._set_track_display(
                f"  \u266b  {n} audio track{'s' if n != 1 else ''}"
                f"  \u2014  {os.path.basename(self._audio_files[0])}")
        else:
            self._set_track_display("No file loaded")

    def _set_track_display(self, text: str) -> None:
        """Update the toolbar info bar and the always-visible transport label."""
        self._info_bar.setText(text)
        self._transport.set_track_name(text)

    # ── Audio → MP3 conversion ────────────────────────────────────────────────

    def _find_project_audio(self) -> list:
        """
        Audio files found in the current project folder (up to 6), excluding
        the combined audio referenced by the script's # @audio field.
        """
        folder = None
        if self._script_path:
            folder = os.path.dirname(self._script_path)
        else:
            src = self._audio.source_path
            if src and os.path.isfile(src):
                folder = os.path.dirname(src)
        return find_audio_files(folder or _PROJECTS, limit=6,
                                exclude=[self._meta.get('audio', '')])

    def _convert_track_sources(self) -> list:
        """Candidate tracks: folder scan + any separately loaded audio."""
        found = self._find_project_audio()
        for p in self._audio_files + ([self._audio.source_path]
                                      if self._audio.source_path else []):
            if p and os.path.isfile(p) and len(found) < 6 and not any(
                    os.path.normcase(p) == os.path.normcase(f) for f in found):
                found.append(os.path.abspath(p))
        return found

    def _convert_to_mp3(self) -> None:
        """
        Convert audio to .MP3 — with several found tracks, let the user pick
        which ones are mixed into a single output file (and its name).
        """
        if self._mp3_worker is not None:
            return   # a conversion is already running

        # ── Candidate tracks: found in the project folder + loaded audio ─────
        sources = self._convert_track_sources()
        if not sources:
            src, _ = QFileDialog.getOpenFileName(
                self, "Select Audio File to Convert", self._last_audio_dir,
                "Audio Files (*.mp3 *.wav *.ogg *.opus *.flac *.aac *.m4a);;All Files (*)",
            )
            if not src:
                return
            self._last_audio_dir = os.path.dirname(src)
            sources = [src]

        # Default output name: script stem, else first track stem
        if self._script_path:
            default_name = os.path.splitext(
                os.path.basename(self._script_path))[0] + ".mp3"
        else:
            default_name = os.path.splitext(
                os.path.basename(sources[0]))[0] + ".mp3"

        from ui.track_mix_dialog import TrackMixDialog
        dlg = TrackMixDialog(sources, default_name, self)
        if dlg.exec() != TrackMixDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_tracks()
        out_folder = (os.path.dirname(os.path.abspath(self._script_path))
                      if self._script_path
                      else os.path.dirname(os.path.abspath(selected[0])))
        dst = os.path.join(out_folder, dlg.output_filename())

        # Never overwrite a source track
        if any(os.path.normcase(dst) == os.path.normcase(s)
               for s in selected):
            QMessageBox.warning(
                self, "Output conflicts with a source track",
                f"'{os.path.basename(dst)}' is one of the selected tracks.\n"
                "Choose a different output file name.")
            return

        need_ffmpeg = (len(selected) > 1 or
                       any(os.path.splitext(s)[1].lower() != ".mp3"
                           for s in selected))
        if need_ffmpeg and not ffmpeg_available():
            QMessageBox.warning(
                self, "ffmpeg not found",
                "Converting to MP3 requires ffmpeg.\n\n"
                "1. Download it from https://ffmpeg.org/download.html\n"
                "2. Extract the archive and add its 'bin' folder to your PATH\n"
                "3. Restart Echoes and try again.\n\n"
                "A single MP3 source is copied and does not need ffmpeg.",
            )
            return

        # ── Run the conversion off the GUI thread ────────────────────────────
        self._set_mp3_converting(True)
        n = len(selected)
        self.statusBar().showMessage(
            f"Converting {n} track{'s' if n != 1 else ''} "
            f"\u2192 {os.path.basename(dst)} \u2026")

        worker = Mp3ConvertWorker(selected[0], dst, self,
                                  extra_sources=selected[1:],
                                  normalize=dlg.normalize())
        worker.succeeded.connect(self._on_mp3_converted)
        worker.failed.connect(self._on_mp3_convert_failed)
        worker.finished.connect(self._on_mp3_worker_finished)
        self._mp3_worker = worker
        worker.start()

    def _set_mp3_converting(self, converting: bool) -> None:
        for act in self._mp3_actions:
            act.setEnabled(not converting)

    def _on_mp3_converted(self, dst: str) -> None:
        # Work on the combined output: load it and vinculate it to the script
        self._audio.load(dst)
        self._audio_files = [dst]
        self._set_track_display(f"  \u266b  {os.path.basename(dst)}")
        if self._script_path:
            self._meta['audio'] = os.path.basename(dst)
        self.statusBar().showMessage(f"MP3 saved: {dst}")
        QMessageBox.information(
            self, "MP3 ready",
            f"Audio saved to:\n{dst}\n\nLoaded for playback.",
        )

    def _on_mp3_convert_failed(self, message: str) -> None:
        self.statusBar().showMessage("MP3 conversion failed")
        QMessageBox.warning(self, "MP3 conversion failed", message)

    def _on_mp3_worker_finished(self) -> None:
        self._set_mp3_converting(False)
        worker = self._mp3_worker
        self._mp3_worker = None
        if worker is not None:
            worker.deleteLater()

    def _open_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "",
            "Echoes Script (*.echoes);;Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            QMessageBox.critical(self, "Cannot open file", str(exc))
            return
        self._meta = self._parse_meta(text)
        self._sync_meta_to_transport()
        body = self._strip_meta(text)
        self._editor.set_tracks(self._parse_tracks(body))
        self._script_path = path
        self.setWindowTitle(f"Echoes — {os.path.basename(path)}")

    def _save_script(self) -> None:
        if os.path.isfile(ini_path):
            ini_meta = _parse_song_ini(ini_path)
            # Merge: song.ini wins for keys that exist in both
            song_meta = {**song_meta, **ini_meta}

        # Append derived timing info for display in the dialog
        bpm_display = (f'{initial_bpm:.3f}' if initial_bpm != round(initial_bpm)
                       else str(round(initial_bpm)))
        song_meta['_Tempo']     = f'{bpm_display} BPM'
        song_meta['_TimeSig']   = f'{ts_num}/{ts_den}'
        song_meta['_Resolution'] = song_meta.get('Resolution', '192') + ' ticks/quarter-note'

        dlg = ChartImportDialog(song_meta, tracks, self)
        if dlg.exec() != ChartImportDialog.DialogCode.Accepted:
            return

        configs = dlg.get_configs()

        if not any(c.enabled for c in configs):
            QMessageBox.information(
                self, "Nothing to import",
                "No tracks were selected.  Enable at least one track to import.",
            )
            return

        track_list, _audio_hint = convert_tracks_per_track(chart_text, configs)

        # ── Merge / assign tracks ─────────────────────────────────────────────
        existing    = self._editor.get_tracks()
        import_meta = True

        from ui.import_merge_dialog import ImportMergeDialog, MAX_TRACKS
        merge_dlg = ImportMergeDialog(
            [name for name, _ in track_list],
            list(existing.keys()),
            self,
        )
        if merge_dlg.exec() != ImportMergeDialog.DialogCode.Accepted:
            return

        incoming_dict = {name: body for name, body in track_list}
        merged: dict[str, str] = {}
        for slot_name, source in merge_dlg.get_slot_assignments():
            if source is None:
                if slot_name and slot_name in existing:
                    merged[slot_name] = existing[slot_name]
            else:
                if slot_name:
                    merged[slot_name] = incoming_dict.get(source, '')
                else:
                    final = source
                    n = 2
                    while final in merged:
                        final = f"{source} ({n})"; n += 1
                    merged[final] = incoming_dict.get(source, '')

        if not merged:
            return
        self._editor.set_tracks(merged)
        import_meta = merge_dlg.wants_metadata()

        # ── Populate script metadata from chart info ──────────────────────────
        def _mv(*keys: str) -> str:
            """First non-empty value from song_meta for any of the given keys."""
            for k in keys:
                v = song_meta.get(k, '').strip().strip('"').strip()
                if v:
                    return v
            return ''

        bpm_int = max(20, min(300, round(initial_bpm)))

        if import_meta:
            self._meta = {}
            self._meta['title']   = _mv('Name',    'name')
            self._meta['author']  = _mv('Artist',  'artist')
            self._meta['charter'] = _mv('Charter', 'charter')
            self._meta['album']   = _mv('Album',   'album')
            self._meta['year']    = re.sub(r'^[,\s]+', '', _mv('Year', 'year'))
            self._meta['bpm']     = str(bpm_int)
            self._meta['timesig'] = f'{ts_num}/{ts_den}'
            if self._audio.duration > 0:
                self._meta['duration'] = format_ms(self._audio.duration)
            # ── Sync transport BPM and time signature ────────────────────────────
            self._transport._bpm.setValue(bpm_int)
            self._transport.set_timesig(ts_num, ts_den)

        # ── Find and load audio ───────────────────────────────────────────────
        audio_file = _find_chart_audio(chart_dir, song_meta)
        if audio_file:
            audio_path = os.path.join(chart_dir, audio_file)
            self._audio.load(audio_path)
            self._set_track_display(f"  \u266b  {audio_file}")
            self.statusBar().showMessage(
                f"Imported: {os.path.basename(path)}  \u2014  "
                f"audio: {audio_file}  \u2014  "
                f"{bpm_int} BPM  {ts_num}/{ts_den}"
            )
        else:
            self.statusBar().showMessage(
                f"Imported: {os.path.basename(path)}  \u2014  "
                f"{bpm_int} BPM  {ts_num}/{ts_den}"
            )

    def _import_midi(self) -> None:
        """Import a Clone Hero / Rock Band notes.mid file as an echoes script."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import MIDI Chart", self._last_audio_dir,
            "MIDI Chart (*.mid *.midi);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, 'rb') as fh:
                midi_data = fh.read()
        except OSError as exc:
            QMessageBox.critical(self, "Cannot open file", str(exc))
            return

        from core.midi_importer import get_midi_info, convert_midi_tracks_per_track
        from ui.chart_import_dialog import ChartImportDialog

        try:
            song_meta, tracks, initial_bpm, ts_num, ts_den = get_midi_info(midi_data)
        except ValueError as exc:
            QMessageBox.critical(self, "Cannot read MIDI file", str(exc))
            return

        # song.ini in the same folder is the canonical metadata source for .mid
        chart_dir = os.path.dirname(path)
        ini_path  = os.path.join(chart_dir, 'song.ini')
        if os.path.isfile(ini_path):
            ini_meta  = _parse_song_ini(ini_path)
            song_meta = {**song_meta, **ini_meta}

        bpm_display = (f'{initial_bpm:.3f}' if initial_bpm != round(initial_bpm)
                       else str(round(initial_bpm)))
        song_meta['_Tempo']      = f'{bpm_display} BPM'
        song_meta['_TimeSig']    = f'{ts_num}/{ts_den}'
        song_meta['_Resolution'] = song_meta.get('Resolution', '480') + ' ticks/quarter-note'

        dlg = ChartImportDialog(song_meta, tracks, self)
        if dlg.exec() != ChartImportDialog.DialogCode.Accepted:
            return

        configs = dlg.get_configs()

        if not any(c.enabled for c in configs):
            QMessageBox.information(
                self, "Nothing to import",
                "No tracks were selected.  Enable at least one track to import.",
            )
            return

        track_list, _ = convert_midi_tracks_per_track(midi_data, configs)

        # ── Merge / assign tracks ─────────────────────────────────────────────
        existing    = self._editor.get_tracks()
        import_meta = True

        from ui.import_merge_dialog import ImportMergeDialog, MAX_TRACKS
        merge_dlg = ImportMergeDialog(
            [name for name, _ in track_list],
            list(existing.keys()),
            self,
        )
        if merge_dlg.exec() != ImportMergeDialog.DialogCode.Accepted:
            return

        incoming_dict = {name: body for name, body in track_list}
        merged: dict[str, str] = {}
        for slot_name, source in merge_dlg.get_slot_assignments():
            if source is None:
                if slot_name and slot_name in existing:
                    merged[slot_name] = existing[slot_name]
            else:
                if slot_name:
                    merged[slot_name] = incoming_dict.get(source, '')
                else:
                    final = source
                    n = 2
                    while final in merged:
                        final = f"{source} ({n})"; n += 1
                    merged[final] = incoming_dict.get(source, '')

        if not merged:
            return
        self._editor.set_tracks(merged)
        import_meta = merge_dlg.wants_metadata()

        # ── Populate metadata ─────────────────────────────────────────────────
        def _mv(*keys: str) -> str:
            for k in keys:
                v = song_meta.get(k, '').strip().strip('"').strip()
                if v:
                    return v
            return ''

        bpm_int = max(20, min(300, round(initial_bpm)))

        if import_meta:
            self._meta = {}
            self._meta['title']   = _mv('Name', 'name')
            self._meta['author']  = _mv('Artist', 'artist')
            self._meta['charter'] = _mv('Charter', 'charter')
            self._meta['album']   = _mv('Album', 'album')
            self._meta['year']    = re.sub(r'^[,\s]+', '', _mv('Year', 'year'))
            self._meta['bpm']     = str(bpm_int)
            self._meta['timesig'] = f'{ts_num}/{ts_den}'
            if self._audio.duration > 0:
                self._meta['duration'] = format_ms(self._audio.duration)
            # ── Sync transport ────────────────────────────────────────────────────
            self._transport._bpm.setValue(bpm_int)
            self._transport.set_timesig(ts_num, ts_den)

        # ── Audio ─────────────────────────────────────────────────────────────
        audio_file = _find_chart_audio(chart_dir, song_meta)
        if audio_file:
            self._audio.load(os.path.join(chart_dir, audio_file))
            self._set_track_display(f"  \u266b  {audio_file}")
            self.statusBar().showMessage(
                f"Imported: {os.path.basename(path)}  \u2014  "
                f"audio: {audio_file}  \u2014  {bpm_int} BPM  {ts_num}/{ts_den}"
            )
        else:
            self.statusBar().showMessage(
                f"Imported: {os.path.basename(path)}  \u2014  "
                f"{bpm_int} BPM  {ts_num}/{ts_den}"
            )

    def _open_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Script", "",
            "Echoes Script (*.echoes);;Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            QMessageBox.critical(self, "Cannot open file", str(exc))
            return
        self._apply_script_text(text, path=path)

    def _apply_script_text(self, text: str, path: Optional[str] = None,
                           title: Optional[str] = None) -> None:
        """
        Load *text* into the editor: parse metadata, sync the transport,
        populate the track slots and rebuild the engines.

        *path* becomes the save target (None = remote/untitled → Save As);
        *title* overrides the window title (used for device-loaded projects).
        """
        self._meta = self._parse_meta(text)
        self._sync_meta_to_transport()
        body = self._strip_meta(text)
        self._editor.set_tracks(self._parse_tracks(body))
        self._rebuild_engines()
        self._script_path = path
        if title:
            self.setWindowTitle(f"Echoes — {title}")
        elif path:
            self.setWindowTitle(f"Echoes — {os.path.basename(path)}")
        # Vinculated audio: load the file recorded in # @audio when available
        if path:
            self._load_audio_for_script(path)
            # Discover multi-track stems for the Convert to .MP3 flow
            found = self._find_project_audio()
            if len(found) > 1:
                self._audio_files = found
                self.statusBar().showMessage(
                    f"Found {len(found)} audio tracks \u2014 "
                    "use \u201cConvert to .MP3\u201d to mix them")

    def _audio_ref_for(self, script_path: str) -> str:
        """
        Reference string for the currently loaded audio, relative to the
        script's folder: the bare filename when the audio sits next to the
        script (the typical project layout), otherwise a relative path.
        Returns '' when no local audio file is loaded.
        """
        src = self._audio.source_path
        if not src or not os.path.isfile(src):
            return ''
        src = os.path.abspath(src)
        script_dir = os.path.dirname(os.path.abspath(script_path))
        if os.path.normcase(os.path.dirname(src)) == os.path.normcase(script_dir):
            return os.path.basename(src)
        try:
            rel = os.path.relpath(src, script_dir)
        except ValueError:                      # different drive letter
            return os.path.basename(src)
        return rel.replace(os.sep, '/')

    def _load_audio_for_script(self, script_path: str) -> None:
        """Load the audio file recorded in the script metadata, if present."""
        audio_val = self._meta.get('audio', '').strip()
        if not audio_val:
            return
        if os.path.isabs(audio_val):
            candidate = audio_val
        else:
            candidate = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(script_path)),
                             audio_val))
        if os.path.isfile(candidate):
            self._set_audio_files([candidate])
        else:
            self.statusBar().showMessage(f"Audio not found: {audio_val}")

    def _save_script(self) -> None:
        if self._script_path:
            self._write_script(self._script_path)
        else:
            self._save_script_as()

    def _save_script_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Script",
            self._script_path or self._last_script_dir,
            "Echoes Script (*.echoes);;Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return
        self._last_script_dir = os.path.dirname(path)
        self._script_path = path
        self._write_script(path)
        self.setWindowTitle(f"Echoes — {os.path.basename(path)}")

    def _write_script(self, path: str) -> None:
        # Refresh auto-fields before writing
        self._meta['bpm'] = str(self._transport._bpm.value())
        ts_num = self._transport._ts_num.value()
        ts_den = int(self._transport._ts_den.currentText())
        self._meta['timesig']  = f"{ts_num}/{ts_den}"
        self._meta['modified'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        dur_ms = self._audio.duration
        if dur_ms:
            self._meta['duration'] = format_ms(dur_ms)
        # Vinculate the loaded audio so opening the script reloads it
        audio_val = self._audio_ref_for(path)
        if audio_val:
            self._meta['audio'] = audio_val
        tracks = self._editor.get_tracks()
        body   = self._build_tracks_body(tracks)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._build_meta_header(self._meta))
                fh.write(body)
            self.statusBar().showMessage(f"Saved: {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Cannot save file", str(exc))

    # ── Runtime slots ─────────────────────────────────────────────────────────

    def _on_position(self, pos_ms: int) -> None:
        eng = self._engines.get(self._editor.active_track_name())
        if eng:
            self._editor.set_active_line(eng.get_active_line_at(pos_ms))
        states = self._evaluate_all(pos_ms)
        self._canvas.update_leds(states)
        self._highway.update_position(pos_ms)
        self._highway.update_leds(states)

    def _on_seek(self, pos_ms: int) -> None:
        eng = self._engines.get(self._editor.active_track_name())
        if eng:
            self._editor.set_active_line(eng.get_active_line_at(pos_ms))
        states = self._evaluate_all(pos_ms)
        self._canvas.update_leds(states)
        self._highway.update_position(pos_ms)
        self._highway.update_leds(states)

    def _on_script_changed(self, text: str) -> None:
        name = self._editor.active_track_name()
        eng  = self._engines.setdefault(name, ScriptEngine())
        eng.parse(text)
        self._highway.update_commands(eng.commands)
        self._editor.set_error_lines(eng.error_line_set())
        n = len(eng.errors)
        if n:
            msgs = "   ".join(
                f"L{e.line_number}: {e.message}"
                for e in eng.errors[:3])
            suffix = f"   (+{n - 3} more)" if n > 3 else ""
            self.statusBar().showMessage(f"\u26a0  {msgs}{suffix}")
        else:
            c = len(eng.commands)
            self.statusBar().showMessage(
                f"Script OK \u2014 {c} command{'s' if c != 1 else ''}")

    def _evaluate_all(self, pos_ms: int) -> dict:
        """Merge LED states from every track engine (later tracks override earlier)."""
        states: dict = {}
        for engine in self._engines.values():
            states.update(engine.evaluate_at(pos_ms))
        return states

    def _rebuild_engines(self) -> None:
        """Re-parse all non-active track engines after a bulk set_tracks call."""
        tracks = self._editor.get_tracks()
        active = self._editor.active_track_name()
        for k in [k for k in self._engines if k not in tracks]:
            del self._engines[k]
        for name, body in tracks.items():
            if name == active:
                continue   # already up-to-date via _on_script_changed
            eng = self._engines.setdefault(name, ScriptEngine())
            eng.parse(body)

    def _on_background_script_changed(self, track_name: str, text: str) -> None:
        """Re-parse a background track's engine when it is edited in the right editor."""
        self._engines.setdefault(track_name, ScriptEngine()).parse(text)

    def _on_command_ready(self, line: str) -> None:
        """Route a toolbox command to the selected target slot."""
        target = self._toolbox.current_target_track()
        if target:
            self._editor.insert_command_line_to(target, line)
        else:
            self._editor.insert_command_line(line)

    def _on_duration(self, ms: int) -> None:
        """Keep _meta duration in sync whenever audio is (re)loaded."""
        if ms > 0:
            self._meta['duration'] = format_ms(ms)

    def _on_audio_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"Audio error: {msg}")

    # ── Properties dialog ────────────────────────────────────────────────

    def _show_properties(self) -> None:
        dlg = MetadataDialog(self._meta, self)
        if dlg.exec() != MetadataDialog.DialogCode.Accepted:
            return
        self._meta = dlg.get_meta()
        self._sync_meta_to_transport()

    # ── Metadata helpers ────────────────────────────────────────────────

    @staticmethod
    def _parse_tracks(body: str) -> dict:
        """
        Split a multi-track .echoes body into {track_name: body_text}.

        Track sections start with a bare ``[TrackName]`` header line.
        If no headers are found the entire body is returned as a single
        'Default' track.
        """
        tracks: dict[str, str] = {}
        current_name: str | None = None
        current_lines: list[str] = []

        for line in body.splitlines(keepends=True):
            m = re.match(r'^\[([^\]]+)\]\s*$', line.strip())
            if m:
                if current_name is not None:
                    tracks[current_name] = ''.join(current_lines).strip('\n')
                current_name = m.group(1)
                current_lines = []
            else:
                if current_name is not None:
                    current_lines.append(line)

        if current_name is not None:
            tracks[current_name] = ''.join(current_lines).strip('\n')

        if not tracks:
            stripped = body.strip()
            if stripped:
                tracks['Default'] = stripped

        return tracks

    @staticmethod
    def _build_tracks_body(tracks: dict) -> str:
        """Serialise {track_name: body_text} to multi-track file format."""
        parts: list[str] = []
        for name, body in tracks.items():
            parts.append(f'[{name}]')
            b = body.strip('\n')
            if b:
                parts.append(b)
            parts.append('')
        return '\n'.join(parts) + '\n' if parts else ''

    @staticmethod
    def _parse_meta(text: str) -> dict:
        """Extract leading # @key value lines from a script."""
        meta: dict = {}
        for line in text.splitlines():
            s = line.strip()
            if not s.startswith('#'):
                break
            m = re.match(r'^#\s*@(\w+)\s*(.*)', s)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if key == 'comment':
                    prev = meta.get('comment', '')
                    meta['comment'] = (prev + '\n' + val).lstrip('\n') if prev else val
                else:
                    meta[key] = val
        return meta

    @staticmethod
    def _strip_meta(text: str) -> str:
        """Remove the leading # @... metadata block, leaving the script body."""
        lines = text.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            if re.match(r'^#\s*@\w+', s) or s == '#':
                i += 1
            else:
                break
        return ''.join(lines[i:])

    @staticmethod
    def _build_meta_header(meta: dict) -> str:
        """Serialise metadata dict to a # @key  value header block."""
        lines = []
        for key in ('title', 'author', 'charter', 'album', 'year',
                    'bpm', 'timesig', 'audio',
                    'track1', 'track2', 'track3', 'track4',
                    'duration', 'modified'):
            val = meta.get(key, '').strip()
            if val:
                lines.append(f'# @{key:<10} {val}')
        comment = meta.get('comment', '').strip()
        if comment:
            for cl in comment.splitlines():
                lines.append(f'# @comment    {cl}')
        if lines:
            lines.append('#')
            return '\n'.join(lines) + '\n'
        return ''

    def _sync_meta_to_transport(self) -> None:
        """Push BPM and time-signature from _meta into the transport bar."""
        try:
            bpm = int(self._meta.get('bpm', '') or 0)
            if bpm:
                self._transport._bpm.setValue(bpm)
        except ValueError:
            pass
        try:
            ts = self._meta.get('timesig', '')
            if '/' in ts:
                num, den = ts.split('/', 1)
                self._transport.set_timesig(int(num), int(den))
            elif ts:
                self._transport.set_timesig(int(ts), 4)
        except (ValueError, IndexError):
            pass

    # ── Hardware version conversion ────────────────────────────────────────────

    def _convert_hw_version(self) -> None:
        """Ask the user for conversion direction, then remap LED addresses."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QRadioButton, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Convert Hardware Version")
        dlg.setFixedWidth(320)

        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(10)

        lbl = QLabel(
            "<b>Remap LED addresses between hardware versions.</b><br><br>"
            "<span style='color:#aaaaaa'>"
            "V1 — eyes at LEDs 27-28 (strip starts at bottom-right spike)<br>"
            "V2 — eyes at LEDs&nbsp;1-2 (strip starts at left eye)"
            "</span>"
        )
        lbl.setWordWrap(True)
        vbox.addWidget(lbl)

        rb_v1_to_v2 = QRadioButton("V1 → V2  (eyes: 27-28 → 1-2)")
        rb_v2_to_v1 = QRadioButton("V2 → V1  (eyes:  1-2 → 27-28)")
        rb_v1_to_v2.setChecked(True)
        vbox.addWidget(rb_v1_to_v2)
        vbox.addWidget(rb_v2_to_v1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        vbox.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        to_v2  = rb_v1_to_v2.isChecked()
        ed     = self._editor.left_editor
        source = ed.toPlainText()
        result = remap_hw_version(source, to_v2)

        if result == source:
            QMessageBox.information(
                self, "No changes",
                "No LED addresses were changed.\n"
                "The script may already be in the target format, "
                "or it contains only wildcard (*) specs."
            )
            return

        direction = "V1 → V2" if to_v2 else "V2 → V1"
        ed.setPlainText(result)
        self.statusBar().showMessage(f"Hardware version converted: {direction}")

    # ── Zone right-click → script insertion ────────────────────────────────

    def _on_zone_right_clicked(self, zone_id: int) -> None:
        ts = format_ms(self._audio.position)
        z  = str(zone_id)

        menu = QMenu(self)
        menu.setTitle(f"Zone {zone_id}  •  {ts}")
        menu.addSection(f"Zone {zone_id}  •  {ts}")

        menu.addAction(
            f"SET  — solid colour",
            lambda: self._insert_command(f"{ts} SET 255 255 255 {z}")
        )
        menu.addAction(
            f"RAMP  — interpolate over time",
            lambda: self._insert_command(
                f"{ts} RAMP 0:01 255 255 255 0 0 0 {z}")
        )
        menu.addAction(
            f"FADE  — exponential decay to black",
            lambda: self._insert_command(f"{ts} FADE 255 255 255 50 {z}")
        )
        menu.exec(QCursor.pos())

    def _insert_command(self, template: str) -> None:
        """
        Insert *template* into the script editor after the last command whose
        timestamp is ≤ the template's own timestamp, preserving chronological
        order.  Selects the editable parameter region so the user can type
        immediately.
        """
        ed    = self._editor.left_editor
        doc   = ed.document()
        parts = template.split()
        ts_ms = _parse_timestamp(parts[0]) or 0

        # Find the last line whose timestamp ≤ ts_ms
        insert_after = -1
        for i in range(doc.blockCount()):
            txt = doc.findBlockByNumber(i).text().split('#', 1)[0].strip()
            if not txt:
                continue
            toks = txt.split()
            if toks:
                t = _parse_timestamp(toks[0])
                if t is not None and t <= ts_ms:
                    insert_after = i

        cur = QTextCursor(doc)
        if insert_after < 0:
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.insertText(template + '\n')
            new_block_num = 0
        else:
            blk = doc.findBlockByNumber(insert_after)
            cur.setPosition(blk.position() + blk.length() - 1)
            cur.insertText('\n' + template)
            new_block_num = insert_after + 1

        # Select the parameter region (between command keyword and LED-SPEC)
        # so the user can type the colours / values straight away
        new_blk      = doc.findBlockByNumber(new_block_num)
        params_start = len(parts[0]) + 1 + len(parts[1]) + 1   # after "M:SS CMD "
        params_end   = len(template) - len(parts[-1]) - 1       # before " LED-SPEC"

        cur.setPosition(new_blk.position() + params_start)
        cur.setPosition(new_blk.position() + params_end,
                        QTextCursor.MoveMode.KeepAnchor)

        ed.setTextCursor(cur)
        ed.ensureCursorVisible()
        ed.setFocus()
