"""
Command Toolbox — one-row macro builder for inserting script commands.

Layout (left → right):
  [ADD]  [0:05.45]  [SET/FADE/RAMP ▾]  <colour-coded parameter widgets>
  [→ target slot]  |  MACRO  [1][2][3][4][5][6][7][8][9]

The timestamp label tracks the audio position live; clicking ADD snapshots
that instant and emits a fully-formed script line via `command_ready`.

The nine numbered buttons on the right fire *macros* (see core/macros.py):
each macro body is a block of commands plus optional `SKIP x/y` timing
steps.  Triggering a macro emits `macro_triggered(int)`; the owner inserts
the resulting timed lines into the target track.  Right-clicking a button
emits `macro_edit_requested(int)` so the owner can open the editor.
"""
from __future__ import annotations

from PySide6.QtCore    import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QStackedWidget, QWidget,
)

from core.audio_player  import AudioPlayer
from core.macros       import stats
from core.script_engine import format_ms


# ── Widget factories ─────────────────────────────────────────────────────────

def _spin(lo: int, hi: int, val: int,
          bg: str, fg: str, tip: str = "", w: int = 52) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(val)
    # Shrinkable: keeps *w* when there is room, compresses when the pane is
    # narrow — so the toolbox does not force the right pane wide and the
    # script editor can be widened.
    s.setMinimumSize(26, 24)
    s.setMaximumSize(w, 24)
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
    e.setMinimumSize(40, 24)
    e.setMaximumSize(w, 24)
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
    Emits `command_ready(str)` with a fully-formatted script line, plus
    `macro_triggered(int)` / `macro_edit_requested(int)` for macro buttons.
    """

    command_ready      = Signal(str)
    macro_triggered    = Signal(int)   # index 1..9 — fire this macro now
    macro_edit_requested = Signal(int) # index 1..9 — open the macro editor
    numpad_changed     = Signal(bool)  # numpad hotkeys enabled / disabled

    def __init__(self, player: AudioPlayer, parent=None) -> None:
        super().__init__(parent)
        self._player = player
        self._ts_ms  = 0
        self._macros: list[dict] = []   # filled externally via set_macros
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
        self._ts_label.setMinimumSize(56, 26)
        self._ts_label.setMaximumSize(72, 26)
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
        self._slot_combo.setMinimumSize(90, 26)
        self._slot_combo.setMaximumSize(140, 26)
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

        # ── Macro buttons 1-9 (right end of the row) ────────────────────────
        sep = QLabel("|")
        sep.setStyleSheet("color:#404040; font:14px 'Segoe UI';")
        row.addWidget(sep)

        mac_lbl = QLabel("MACRO")
        mac_lbl.setStyleSheet(
            "color:#b58900; font:bold 9px 'Segoe UI'; margin:0 2px;")
        mac_lbl.setToolTip(
            "Macro buttons \u2014 insert a stored block of commands at the\n"
            "current time.  SKIP x/y lines spread the commands across the\n"
            "bar grid.  Hotkeys: 1-9 (not while editing text).\n"
            "Right-click a button to edit its macro.")
        row.addWidget(mac_lbl)

        self._macro_btns: list[QPushButton] = []
        for i in range(1, 10):
            b = QPushButton(str(i))
            b.setFixedSize(24, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _pos, n=i: self.macro_edit_requested.emit(n))
            b.setToolTip(self._macro_tooltip(i, None))
            self._apply_macro_style(b, has_content=False)
            row.addWidget(b)
            self._macro_btns.append(b)

        # ── Numpad toggle — main-row 1-9 always fire; numpad 1-9 only when on
        self._btn_num = QPushButton("NUM")
        self._btn_num.setCheckable(True)
        self._btn_num.setChecked(True)
        self._btn_num.setFixedSize(40, 24)
        self._btn_num.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_num.setToolTip(
            "Numpad macro hotkeys\n"
            "ON  — the numpad digit keys 1-9 also fire macros\n"
            "OFF — only the main-row 1-9 keys fire macros\n"
            "(never while typing in a text field)")
        self._apply_num_style(True)
        row.addSpacing(4)
        row.addWidget(self._btn_num)
        self._btn_num.toggled.connect(self._on_num_toggled)

    # ── Numpad toggle helpers ────────────────────────────────────────────────

    def _apply_num_style(self, on: bool) -> None:
        """Blue highlight while numpad hotkeys are on, neutral when off."""
        if on:
            style = (
                "QPushButton { background:#0e639c; color:#ffffff; "
                "border:1px solid #1177bb; border-radius:3px; "
                "font:bold 9px 'Segoe UI'; padding:0; }"
                "QPushButton:hover { background:#1177bb; }"
                "QPushButton:pressed { background:#0a4f7a; }"
            )
        else:
            style = (
                "QPushButton { background:#262626; color:#888888; "
                "border:1px solid #3f3f46; border-radius:3px; "
                "font:bold 9px 'Segoe UI'; padding:0; }"
                "QPushButton:hover { background:#3a3a3a; color:#cccccc; }"
                "QPushButton:pressed { background:#161616; }"
            )
        self._btn_num.setStyleSheet(style)

    def _on_num_toggled(self, on: bool) -> None:
        self._apply_num_style(on)
        self.numpad_changed.emit(on)

    # ── Macro button helpers ────────────────────────────────────────────────

    @staticmethod
    def _macro_tooltip(index: int, macro: dict | None) -> str:
        """Tooltip for macro button *index* (1-9) given its stored dict."""
        head = f"Macro {index}"
        if not macro:
            return (f"{head} \u2014 empty\nRight-click to edit \u00b7 "
                    f"hotkey: {index}")
        name = (macro.get('name') or '').strip()
        body = (macro.get('content') or '').strip()
        if name and name != f"Macro {index}":
            head += f"  \u2014  {name}"
        parts = [head]
        lines = [ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith('#')]
        for ln in lines[:3]:
            parts.append("    " + ln)
        if len(lines) > 3:
            parts.append(f"    \u2026 +{len(lines) - 3} more")
        n_cmd, n_skip = stats(body)
        parts.append(f"{n_cmd} command{'s' if n_cmd != 1 else ''}"
                     f" \u00b7 {n_skip} skip{'s' if n_skip != 1 else ''}"
                     f"  \u00b7 hotkey: {index}  \u00b7 "
                     f"right-click to edit")
        return "\n".join(parts)

    @staticmethod
    def _apply_macro_style(btn: QPushButton, has_content: bool) -> None:
        """Number styling: amber when the macro has content, grey when empty."""
        if has_content:
            base, hover, border = "#b58900", "#d7a92e", "#d7a92e"
        else:
            base, hover, border = "#6a6a6a", "#9a9a9a", "#555555"
        btn.setStyleSheet(
            f"QPushButton {{ background:#262626; color:{base}; "
            f"border:1px solid {border}; border-radius:3px; "
            f"font:bold 10px 'Consolas'; padding:0; }}"
            f"QPushButton:hover {{ background:#3a3a3a; color:{hover}; "
            f"border-color:{border}; }}"
            f"QPushButton:pressed {{ background:#161616; }}"
        )

    # ── Wiring ────────────────────────────────────────────────────────────────

    def _wire(self) -> None:
        self._player.position_changed.connect(self._on_position)
        self._cmd_combo.currentIndexChanged.connect(self._stack.setCurrentIndex)
        self._btn_add.clicked.connect(self._on_add)
        for i, btn in enumerate(self._macro_btns, start=1):
            btn.clicked.connect(lambda _=False, n=i: self.macro_triggered.emit(n))

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

    def set_track_names(self, names: list[str],
                        labels: dict[str, str] | None = None) -> None:
        """
        Repopulate the target slot dropdown, preserving the current selection.

        *labels* is the per-slot display label (``name (label)``); the stored
        item data stays the bare track name so inserts target the right slot.
        """
        labels = labels or {}
        current = self._slot_combo.currentData() or self._slot_combo.currentText()
        self._slot_combo.blockSignals(True)
        self._slot_combo.clear()
        for name in names:
            label = (labels.get(name) or '').strip()
            self._slot_combo.addItem(label or name, name)
            self._slot_combo.setItemData(self._slot_combo.count() - 1,
                                         f'Target track: {name}',
                                         Qt.ItemDataRole.ToolTipRole)
        idx = self._slot_combo.findData(current)
        if idx >= 0:
            self._slot_combo.setCurrentIndex(idx)
        self._slot_combo.blockSignals(False)

    def current_target_track(self) -> str:
        """Return the currently selected target slot name."""
        return self._slot_combo.currentData() or ''

    def set_macros(self, macros: list[dict]) -> None:
        """
        Refresh the nine macro buttons from *macros* (a list of dicts with
        'name' and 'content' keys, length 1-9).  Buttons with content get an
        amber number + a tooltip preview; empty ones are dimmed.
        """
        self._macros = list(macros) if macros else []
        for i, btn in enumerate(self._macro_btns):
            macro = (self._macros[i]
                     if i < len(self._macros) else None)
            body   = (macro or {}).get('content') or ''
            has    = bool(str(body).strip())
            btn.setToolTip(self._macro_tooltip(i + 1, macro))
            self._apply_macro_style(btn, has_content=has)

    def set_numpad_enabled(self, on: bool) -> None:
        """Sync the NUM toggle from outside (no signal re-emitted)."""
        self._btn_num.blockSignals(True)
        self._btn_num.setChecked(bool(on))
        self._btn_num.blockSignals(False)
        self._apply_num_style(bool(on))
