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

from PySide6.QtCore    import Qt, QSize
from PySide6.QtGui     import QAction, QCursor, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QSizePolicy, QSplitter, QToolBar, QVBoxLayout, QWidget,
)

from core.audio_player  import AudioPlayer
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
from ui.led_canvas       import LEDCanvas
from ui.metadata_dialog  import MetadataDialog
from ui.note_highway     import NoteHighway
from ui.script_editor    import MultiTrackEditorWidget, ScriptEditor
from ui.transport_bar    import TransportBar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Echoes")
        self.resize(1440, 780)
        self.setMinimumSize(900, 560)

        self._audio  = AudioPlayer(self)
        self._engines: dict[str, ScriptEngine] = {}   # one engine per track slot
        self._script_path: Optional[str] = None
        self._last_audio_dir:  str = _PROJECTS
        self._last_script_dir: str = _PROJECTS
        self._meta: dict = {}   # metadata parsed from / written to the .echoes header

        self._build_central()
        self._build_toolbar()
        self._build_menu()
        self._wire()

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
        hsplit.addWidget(self._canvas)

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

        tb.addSeparator()

        act_convert = QAction(
            style.standardIcon(SP.SP_BrowserReload), "  Convert HW", self)
        act_convert.setToolTip(
            "Remap LED addresses between V1 and V2 hardware\n"
            "V1: eyes at LEDs 27-28 | V2: eyes at LEDs 1-2")
        act_convert.triggered.connect(self._convert_hw_version)
        tb.addAction(act_convert)

        # Expanding spacer so the filename label is pushed to the right
        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # Background image toggle
        self._bg_check = QCheckBox("BG", self)
        self._bg_check.setChecked(True)
        self._bg_check.setToolTip("Show / hide the background image")
        self._bg_check.setStyleSheet(
            "color:#aaaaaa; font:11px 'Segoe UI'; padding-right:6px;")
        self._bg_check.toggled.connect(
            lambda on: self._canvas.set_bg_visible(on))
        tb.addWidget(self._bg_check)

        # Zone label toggle
        self._lbl_check = QCheckBox("Labels", self)
        self._lbl_check.setChecked(True)
        self._lbl_check.setToolTip("Show / hide zone number labels")
        self._lbl_check.setStyleSheet(
            "color:#aaaaaa; font:11px 'Segoe UI'; padding-right:6px;")
        self._lbl_check.toggled.connect(
            lambda on: self._canvas.set_labels_visible(on))
        tb.addWidget(self._lbl_check)

        # Hardware version toggle
        self._v2_check = QCheckBox("HW V2", self)
        self._v2_check.setChecked(False)
        self._v2_check.setToolTip(
            "V1: eyes at LEDs 27-28 (default)\n"
            "V2: eyes at LEDs 1-2")
        self._v2_check.setStyleSheet(
            "color:#aaaaaa; font:11px 'Segoe UI'; padding-right:6px;")
        self._v2_check.toggled.connect(
            lambda v2: self._canvas.set_hw_version(v2))
        tb.addWidget(self._v2_check)

        self._info_bar = QLabel("No file loaded")
        self._info_bar.setStyleSheet(
            "color:#888888; font:11px 'Segoe UI'; padding-right:8px;")
        tb.addWidget(self._info_bar)

    def _build_menu(self) -> None:
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        self._act(fm, "&Open Audio\u2026",         "Ctrl+O",        self._open_mp3)
        fm.addSeparator()
        self._act(fm, "Open &Project\u2026",      "Ctrl+Shift+O",  self._open_script)
        self._act(fm, "&Save Script",             "Ctrl+S",        self._save_script)
        self._act(fm, "Save Script &As\u2026",    "Ctrl+Shift+S",  self._save_script_as)
        self._act(fm, "Import &Tracks\u2026",     "Ctrl+Shift+I",  self._import_tracks)
        fm.addSeparator()
        self._act(fm, "Script &Properties\u2026", "Ctrl+I",        self._show_properties)
        fm.addSeparator()
        self._act(fm, "&Quit",                    "Ctrl+Q",        self.close)

    def _act(self, menu, label: str, shortcut: str, slot) -> None:
        a = QAction(label, self)
        a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)

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

    # ── File operations ───────────────────────────────────────────────────────

    def _open_mp3(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Audio File", "",
            "Audio Files (*.mp3 *.wav *.ogg *.opus *.flac *.aac *.m4a);;All Files (*)",
        )
        if not path:
            return
        self._audio.load(path)
        self._info_bar.setText(f"  \u266b  {os.path.basename(path)}")
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
            self._info_bar.setText(f'  \u266b  {audio_file}')
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
        self._audio.load(path)
        self._info_bar.setText(f"  \u266b  {os.path.basename(path)}")
        self.statusBar().showMessage(f"Loaded: {path}")

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
            self._info_bar.setText(f"  \u266b  {audio_file}")
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
            self._info_bar.setText(f"  \u266b  {audio_file}")
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
        self._meta = self._parse_meta(text)
        self._sync_meta_to_transport()
        body = self._strip_meta(text)
        self._editor.set_tracks(self._parse_tracks(body))
        self._rebuild_engines()
        self._script_path = path
        self.setWindowTitle(f"Echoes — {os.path.basename(path)}")

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
                    'bpm', 'timesig',
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
