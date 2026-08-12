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

import os
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
from ui.command_toolbox import CommandToolbox
from ui.led_canvas      import LEDCanvas
from ui.script_editor   import ScriptEditor
from ui.transport_bar   import TransportBar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Echoes")
        self.resize(1280, 720)

        self._audio  = AudioPlayer(self)
        self._engine = ScriptEngine()
        self._script_path: Optional[str] = None
        self._last_audio_dir:  str = _PROJECTS
        self._last_script_dir: str = _PROJECTS

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

        # Left — script editor
        self._editor = ScriptEditor()
        self._editor.setPlaceholderText(
            "# Echoes light show script\n"
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

        # Right — canvas + transport
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        self._canvas = LEDCanvas()
        rv.addWidget(self._canvas, stretch=1)

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
        splitter.setSizes([400, 880])
        vbox.addWidget(splitter, stretch=1)

        self.statusBar().showMessage(
            "Ready  —  File › Open Audio  to load a track, "
            "File › Open Script  to load or create a show")

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
            style.standardIcon(SP.SP_FileDialogStart), "  Import Script", self)
        act_import.setShortcut("Ctrl+Shift+O")
        act_import.setToolTip("Open an existing .echoes script  (Ctrl+Shift+O)")
        act_import.triggered.connect(self._open_script)
        tb.addAction(act_import)

        act_export = QAction(
            style.standardIcon(SP.SP_DialogSaveButton), "  Export Script", self)
        act_export.setShortcut("Ctrl+S")
        act_export.setToolTip("Save script to file  (Ctrl+S)")
        act_export.triggered.connect(self._save_script)
        tb.addAction(act_export)

        act_chart = QAction(
            style.standardIcon(SP.SP_FileDialogDetailedView), "  Import .chart", self)
        act_chart.setToolTip("Import a Clone Hero .chart file as echoes script")
        act_chart.triggered.connect(self._import_chart)
        tb.addAction(act_chart)

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
        self._act(fm, "&Open Audio…",      "Ctrl+O",        self._open_mp3)
        fm.addSeparator()
        self._act(fm, "Open &Script…",    "Ctrl+Shift+O",  self._open_script)
        self._act(fm, "&Save Script",      "Ctrl+S",        self._save_script)
        self._act(fm, "Save Script &As\u2026", "Ctrl+Shift+S",  self._save_script_as)
        self._act(fm, "Import &.chart\u2026",  "",              self._import_chart)
        fm.addSeparator()
        self._act(fm, "&Quit",             "Ctrl+Q",        self.close)

    def _act(self, menu, label: str, shortcut: str, slot) -> None:
        a = QAction(label, self)
        a.setShortcut(shortcut)
        a.triggered.connect(slot)
        menu.addAction(a)

    # ── Signal wiring ─────────────────────────────────────────────────────────

    def _wire(self) -> None:
        self._audio.position_changed.connect(self._on_position)
        self._audio.error_occurred.connect(self._on_audio_error)
        self._editor.script_changed.connect(self._on_script_changed)
        self._transport.seek_requested.connect(self._on_seek)
        self._canvas.zone_right_clicked.connect(self._on_zone_right_clicked)
        self._toolbox.command_ready.connect(self._editor.insert_command_line)

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

    def _import_chart(self) -> None:
        """Import a Clone Hero .chart file and convert it to an echoes script."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Clone Hero Chart", self._last_audio_dir,
            "Clone Hero Chart (*.chart);;All Files (*)",
        )
        if not path:
            return

        if self._editor.toPlainText().strip():
            ans = QMessageBox.question(
                self, "Replace script?",
                "The editor already contains a script.\n"
                "Replace it with the imported chart?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                chart_text = fh.read()
        except OSError as exc:
            QMessageBox.critical(self, "Cannot open file", str(exc))
            return

        from core.chart_importer import convert
        script, audio_file = convert(chart_text)

        self._editor.setPlainText(script)
        self.statusBar().showMessage(f"Imported: {os.path.basename(path)}")

        # Auto-load the referenced audio file if it lives next to the .chart
        if audio_file:
            chart_dir = os.path.dirname(path)
            audio_path = os.path.join(chart_dir, audio_file)
            if os.path.isfile(audio_path):
                self._audio.load(audio_path)
                self._info_bar.setText(f"  \u266b  {audio_file}")
                self.statusBar().showMessage(
                    f"Imported: {os.path.basename(path)}  \u2014  "
                    f"audio: {audio_file}"
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
                self._editor.setPlainText(fh.read())
        except OSError as exc:
            QMessageBox.critical(self, "Cannot open file", str(exc))
            return
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
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._editor.toPlainText())
            self.statusBar().showMessage(f"Saved: {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Cannot save file", str(exc))

    # ── Runtime slots ─────────────────────────────────────────────────────────

    def _on_position(self, pos_ms: int) -> None:
        self._editor.set_active_line(self._engine.get_active_line_at(pos_ms))
        self._canvas.update_leds(self._engine.evaluate_at(pos_ms))

    def _on_seek(self, pos_ms: int) -> None:
        self._editor.set_active_line(self._engine.get_active_line_at(pos_ms))
        self._canvas.update_leds(self._engine.evaluate_at(pos_ms))

    def _on_script_changed(self, text: str) -> None:
        self._engine.parse(text)
        self._editor.set_error_lines(self._engine.error_line_set())
        n = len(self._engine.errors)
        if n:
            msgs = "   ".join(
                f"L{e.line_number}: {e.message}"
                for e in self._engine.errors[:3])
            suffix = f"   (+{n - 3} more)" if n > 3 else ""
            self.statusBar().showMessage(f"\u26a0  {msgs}{suffix}")
        else:
            c = len(self._engine.commands)
            self.statusBar().showMessage(
                f"Script OK \u2014 {c} command{'s' if c != 1 else ''}")

    def _on_audio_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"Audio error: {msg}")

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
        source = self._editor.toPlainText()
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
        self._editor.setPlainText(result)
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
        doc   = self._editor.document()
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
        new_blk     = doc.findBlockByNumber(new_block_num)
        params_start = len(parts[0]) + 1 + len(parts[1]) + 1   # after "M:SS CMD "
        params_end   = len(template) - len(parts[-1]) - 1       # before " LED-SPEC"

        cur.setPosition(new_blk.position() + params_start)
        cur.setPosition(new_blk.position() + params_end,
                        QTextCursor.MoveMode.KeepAnchor)

        self._editor.setTextCursor(cur)
        self._editor.ensureCursorVisible()
        self._editor.setFocus()
