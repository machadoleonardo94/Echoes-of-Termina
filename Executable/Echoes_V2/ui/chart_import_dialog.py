"""
Chart import configuration dialog.

Displays the [Song] metadata from a .chart file and presents one
configuration block per detected note track.  Per-track settings:

  • enabled / disabled
  • LED preset  (fills per-fret LED specs)  or manual per-fret LED spec
  • per-fret color  (RGB via color-picker)
  • short-note threshold  (ms)
  • fade decay rate  (% / second)
  • fade-lead time  (ms before a sustained note ends)

Last-used values are persisted to ~/.echoes/import_prefs.json and
restored the next time the dialog opens (per track position, 0-3).
"""
from __future__ import annotations

import json
import pathlib

from PySide6.QtCore    import QByteArray, Qt
from PySide6.QtGui     import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from core.chart_importer import GH_FRET_COLORS, LED_PRESETS, TrackConfig
from ui.script_editor   import MAX_TRACKS


# ── Preferences file ──────────────────────────────────────────────────────────

_PREFS_FILE = pathlib.Path.home() / '.echoes' / 'import_prefs.json'


def _load_prefs() -> dict:
    try:
        if _PREFS_FILE.exists():
            return json.loads(_PREFS_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _save_prefs(data: dict) -> None:
    try:
        _PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PREFS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    except Exception:
        pass


# ── Shared style constants ────────────────────────────────────────────────────

_BASE = (
    # Text boxes use a noticeably lighter shade than the dark dialog
    # background so they are easy to spot.
    "QSpinBox, QComboBox, QLineEdit {"
    "  background:#3d3d3d; color:#e8e8e8;"
    "  border:1px solid #666666; border-radius:3px;"
    "  padding:2px 5px; font:11px 'Segoe UI';"
    "}"
    "QSpinBox::up-button, QSpinBox::down-button { width:14px; }"
    "QLineEdit:focus, QComboBox:focus { border-color:#007acc; }"
    "QCheckBox { color:#d4d4d4; font:11px 'Segoe UI'; }"
    "QLabel    { color:#aaaaaa; font:11px 'Segoe UI'; }"
)

_PRESET_TIPS: dict[str, str] = {
    'Spikes': 'Outer spike LEDs 17–26  (fret 1 → bottom pair, fret 5 → top pair)',
    'Center': 'Central mask LEDs 4–13  (fret 1 → middle pair, outward)',
    'Eyes':   'Eye LEDs 27–28  (all frets → both eyes)',
    'All':    'Every LED  (*)  — fret value is ignored',
}

_DEFAULT_PRESETS = list(LED_PRESETS.keys())   # Spikes, Center, Eyes, All


# ── Utilities ─────────────────────────────────────────────────────────────────

def _dim(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color:#777; font:10px 'Segoe UI';")
    return lbl


def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#333; margin:0px;")
    return f


# ── Preset configuration block ────────────────────────────────────────────────

class _PresetRow(QWidget):
    """
    One of six persistent preset slots.

    Each row stores a reusable LED / colour / timing configuration that is
    independent of any particular source file.  Two extra dropdowns let the
    user wire it to the import at hand:

      Source  — which chart track feeds this preset  ("— None —" = skip)
      → Slot  — which project slot receives the output

    The LED / colour / timing settings are saved by preset index so they
    survive across imports of different files.
    """

    def __init__(
        self,
        preset_idx:       int,
        available_tracks: list[str],
        slot_names:       list[str],
        prefs:            dict | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._preset_idx = preset_idx

        # ── initial values from prefs or defaults ─────────────────────────────
        default_preset = _DEFAULT_PRESETS[preset_idx % len(_DEFAULT_PRESETS)]
        default_leds   = [LED_PRESETS[default_preset][f] for f in range(5)]

        if prefs:
            raw_colors = prefs.get('fret_colors', [])
            self._fret_colors: dict[int, QColor] = {
                f: QColor(*raw_colors[f]) if f < len(raw_colors) else QColor(*GH_FRET_COLORS[f])
                for f in range(5)
            }
            saved_leds = prefs.get('fret_leds', default_leds)
            init_leds  = [str(saved_leds[f]) if f < len(saved_leds) else default_leds[f]
                          for f in range(5)]
            init_short = prefs.get('short_ms',     150)
            init_fade  = prefs.get('fade_percent',  80)
            init_lead  = prefs.get('fade_lead_ms', 400)
            init_label = str(prefs.get('label', f'Preset {preset_idx + 1}'))
        else:
            self._fret_colors = {f: QColor(*rgb) for f, rgb in GH_FRET_COLORS.items()}
            init_leds  = default_leds
            init_short = 150
            init_fade  = 80
            init_lead  = 400
            init_label = f'Preset {preset_idx + 1}'

        # ── outer layout ──────────────────────────────────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # ════════ header row ══════════════════════════════════════════════════
        header = QWidget()
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(10)

        # ── editable preset name (persisted, used by Export / Import) ──────
        self._label = QLineEdit(init_label)
        self._label.setFixedWidth(110)
        self._label.setToolTip(
            "Preset name \u2014 shown on this row and saved with the preset\n"
            "settings (Export / Import Presets).")
        self._label.setStyleSheet(
            "QLineEdit { background:#3d3d3d; color:#e8e8e8; "
            "border:1px solid #666666; border-radius:3px; "
            "padding:1px 5px; font:bold 11px 'Segoe UI'; }"
            "QLineEdit:focus { border-color:#007acc; }"
        )
        hrow.addWidget(self._label)

        hrow.addWidget(_dim("Source"))
        self._source_combo = QComboBox()
        self._source_combo.setFixedWidth(180)
        self._source_combo.setToolTip(
            "Which chart track feeds this preset.\n"
            "'\u2014 None \u2014' skips this preset row.")
        self._source_combo.addItem("\u2014 None \u2014", None)
        for t in available_tracks:
            self._source_combo.addItem(t, t)
        # Auto-assign: preset N defaults to the N-th available track
        if preset_idx < len(available_tracks):
            self._source_combo.setCurrentIndex(preset_idx + 1)  # +1 for None item
        hrow.addWidget(self._source_combo)

        hrow.addWidget(_dim("\u2192 Slot"))
        self._slot_combo = QComboBox()
        self._slot_combo.setFixedWidth(140)
        self._slot_combo.setToolTip("Project slot this preset's output is written to.")
        for s in slot_names:
            self._slot_combo.addItem(s)
        if preset_idx < len(slot_names):
            self._slot_combo.setCurrentIndex(preset_idx)
        hrow.addWidget(self._slot_combo)

        hrow.addWidget(_dim("Preset"))
        self._preset = QComboBox()
        self._preset.setFixedWidth(80)
        for pname, tip in _PRESET_TIPS.items():
            self._preset.addItem(pname)
            self._preset.setItemData(
                self._preset.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self._preset.addItem('Custom')
        self._preset.setItemData(
            self._preset.count() - 1,
            "Fret LED specs have been edited manually",
            Qt.ItemDataRole.ToolTipRole,
        )
        self._preset.currentTextChanged.connect(self._on_preset_changed)
        hrow.addWidget(self._preset)

        hrow.addWidget(_dim("Short"))
        self._short = QSpinBox()
        self._short.setRange(0, 2000); self._short.setValue(init_short)
        self._short.setSuffix(" ms"); self._short.setFixedWidth(78)
        self._short.setToolTip(
            "Notes shorter than this are rendered as a single FADE flash.\n"
            "Longer notes get SET at start + FADE near end.")
        hrow.addWidget(self._short)

        hrow.addWidget(_dim("Fade"))
        self._fade = QSpinBox()
        self._fade.setRange(1, 99); self._fade.setValue(init_fade)
        self._fade.setSuffix("%"); self._fade.setFixedWidth(62)
        self._fade.setToolTip(
            "Minimum decay rate in % per second.\n"
            "The importer raises it per note so the LED reaches black when\n"
            "the note ends (brightness matches the note), up to 99 %/s.\n"
            "e.g. 90 \u2248 1 s,  50 \u2248 7 s.")
        hrow.addWidget(self._fade)

        hrow.addWidget(_dim("Lead"))
        self._lead = QSpinBox()
        self._lead.setRange(0, 2000); self._lead.setValue(init_lead)
        self._lead.setSuffix(" ms"); self._lead.setFixedWidth(78)
        self._lead.setToolTip(
            "How early before a sustained note ends to issue the FADE command.")
        hrow.addWidget(self._lead)

        hrow.addStretch()
        outer.addWidget(header)

        # ════════ per-fret columns ═══════════════════════════════════════════
        frets_widget = QWidget()
        frets_widget.setStyleSheet("background:transparent;")
        frow = QHBoxLayout(frets_widget)
        frow.setContentsMargins(0, 2, 0, 2)
        frow.setSpacing(10)

        self._led_inputs: list[QLineEdit]   = []
        self._fret_btns:  list[QPushButton] = []

        for fret in range(5):
            cell = QWidget()
            cell.setStyleSheet("background:transparent;")
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(3)

            fret_lbl = QLabel(f"Fret {fret + 1}")
            fret_lbl.setStyleSheet("color:#777; font:9px 'Segoe UI';")
            fret_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            cv.addWidget(fret_lbl)

            led_inp = QLineEdit(init_leds[fret])
            led_inp.setFixedWidth(72)
            led_inp.setPlaceholderText("e.g. 17,26")
            led_inp.setToolTip(
                "LED specification for this fret.\n"
                "Single: 5   Range: 9-16   List: 17,26   All: *   Empty: skip")
            led_inp.textEdited.connect(self._on_led_manually_edited)
            self._led_inputs.append(led_inp)

            btn = QPushButton(str(fret + 1))
            btn.setFixedSize(26, 22)
            btn.clicked.connect(lambda _c, f=fret: self._pick_fret_color(f))
            self._fret_btns.append(btn)

            # Colour box on the left, LED text box on the right
            fret_row = QHBoxLayout()
            fret_row.setContentsMargins(0, 0, 0, 0)
            fret_row.setSpacing(4)
            fret_row.addWidget(btn)
            fret_row.addWidget(led_inp)
            cv.addLayout(fret_row)

            frow.addWidget(cell)

        frow.addStretch()
        self._refresh_all_btns()
        outer.addWidget(frets_widget)

        # initial preset selection
        self._suppress_preset_signal = False
        self._sync_preset_combo(init_leds)

        # Controls that follow source selection
        self._cfg_controls: list[QWidget] = (
            [self._slot_combo, self._preset] + self._led_inputs + self._fret_btns +
            [self._short, self._fade, self._lead]
        )
        self._source_combo.currentIndexChanged.connect(self._on_source_changed)
        self._on_source_changed()   # set initial enabled state

    # ── Source change ─────────────────────────────────────────────────────────

    def _on_source_changed(self) -> None:
        """Enable config controls only when a real source track is selected."""
        enabled = self._source_combo.currentData() is not None
        for w in self._cfg_controls:
            w.setEnabled(enabled)

    # ── Preset / LED sync ─────────────────────────────────────────────────────

    def _on_preset_changed(self, preset_name: str) -> None:
        if self._suppress_preset_signal:
            return
        if preset_name in LED_PRESETS:
            fmap = LED_PRESETS[preset_name]
            for fret, inp in enumerate(self._led_inputs):
                inp.blockSignals(True)
                inp.setText(fmap.get(fret, ''))
                inp.blockSignals(False)
        tip = _PRESET_TIPS.get(preset_name, 'Manually configured LED mapping')
        self._preset.setToolTip(tip)

    def _on_led_manually_edited(self) -> None:
        self._suppress_preset_signal = True
        idx = self._preset.findText('Custom')
        if idx >= 0:
            self._preset.setCurrentIndex(idx)
        self._suppress_preset_signal = False

    def _sync_preset_combo(self, leds: list[str]) -> None:
        self._suppress_preset_signal = True
        for pname, fmap in LED_PRESETS.items():
            if all(fmap.get(f, '') == leds[f] for f in range(5)):
                idx = self._preset.findText(pname)
                if idx >= 0:
                    self._preset.setCurrentIndex(idx)
                    self._preset.setToolTip(_PRESET_TIPS.get(pname, ''))
                self._suppress_preset_signal = False
                return
        idx = self._preset.findText('Custom')
        if idx >= 0:
            self._preset.setCurrentIndex(idx)
        self._suppress_preset_signal = False

    # ── Color buttons ─────────────────────────────────────────────────────────

    def _pick_fret_color(self, fret: int) -> None:
        c = QColorDialog.getColor(
            self._fret_colors[fret], self,
            f"Fret {fret + 1}  Color",
        )
        if c.isValid():
            self._fret_colors[fret] = c
            self._refresh_fret_btn(fret)

    def _refresh_fret_btn(self, fret: int) -> None:
        c   = self._fret_colors[fret]
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        txt = "#000" if lum > 140 else "#fff"
        btn = self._fret_btns[fret]
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background:{c.name()}; border:1px solid #555;"
            f"  border-radius:3px; color:{txt}; font:bold 9px 'Segoe UI';"
            f"}}"
            f"QPushButton:hover {{ border-color:#007acc; }}"
        )
        btn.setToolTip(
            f"Fret {fret + 1}  \u2014  "
            f"rgb({c.red()}, {c.green()}, {c.blue()})  \u2014  click to change"
        )

    def _refresh_all_btns(self) -> None:
        for fret in range(5):
            self._refresh_fret_btn(fret)

    # ── Public interface ──────────────────────────────────────────────────────

    def get_config(self) -> 'TrackConfig | None':
        """Return a TrackConfig if a source is selected, else None (preset disabled)."""
        source = self._source_combo.currentData()
        if source is None:
            return None
        return TrackConfig(
            track_name   = source,
            enabled      = True,
            fret_colors  = {
                f: (c.red(), c.green(), c.blue())
                for f, c in self._fret_colors.items()
            },
            fret_leds    = {
                f: inp.text().strip()
                for f, inp in enumerate(self._led_inputs)
            },
            short_ms     = self._short.value(),
            fade_percent = self._fade.value(),
            fade_lead_ms = self._lead.value(),
        )

    def get_slot_target(self) -> str:
        return self._slot_combo.currentText()

    def get_prefs(self) -> dict:
        """Serializable snapshot of LED / colour / timing settings for persistence."""
        return {
            'label':        self._label.text().strip() or f'Preset {self._preset_idx + 1}',
            'fret_colors': [
                [self._fret_colors[f].red(),
                 self._fret_colors[f].green(),
                 self._fret_colors[f].blue()]
                for f in range(5)
            ],
            'fret_leds':    [inp.text().strip() for inp in self._led_inputs],
            'short_ms':     self._short.value(),
            'fade_percent': self._fade.value(),
            'fade_lead_ms': self._lead.value(),
        }

    def apply_prefs(self, prefs: dict) -> None:
        """Restore a saved preset dict (label, colours, LED specs, timing)."""
        if not isinstance(prefs, dict):
            return
        raw_colors = prefs.get('fret_colors', [])
        if raw_colors:
            for f in range(5):
                if f < len(raw_colors):
                    try:
                        self._fret_colors[f] = QColor(*raw_colors[f])
                    except (TypeError, ValueError):
                        pass
        saved_leds = prefs.get('fret_leds')
        if saved_leds:
            for f, inp in enumerate(self._led_inputs):
                if f < len(saved_leds):
                    inp.setText(str(saved_leds[f]))
        try:
            self._short.setValue(int(prefs.get('short_ms', self._short.value())))
            self._fade.setValue(int(prefs.get('fade_percent', self._fade.value())))
            self._lead.setValue(int(prefs.get('fade_lead_ms', self._lead.value())))
        except (TypeError, ValueError):
            pass
        label = str(prefs.get('label', '')).strip()
        if label:
            self._label.setText(label)
        self._refresh_all_btns()
        self._sync_preset_combo([inp.text().strip() for inp in self._led_inputs])


# ── Main dialog ───────────────────────────────────────────────────────────────

class ChartImportDialog(QDialog):
    """
    Import configuration dialog (used for both .chart and .mid files).

    Shows the [Song] metadata, a metadata-import checkbox, and one
    *_PresetRow* per project slot (always MAX_TRACKS = 6 rows).  Each row
    stores a reusable LED / colour / timing configuration and lets the user
    pick the source chart track and target project slot independently.
    """

    def __init__(
        self,
        song_meta:  dict[str, str],
        tracks:     list[tuple[str, int]],
        parent=None,
        slot_names: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Tracks")
        self.setMinimumWidth(720)
        self.setStyleSheet("background:#1e1e1e; " + _BASE)

        if not slot_names:
            slot_names = [f"Slot {i + 1}" for i in range(MAX_TRACKS)]

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(12, 12, 12, 12)

        # ── [Song] info ───────────────────────────────────────────────────────
        song_lbl = QLabel("[Song]")
        song_lbl.setStyleSheet("color:#aaa; font:bold 11px 'Segoe UI';")
        root.addWidget(song_lbl)

        info = QPlainTextEdit()
        info.setReadOnly(True)
        info.setFixedHeight(110)
        info.setStyleSheet(
            "QPlainTextEdit {"
            "  background:#252526; color:#ce9178;"
            "  border:1px solid #3a3a3a; border-radius:3px;"
            "  font:11px 'Cascadia Code','Consolas',monospace; padding:4px;"
            "}"
        )
        if song_meta:
            info.setPlainText('\n'.join(f"  {k} = {v}" for k, v in song_meta.items()))
        else:
            info.setPlainText("  (no [Song] metadata found in this file)")
            info.setStyleSheet(
                "QPlainTextEdit {"
                "  background:#252526; color:#666;"
                "  border:1px solid #3a3a3a; border-radius:3px;"
                "  font:11px 'Cascadia Code','Consolas',monospace; padding:4px;"
                "}"
            )
        root.addWidget(info)

        # ── Metadata checkbox ─────────────────────────────────────────────────
        self._meta_chk = QCheckBox(
            "Import song metadata  (title, BPM, time signature, album, year\u2026)")
        self._meta_chk.setChecked(True)
        root.addWidget(self._meta_chk)

        # ── Info label ────────────────────────────────────────────────────────
        n = len(tracks)
        available_tracks = [name for name, _ in tracks]
        info_lbl = QLabel(
            f"Tracks found in file: {n}  \u2014  "
            "assign source tracks to the 6 preset slots below"
            if n else
            "No playable tracks detected in this file."
        )
        info_lbl.setStyleSheet("color:#aaa; font:bold 11px 'Segoe UI';")
        root.addWidget(info_lbl)

        # ── Preset rows (always MAX_TRACKS) ───────────────────────────────────
        prefs = _load_prefs()

        # ── remember the dialog window size across sessions ─────────────────
        saved_geo = prefs.get('window_geometry')
        if isinstance(saved_geo, str) and saved_geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(saved_geo.encode('ascii')))
            except Exception:
                pass

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border:1px solid #3a3a3a; border-radius:3px; }"
            "QScrollBar:vertical { background:#252526; width:10px; }"
            "QScrollBar::handle:vertical { background:#555; border-radius:4px; }"
        )

        inner = QWidget()
        inner.setStyleSheet("background:#252526;")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 4, 0, 4)
        inner_layout.setSpacing(0)

        self._rows: list[_PresetRow] = []
        for i in range(MAX_TRACKS):
            row = _PresetRow(i, available_tracks, slot_names, prefs.get(f'track_{i}'), inner)
            inner_layout.addWidget(row)
            if i < MAX_TRACKS - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet("color:#3a3a3a; margin:0 6px;")
                inner_layout.addWidget(sep)
            self._rows.append(row)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        scroll.setMinimumHeight(min(30 + MAX_TRACKS * 100, 540))
        root.addWidget(scroll)

        # ── Buttons ───────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setStyleSheet(
            "QPushButton { background:#005f9e; color:#fff; border:none;"
            " border-radius:3px; padding:4px 16px; font:11px 'Segoe UI'; }"
            "QPushButton:hover { background:#0078c8; }"
        )

        # ── Export / Import preset settings ─────────────────────────────────
        _preset_btn_style = (
            "QPushButton { background:#3a3a3a; color:#ccc; border:none;"
            " border-radius:3px; padding:4px 10px; font:11px 'Segoe UI'; }"
            "QPushButton:hover { background:#4a4a4a; }"
        )
        btn_export = buttons.addButton(
            "Export Presets\u2026", QDialogButtonBox.ButtonRole.ActionRole)
        btn_export.setStyleSheet(_preset_btn_style)
        btn_export.setToolTip(
            "Save the current preset settings (labels, colours, LED specs,\n"
            "timing) to a .json file.")
        btn_export.clicked.connect(self._export_presets)

        btn_import = buttons.addButton(
            "Import Presets\u2026", QDialogButtonBox.ButtonRole.ActionRole)
        btn_import.setStyleSheet(_preset_btn_style)
        btn_import.setToolTip(
            "Load preset settings from a previously exported .json file.")
        btn_import.clicked.connect(self._import_presets)

        buttons.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(
            "QPushButton { background:#3a3a3a; color:#ccc; border:none;"
            " border-radius:3px; padding:4px 12px; font:11px 'Segoe UI'; }"
            "QPushButton:hover { background:#4a4a4a; }"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Save prefs on accept ──────────────────────────────────────────────────

    def _remember_geometry(self, data: dict) -> None:
        """Store the dialog window size in the prefs metadata."""
        data['window_geometry'] = bytes(
            self.saveGeometry().toBase64()).decode('ascii')

    def accept(self) -> None:
        data = _load_prefs()
        data.update({f'track_{i}': row.get_prefs()
                     for i, row in enumerate(self._rows)})
        self._remember_geometry(data)
        _save_prefs(data)
        super().accept()

    def reject(self) -> None:
        data = _load_prefs()
        self._remember_geometry(data)
        _save_prefs(data)
        super().reject()

    # ── Export / Import preset settings ───────────────────────────────────────

    def _export_presets(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Preset Settings", "echoes_presets.json",
            "Echoes presets (*.json);;All Files (*)")
        if not path:
            return
        data = {f'track_{i}': row.get_prefs()
                for i, row in enumerate(self._rows)}
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        QMessageBox.information(
            self, "Presets exported",
            f"Preset settings saved to:\n{path}")

    def _import_presets(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Preset Settings", "",
            "Echoes presets (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "Import failed",
                                "Not a valid presets file.")
            return
        n = 0
        for i, row in enumerate(self._rows):
            prefs = data.get(f'track_{i}')
            if isinstance(prefs, dict):
                row.apply_prefs(prefs)
                n += 1
        # Persist immediately so the import survives a dialog cancel
        saved = _load_prefs()
        saved.update({f'track_{i}': row.get_prefs()
                      for i, row in enumerate(self._rows)})
        _save_prefs(saved)
        QMessageBox.information(
            self, "Presets imported",
            f"Applied settings to {n} preset row(s) from:\n{path}")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_preset_assignments(self) -> list[tuple['TrackConfig', str]]:
        """
        Return ``[(TrackConfig, slot_name)]`` for each enabled preset row.

        A row is enabled when its Source dropdown is not "— None —".
        The same source track may appear in multiple rows (different LED configs).
        """
        result = []
        for row in self._rows:
            cfg = row.get_config()
            if cfg is not None:
                result.append((cfg, row.get_slot_target()))
        return result

    def wants_metadata(self) -> bool:
        """True if the user wants to overwrite the project's song metadata."""
        return self._meta_chk.isChecked()
