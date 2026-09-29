"""
Echoes — MainWindow chrome construction (mixin).

Split out of main_window.py to keep that file focused on runtime behaviour.
This module only builds static UI structure: the central widget (editor /
highway / canvas / toolbox / transport), the two toolbars, and the menu bar.
It assumes it is mixed into MainWindow, which provides the ``self`` widgets
and slots referenced here (``self._open_mp3``, ``self._device_play`` …).
"""
from __future__ import annotations

from PySide6.QtCore    import Qt, QSize
from PySide6.QtGui     import QAction
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy,
    QSplitter, QToolBar, QVBoxLayout, QWidget,
)

from core.device_discovery import DEFAULT_HOSTNAME

from ui.command_toolbox  import CommandToolbox
from ui.led_canvas       import LEDCanvas
from ui.note_highway     import NoteHighway
from ui.script_editor    import MultiTrackEditorWidget
from ui.transport_bar    import TransportBar


class MainWindowChromeMixin:
    """Builds the MainWindow's static UI: central widget, toolbars, menu."""

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
        splitter.addWidget(right)

        # Editor starts wide (~44 %) so the two split editors stay readable,
        # and it absorbs extra width when the window grows.  The right pane
        # holds only the highway/canvas (small minimums), so the editor can be
        # dragged much wider by the user.
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([640, 800])
        vbox.addWidget(splitter, stretch=1)

        # ── Full-width bottom bars: command toolbox, then transport ──────────
        # Kept outside the right pane so their natural width (the toolbox is
        # wide) never squeezes the script editor.
        div = QWidget()
        div.setFixedHeight(1)
        div.setStyleSheet("background:#333333;")
        vbox.addWidget(div)

        self._toolbox = CommandToolbox(self._audio)
        vbox.addWidget(self._toolbox, stretch=0)

        div2 = QWidget()
        div2.setFixedHeight(1)
        div2.setStyleSheet("background:#333333;")
        vbox.addWidget(div2)

        self._transport = TransportBar(self._audio)
        vbox.addWidget(self._transport, stretch=0)

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
        tm.addSeparator()
        self._numpad_action = QAction("Numpad &Macro Hotkeys (1-9)", self)
        self._numpad_action.setCheckable(True)
        self._numpad_action.setChecked(self._use_numpad)
        self._numpad_action.setToolTip(
            "Let the numpad digit keys 1-9 fire macros as well as the\n"
            "main-row 1-9 keys.  Disable if the numpad is used for\n"
            "something else.  (Neither ever fires while editing text.)")
        self._numpad_action.toggled.connect(self._on_numpad_changed)
        tm.addAction(self._numpad_action)

    def _act(self, menu, label: str, shortcut: str, slot) -> QAction:
        a = QAction(label, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a
