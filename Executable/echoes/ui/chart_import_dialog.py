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

from PySide6.QtCore    import Qt
from PySide6.QtGui     import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from core.chart_importer import GH_FRET_COLORS, LED_PRESETS, TrackConfig


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
    "QSpinBox, QComboBox, QLineEdit {"
    "  background:#2d2d2d; color:#d4d4d4;"
    "  border:1px solid #555; border-radius:3px;"
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


# ── Single track configuration block ─────────────────────────────────────────

class _TrackRow(QWidget):
    """
    Vertical block:
      ┌ header ── [x] TrackName  (N)  Preset [combo]  Short [spin]  Fade [spin]  Lead [spin]
      │ fret 1 ──  Fret 1  LEDs: [___________]  [1 ●]
      │ fret 2 ──  Fret 2  LEDs: [___________]  [2 ●]
      │   …
      └ fret 5 ──  Fret 5  LEDs: [___________]  [5 ●]
    """

    def __init__(
        self,
        track_name: str,
        note_count: int,
        track_idx:  int,
        prefs:      dict | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._track_name = track_name
        self._track_idx  = track_idx

        # ── initial values from prefs or defaults ─────────────────────────────
        default_preset = _DEFAULT_PRESETS[track_idx % len(_DEFAULT_PRESETS)]
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
        else:
            self._fret_colors = {f: QColor(*rgb) for f, rgb in GH_FRET_COLORS.items()}
            init_leds  = default_leds
            init_short = 150
            init_fade  = 80
            init_lead  = 400

        # ── outer layout ──────────────────────────────────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # ════════ header row ══════════════════════════════════════════════════
        header = QWidget()
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(10)

        self._check = QCheckBox()
        self._check.setChecked(True)
        self._check.setToolTip("Include this track in the import")
        self._check.setFixedWidth(18)
        hrow.addWidget(self._check)

        name_lbl = QLabel(track_name)
        name_lbl.setStyleSheet(
            "color:#e0e0e0; font:bold 11px 'Segoe UI'; min-width:160px;")
        hrow.addWidget(name_lbl)

        count_lbl = QLabel(f"({note_count} notes)")
        count_lbl.setStyleSheet("color:#666; font:11px 'Segoe UI'; min-width:75px;")
        hrow.addWidget(count_lbl)

        hrow.addWidget(_dim("Preset"))

        # ── LED preset combo ──────────────────────────────────────────────────
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
            "Decay rate in % per second.\n"
            "Higher = quicker fade to black  (e.g. 90 ≈ 1 s,  50 ≈ 7 s).")
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

        # ════════ per-fret columns (one horizontal group per fret) ══════════════
        frets_widget = QWidget()
        frets_widget.setStyleSheet("background:transparent;")
        frow = QHBoxLayout(frets_widget)
        frow.setContentsMargins(0, 2, 0, 2)
        frow.setSpacing(10)

        self._led_inputs: list[QLineEdit]    = []
        self._fret_btns:  list[QPushButton]  = []

        for fret in range(5):
            # Each fret is a small vertical cell: label / LED input / color btn
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
            cv.addWidget(led_inp)

            btn = QPushButton(str(fret + 1))
            btn.setFixedSize(26, 22)
            btn.clicked.connect(lambda _c, f=fret: self._pick_fret_color(f))
            self._fret_btns.append(btn)
            cv.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)

            frow.addWidget(cell)

        frow.addStretch()
        self._refresh_all_btns()
        outer.addWidget(frets_widget)

        # ── set initial preset selection (detect from loaded LED values) ──────
        self._suppress_preset_signal = False
        self._sync_preset_combo(init_leds)

        # ── wire enable/disable ───────────────────────────────────────────────
        self._track_controls: list[QWidget] = (
            [self._preset] + self._led_inputs + self._fret_btns +
            [self._short, self._fade, self._lead]
        )
        self._check.toggled.connect(self._on_toggle)

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
        """Switch to 'Custom' when the user edits any LED field directly."""
        self._suppress_preset_signal = True
        idx = self._preset.findText('Custom')
        if idx >= 0:
            self._preset.setCurrentIndex(idx)
        self._suppress_preset_signal = False

    def _sync_preset_combo(self, leds: list[str]) -> None:
        """Select the named preset whose mapping matches *leds*, else 'Custom'."""
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
            f"Fret {fret + 1}  —  "
            f"rgb({c.red()}, {c.green()}, {c.blue()})  —  click to change"
        )

    def _refresh_all_btns(self) -> None:
        for fret in range(5):
            self._refresh_fret_btn(fret)

    # ── Enable/disable ────────────────────────────────────────────────────────

    def _on_toggle(self, on: bool) -> None:
        for w in self._track_controls:
            w.setEnabled(on)

    # ── Public interface ──────────────────────────────────────────────────────

    def get_config(self) -> TrackConfig:
        return TrackConfig(
            track_name   = self._track_name,
            enabled      = self._check.isChecked(),
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

    def get_prefs(self) -> dict:
        """Serializable snapshot of current values for persistence."""
        return {
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


# ── Main dialog ───────────────────────────────────────────────────────────────

class ChartImportDialog(QDialog):
    """
    Chart import configuration dialog.

    Parameters
    ----------
    song_meta : dict   — {key: value} from the [Song] section
    tracks    : list   — [(section_name, note_count), …] in file order
    """

    def __init__(
        self,
        song_meta: dict[str, str],
        tracks: list[tuple[str, int]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import .chart")
        self.setMinimumWidth(680)
        self.setStyleSheet("background:#1e1e1e; " + _BASE)

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

        # ── Tracks ────────────────────────────────────────────────────────────
        n = len(tracks)
        tracks_lbl = QLabel(
            f"Tracks found: {n}  "
            f"{'—  configure each track below' if n else '— no playable tracks detected'}"            + ("  \u2014  max 4 can be enabled" if n > 4 else "")        )
        tracks_lbl.setStyleSheet("color:#aaa; font:bold 11px 'Segoe UI';")
        root.addWidget(tracks_lbl)

        prefs = _load_prefs()

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

        self._rows: list[_TrackRow] = []

        if tracks:
            for i, (name, count) in enumerate(tracks):
                track_prefs = prefs.get(f'track_{i}')
                row = _TrackRow(name, count, i, track_prefs, inner)
                inner_layout.addWidget(row)
                if i < len(tracks) - 1:
                    sep = QFrame()
                    sep.setFrameShape(QFrame.Shape.HLine)
                    sep.setStyleSheet("color:#3a3a3a; margin:0 6px;")
                    inner_layout.addWidget(sep)
                self._rows.append(row)
        else:
            empty = QLabel("  No playable note sections found in this file.")
            empty.setStyleSheet("color:#666; font:11px 'Segoe UI'; padding:8px;")
            inner_layout.addWidget(empty)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        # Height: ~90px per track (header + 5 fret rows)
        scroll.setMinimumHeight(min(30 + len(tracks) * 100, 500))
        root.addWidget(scroll)

        # ── Enforce 4-track maximum ───────────────────────────────────────────
        # Rows beyond the 4th start unchecked; all checkboxes report to the
        # limit-enforcer so toggling is capped at 4 enabled at any time.
        for i, row in enumerate(self._rows):
            if i >= 4:
                row._check.setChecked(False)   # fires _on_toggle → disables controls
        for row in self._rows:
            row._check.toggled.connect(self._update_track_limits)
        self._update_track_limits()  # set initial disabled/tooltip state

        # ── Buttons ───────────────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setEnabled(bool(tracks))
        ok_btn.setStyleSheet(
            "QPushButton { background:#005f9e; color:#fff; border:none;"
            " border-radius:3px; padding:4px 16px; font:11px 'Segoe UI'; }"
            "QPushButton:hover { background:#0078c8; }"
            "QPushButton:disabled { background:#3a3a3a; color:#666; }"
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(
            "QPushButton { background:#3a3a3a; color:#ccc; border:none;"
            " border-radius:3px; padding:4px 12px; font:11px 'Segoe UI'; }"
            "QPushButton:hover { background:#4a4a4a; }"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Track-limit enforcement ───────────────────────────────────────────────

    def _update_track_limits(self) -> None:
        """Disable unchecked track checkboxes once 4 are already selected."""
        enabled = sum(1 for r in self._rows if r._check.isChecked())
        at_max  = enabled >= 4
        for row in self._rows:
            if row._check.isChecked():
                row._check.setEnabled(True)
                row._check.setToolTip("Include this track in the import")
            else:
                row._check.setEnabled(not at_max)
                row._check.setToolTip(
                    "4 tracks already selected \u2014 deselect another to enable this one"
                    if at_max else
                    "Include this track in the import"
                )

    # ── Override accept to save prefs ─────────────────────────────────────────

    def accept(self) -> None:
        data = {f'track_{i}': row.get_prefs() for i, row in enumerate(self._rows)}
        _save_prefs(data)
        super().accept()

    # ── Public ───────────────────────────────────────────────────────────────

    def get_configs(self) -> list[TrackConfig]:
        """Return a TrackConfig for every row (enabled or not)."""
        return [row.get_config() for row in self._rows]
