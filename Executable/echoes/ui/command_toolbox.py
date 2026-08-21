"""
Command Toolbox — one-row macro builder for inserting script commands.

Layout (left → right):
  [ADD]  [0:05.45]  [SET/FADE/RAMP ▾]  <colour-coded parameter widgets>

The timestamp label tracks the audio position live; clicking ADD snapshots
that instant and emits a fully-formed script line via `command_ready`.
"""
from __future__ import annotations

from PySide6.QtCore    import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QStackedWidget, QWidget,
)

from core.audio_player  import AudioPlayer
from core.script_engine import format_ms


# ── Widget factories ─────────────────────────────────────────────────────────

def _spin(lo: int, hi: int, val: int,
          bg: str, fg: str, tip: str = "", w: int = 52) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(val)
    s.setFixedSize(w, 24)
    s.setAlignment(Qt.AlignmentFlag.AlignCenter)
    s.setToolTip(tip)
    s.setStyleSheet(
        f"QSpinBox {{ background:{bg}; color:{fg}; border:1px solid #404040; "
        f"border-radius:3px; padding:0 2px; }}"
        f"QSpinBox::up-button, QSpinBox::down-button {{ width:12px; }}"
    )
    return s


def _line(val: str, bg: str, fg: str, tip: str = "", w: int = 54) -> QLineEdit:
    e = QLineEdit(val)
    e.setFixedSize(w, 24)
    e.setAlignment(Qt.AlignmentFlag.AlignCenter)
    e.setToolTip(tip)
    e.setStyleSheet(
        f"QLineEdit {{ background:{bg}; color:{fg}; border:1px solid #404040; "
        f"border-radius:3px; padding:0 4px; }}"
    )
    return e


def _sep_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color:#505050; font:9px 'Segoe UI'; margin:0 1px;")
    return lbl


# ── Per-command parameter rows ────────────────────────────────────────────────

class _ParamWidget(QWidget):
    """Thin wrapper so a row of widgets sits inside QStackedWidget cleanly."""
    def __init__(self, children: list, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        for c in children:
            row.addWidget(c)
        row.addStretch(1)


# ── Main widget ───────────────────────────────────────────────────────────────

class CommandToolbox(QWidget):
    """
    Compact single-row command builder.
    Emits `command_ready(str)` with a fully-formatted script line.
    """

    command_ready = Signal(str)

    def __init__(self, player: AudioPlayer, parent=None) -> None:
        super().__init__(parent)
        self._player = player
        self._ts_ms  = 0
        self._build()
        self._wire()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setFixedHeight(38)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(6)

        # ADD button
        self._btn_add = QPushButton("ADD")
        self._btn_add.setFixedSize(44, 26)
        self._btn_add.setStyleSheet(
            "QPushButton { background:#005f5f; color:#ccffff; border:none; "
            "border-radius:3px; font:bold 11px 'Segoe UI'; }"
            "QPushButton:hover   { background:#007f7f; }"
            "QPushButton:pressed { background:#003f3f; }"
        )
        self._btn_add.setToolTip("Insert command at current playback position")

        # Live timestamp label
        self._ts_label = QLabel("0:00.00")
        self._ts_label.setFixedSize(72, 26)
        self._ts_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ts_label.setStyleSheet(
            "color:#4ec9b0; font:bold 11px 'Consolas'; "
            "background:#1a2a28; border:1px solid #2f4f4a; "
            "border-radius:3px;"
        )
        self._ts_label.setToolTip("Current playback position — captured on ADD")

        # Command dropdown
        self._cmd_combo = QComboBox()
        self._cmd_combo.addItems(["SET", "FADE", "RAMP"])
        self._cmd_combo.setCurrentIndex(1)   # FADE is the most common
        self._cmd_combo.setFixedSize(68, 26)
        self._cmd_combo.setStyleSheet(
            "QComboBox { background:#2a2a2a; color:#dcdcaa; "
            "border:1px solid #404040; border-radius:3px; "
            "padding:2px 6px; font:bold 11px 'Consolas'; }"
            "QComboBox::drop-down { border:none; width:16px; }"
            "QComboBox QAbstractItemView { background:#2a2a2a; color:#dcdcaa; "
            "selection-background-color:#005f5f; outline:none; }"
        )

        # ── Parameter stacks ──────────────────────────────────────────────────
        self._stack = QStackedWidget()

        # ── SET  :  R G B  leds
        self._set_r   = _spin(0, 255, 255, "#3a1818", "#e07878", "Red (0-255)")
        self._set_g   = _spin(0, 255,   0, "#183218", "#70d870", "Green (0-255)")
        self._set_b   = _spin(0, 255,   0, "#18183a", "#7878e0", "Blue (0-255)")
        self._set_led = _line("*", "#18252e", "#4fc1ff", "LED spec  e.g. *  1-2  1,3,5")
        # stack index 0
        self._stack.addWidget(_ParamWidget([
            self._set_r, self._set_g, self._set_b,
            _sep_label("leds"), self._set_led,
        ]))

        # ── FADE  :  R G B  rate(%)  leds
        self._fad_r   = _spin(0, 255, 255, "#3a1818", "#e07878", "Red (0-255)")
        self._fad_g   = _spin(0, 255,   0, "#183218", "#70d870", "Green (0-255)")
        self._fad_b   = _spin(0, 255,   0, "#18183a", "#7878e0", "Blue (0-255)")
        self._fad_rt  = _spin(1,  99,  50, "#252525", "#ce9178",
                              "Fade rate — % brightness lost per second (1-99)")
        self._fad_led = _line("*", "#18252e", "#4fc1ff", "LED spec  e.g. *  1-2  1,3,5")
        # stack index 1
        self._stack.addWidget(_ParamWidget([
            self._fad_r, self._fad_g, self._fad_b,
            _sep_label("%"), self._fad_rt,
            _sep_label("leds"), self._fad_led,
        ]))

        # ── RAMP  :  dur  R0 G0 B0  R1 G1 B1  leds
        self._rmp_dur = _line("0:03", "#18302e", "#4ec9b0",
                              "Ramp duration  M:SS  e.g. 0:03", w=54)
        self._rmp_r0  = _spin(0, 255,   0, "#3a1818", "#e07878", "Start red")
        self._rmp_g0  = _spin(0, 255,   0, "#183218", "#70d870", "Start green")
        self._rmp_b0  = _spin(0, 255, 255, "#18183a", "#7878e0", "Start blue")
        self._rmp_r1  = _spin(0, 255, 255, "#3a2230", "#d080c0", "End red")
        self._rmp_g1  = _spin(0, 255,  80, "#20203a", "#9090d8", "End green")
        self._rmp_b1  = _spin(0, 255,   0, "#221838", "#b07ed8", "End blue")
        self._rmp_led = _line("*", "#18252e", "#4fc1ff", "LED spec  e.g. *  1-2  1,3,5")
        # stack index 2
        self._stack.addWidget(_ParamWidget([
            _sep_label("dur"), self._rmp_dur,
            _sep_label("from"), self._rmp_r0, self._rmp_g0, self._rmp_b0,
            _sep_label("→"), self._rmp_r1, self._rmp_g1, self._rmp_b1,
            _sep_label("leds"), self._rmp_led,
        ]))

        # Default to FADE (index 1)
        self._stack.setCurrentIndex(1)

        row.addWidget(self._btn_add)
        row.addWidget(self._ts_label)
        row.addWidget(self._cmd_combo)
        row.addWidget(self._stack, stretch=1)

        # Target slot dropdown  (populated externally via set_track_names)
        _sep = QLabel("|")
        _sep.setStyleSheet("color:#404040; font:14px 'Segoe UI';")
        row.addWidget(_sep)

        _slot_lbl = QLabel("→")
        _slot_lbl.setStyleSheet("color:#666666; font:11px 'Segoe UI';")
        row.addWidget(_slot_lbl)

        self._slot_combo = QComboBox()
        self._slot_combo.setFixedSize(140, 26)
        self._slot_combo.setToolTip("Target slot — the ADD button inserts into this track")
        self._slot_combo.setStyleSheet(
            "QComboBox { background:#2a2a2a; color:#9cdcfe; "
            "border:1px solid #404040; border-radius:3px; "
            "padding:2px 6px; font:11px 'Segoe UI'; }"
            "QComboBox::drop-down { border:none; width:14px; }"
            "QComboBox QAbstractItemView { background:#2a2a2a; color:#9cdcfe; "
            "selection-background-color:#005f5f; }"
        )
        row.addWidget(self._slot_combo)

    # ── Wiring ────────────────────────────────────────────────────────────────

    def _wire(self) -> None:
        self._player.position_changed.connect(self._on_position)
        self._cmd_combo.currentIndexChanged.connect(self._stack.setCurrentIndex)
        self._btn_add.clicked.connect(self._on_add)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_position(self, ms: int) -> None:
        self._ts_ms = ms
        self._ts_label.setText(format_ms(ms))

    def _on_add(self) -> None:
        ts  = format_ms(self._ts_ms)
        cmd = self._cmd_combo.currentText()

        if cmd == "SET":
            line = (f"{ts} SET "
                    f"{self._set_r.value()} "
                    f"{self._set_g.value()} "
                    f"{self._set_b.value()} "
                    f"{self._set_led.text().strip() or '*'}")

        elif cmd == "FADE":
            line = (f"{ts} FADE "
                    f"{self._fad_r.value()} "
                    f"{self._fad_g.value()} "
                    f"{self._fad_b.value()} "
                    f"{self._fad_rt.value()} "
                    f"{self._fad_led.text().strip() or '*'}")

        else:  # RAMP
            dur = self._rmp_dur.text().strip() or "0:03"
            line = (f"{ts} RAMP {dur} "
                    f"{self._rmp_r0.value()} "
                    f"{self._rmp_g0.value()} "
                    f"{self._rmp_b0.value()} "
                    f"{self._rmp_r1.value()} "
                    f"{self._rmp_g1.value()} "
                    f"{self._rmp_b1.value()} "
                    f"{self._rmp_led.text().strip() or '*'}")

        self.command_ready.emit(line)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_track_names(self, names: list[str]) -> None:
        """Repopulate the target slot dropdown, preserving the current selection."""
        current = self._slot_combo.currentText()
        self._slot_combo.blockSignals(True)
        self._slot_combo.clear()
        self._slot_combo.addItems(names)
        idx = self._slot_combo.findText(current)
        if idx >= 0:
            self._slot_combo.setCurrentIndex(idx)
        self._slot_combo.blockSignals(False)

    def current_target_track(self) -> str:
        """Return the currently selected target slot name."""
        return self._slot_combo.currentText()
