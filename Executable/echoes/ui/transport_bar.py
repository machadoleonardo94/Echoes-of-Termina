"""
Single-row audio transport bar:
  ⏹  ▶/⏸  ⏮ ⏭  0:15.50  ─────●──────  3:45.00  🔊 ─●──  BPM [___]
"""
from __future__ import annotations

from PySide6.QtCore    import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QSlider, QSpinBox, QWidget)

from core.audio_player import AudioPlayer


def _fmt(ms: int) -> str:
    """Format as M:SS.cc  (centiseconds = 2 digits)."""
    ms      = max(0, ms)
    total_s = ms // 1000
    cs      = (ms % 1000) // 10
    return f"{total_s // 60}:{total_s % 60:02d}.{cs:02d}"


_SEEK_GROOVE = """
    QSlider::groove:horizontal {
        height: 4px; background: #404040; border-radius: 2px;
    }
    QSlider::sub-page:horizontal {
        background: #007acc; border-radius: 2px;
    }
    QSlider::handle:horizontal {
        width: 12px; height: 12px; margin: -4px 0;
        background: #ffffff; border-radius: 6px;
    }
    QSlider::handle:horizontal:hover { background: #cccccc; }
"""

_VOL_GROOVE = """
    QSlider::groove:horizontal {
        height: 3px; background: #404040; border-radius: 2px;
    }
    QSlider::sub-page:horizontal {
        background: #555555; border-radius: 2px;
    }
    QSlider::handle:horizontal {
        width: 10px; height: 10px; margin: -4px 0;
        background: #888888; border-radius: 5px;
    }
    QSlider::handle:horizontal:hover { background: #aaaaaa; }
"""


class TransportBar(QWidget):
    """Compact single-row transport control attached to an *AudioPlayer*."""

    seek_requested = Signal(int)   # position in ms — fired while scrubbing & on release

    def __init__(self, player: AudioPlayer, parent=None) -> None:
        super().__init__(parent)
        self._player   = player
        self._seeking  = False
        self._beat_ms  = 500          # default 120 BPM → 500 ms/beat

        # Single-shot timer: pauses after a one-beat preview
        self._beat_preview = QTimer(self)
        self._beat_preview.setSingleShot(True)
        self._beat_preview.timeout.connect(self._player.pause)
        self._beats_per_bar = 4

        self._build()
        self._wire()
        self._update_step_tips()

    # ── Construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.setFixedHeight(44)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(6)

        # ── Stop / Play-Pause
        self._btn_stop = QPushButton("⏹")
        self._btn_pp   = QPushButton("▶")
        for b in (self._btn_stop, self._btn_pp):
            b.setFixedSize(34, 30)
            b.setStyleSheet("font-size: 14px; padding: 0;")

        # ── Beat-step buttons  [-1][-½][-¼][-⅛]  [PP]  [+⅛][+¼][+½][+1]
        _skip_style = (
            "QPushButton { font-size: 11px; padding: 0; "
            "background: #2a2a2a; color: #cccccc; "
            "border: 1px solid #444; border-radius: 3px; }"
            "QPushButton:hover { background: #3a3a3a; border-color: #007acc; }"
            "QPushButton:pressed { background: #1a1a1a; }"
        )
        self._btn_back_1 = QPushButton("-1")
        self._btn_back_h = QPushButton("-\u00bd")   # -½
        self._btn_back_q = QPushButton("-\u00bc")   # -¼
        self._btn_back_e = QPushButton("-\u215b")   # -⅛
        self._btn_next_e = QPushButton("+\u215b")   # +⅛
        self._btn_next_q = QPushButton("+\u00bc")   # +¼
        self._btn_next_h = QPushButton("+\u00bd")   # +½
        self._btn_next_1 = QPushButton("+1")
        self._step_btns: list[tuple[QPushButton, str, float]] = [
            (self._btn_back_1, "Step back",    -1.0  ),
            (self._btn_back_h, "Step back",    -0.5  ),
            (self._btn_back_q, "Step back",    -0.25 ),
            (self._btn_back_e, "Step back",    -0.125),
            (self._btn_next_e, "Step forward",  0.125),
            (self._btn_next_q, "Step forward",  0.25 ),
            (self._btn_next_h, "Step forward",  0.5  ),
            (self._btn_next_1, "Step forward",  1.0  ),
        ]
        for b, _label, _beats in self._step_btns:
            b.setFixedSize(30, 28)
            b.setStyleSheet(_skip_style)

        # ── Position box  (editable — type a timestamp and press Enter to seek)
        self._lbl_pos = QLineEdit("0:00.00")
        self._lbl_pos.setFixedWidth(68)
        self._lbl_pos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_pos.setToolTip("Current position — type M:SS.cc and press Enter to seek")
        self._lbl_pos.setStyleSheet(
            "QLineEdit {"
            "  font: 11px 'Consolas', monospace; color: #aaaaaa;"
            "  background: transparent; border: 1px solid transparent;"
            "  border-radius: 3px; padding: 0 3px;"
            "}"
            "QLineEdit:focus {"
            "  color: #e8e8e8; background: #2d2d2d;"
            "  border-color: #007acc;"
            "}")

        # ── Duration label  (M:SS.cc)
        self._lbl_dur = QLabel("0:00.00")
        self._lbl_dur.setFixedWidth(62)
        self._lbl_dur.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_dur.setStyleSheet(
            "font: 11px 'Consolas', monospace; color: #555555;")

        # ── Seek slider
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setMinimum(0)
        self._seek.setMaximum(0)
        self._seek.setSingleStep(10)     # 10 ms
        self._seek.setPageStep(1_000)    # 1 s
        self._seek.setStyleSheet(_SEEK_GROOVE)

        # ── Volume
        self._lbl_vol = QLabel("🔊")
        self._lbl_vol.setStyleSheet("font-size: 12px; color: #666666;")
        self._vol = QSlider(Qt.Orientation.Horizontal)
        self._vol.setRange(0, 100)
        self._vol.setValue(80)
        self._vol.setFixedWidth(70)
        self._vol.setStyleSheet(_VOL_GROOVE)

        # ── BPM spinbox
        self._lbl_bpm = QLabel("BPM")
        self._lbl_bpm.setStyleSheet("color:#666666; font:11px 'Segoe UI';")
        _spin_style = (
            "QSpinBox { background:#2d2d2d; color:#d4d4d4; "
            "border:1px solid #555; border-radius:3px; "
            "padding:1px 4px; font:11px 'Consolas'; }"
            "QSpinBox::up-button, QSpinBox::down-button { width:14px; }"
        )
        self._bpm = QSpinBox()
        self._bpm.setRange(20, 300)
        self._bpm.setValue(120)
        self._bpm.setFixedWidth(54)
        self._bpm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bpm.setToolTip("Beats per minute — controls beat-step size")
        self._bpm.setStyleSheet(_spin_style)

        # ── Time-signature  (numerator spinbox  /  denominator combobox)
        self._lbl_ts_sep = QLabel("/")
        self._lbl_ts_sep.setStyleSheet("color:#555555; font:13px 'Segoe UI';")
        self._ts_num = QSpinBox()
        self._ts_num.setRange(2, 12)
        self._ts_num.setValue(4)
        self._ts_num.setFixedWidth(38)
        self._ts_num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ts_num.setToolTip("Beats per bar — numerator of the time signature")
        self._ts_num.setStyleSheet(_spin_style)

        _den_style = (
            "QComboBox { background:#2d2d2d; color:#d4d4d4; "
            "border:1px solid #555; border-radius:3px; "
            "padding:1px 2px; font:11px 'Consolas'; }"
            "QComboBox:focus { border-color:#007acc; }"
            "QComboBox::drop-down { border:none; width:14px; }"
            "QComboBox QAbstractItemView {"
            "  background:#2d2d2d; color:#d4d4d4; "
            "  selection-background-color:#005f9e; }"
        )
        self._ts_den = QComboBox()
        self._ts_den.addItems(["2", "4", "8"])
        self._ts_den.setCurrentText("4")
        self._ts_den.setFixedWidth(36)
        self._ts_den.setToolTip("Beat unit — denominator of the time signature")
        self._ts_den.setStyleSheet(_den_style)

        # ── Layout
        row.addWidget(self._btn_stop)
        row.addSpacing(2)
        row.addWidget(self._btn_back_1)
        row.addWidget(self._btn_back_h)
        row.addWidget(self._btn_back_q)
        row.addWidget(self._btn_back_e)
        row.addSpacing(2)
        row.addWidget(self._btn_pp)
        row.addSpacing(2)
        row.addWidget(self._btn_next_e)
        row.addWidget(self._btn_next_q)
        row.addWidget(self._btn_next_h)
        row.addWidget(self._btn_next_1)
        row.addSpacing(4)
        row.addWidget(self._lbl_pos)
        row.addWidget(self._seek, stretch=1)
        row.addWidget(self._lbl_dur)
        row.addSpacing(4)
        row.addWidget(self._lbl_vol)
        row.addWidget(self._vol)
        row.addSpacing(6)
        row.addWidget(self._lbl_bpm)
        row.addWidget(self._bpm)
        row.addSpacing(4)
        row.addWidget(self._ts_num)
        row.addWidget(self._lbl_ts_sep)
        row.addWidget(self._ts_den)

    def _wire(self) -> None:
        self._player.position_changed.connect(self._on_pos)
        self._player.duration_changed.connect(self._on_dur)
        self._player.playing_changed.connect(self._on_playing)

        self._seek.sliderPressed.connect(
            lambda: setattr(self, '_seeking', True))
        self._seek.sliderReleased.connect(self._on_release)
        self._seek.sliderMoved.connect(self._on_moved)

        self._btn_pp.clicked.connect(self._toggle)
        self._btn_stop.clicked.connect(self._player.stop)
        self._btn_back_1.clicked.connect(lambda: self._step(-1.0))
        self._btn_back_h.clicked.connect(lambda: self._step(-0.5))
        self._btn_back_q.clicked.connect(lambda: self._step(-0.25))
        self._btn_back_e.clicked.connect(lambda: self._step(-0.125))
        self._btn_next_e.clicked.connect(lambda: self._step(0.125))
        self._btn_next_q.clicked.connect(lambda: self._step(0.25))
        self._btn_next_h.clicked.connect(lambda: self._step(0.5))
        self._btn_next_1.clicked.connect(lambda: self._step(1.0))
        self._vol.valueChanged.connect(
            lambda v: self._player.set_volume(v / 100.0))
        self._bpm.valueChanged.connect(self._on_bpm_changed)
        self._ts_num.valueChanged.connect(
            lambda n: setattr(self, '_beats_per_bar', n))
        self._lbl_pos.returnPressed.connect(self._on_pos_entered)
        self._lbl_pos.editingFinished.connect(self._on_pos_entered)

    # ── Step-button tooltip helpers ──────────────────────────────────────────

    def _update_step_tips(self) -> None:
        """Refresh all step-button tooltips to reflect the current beat_ms."""
        _FRAC_NAMES = {
            1.0:   '1 beat',
            0.5:   '\u00bd beat',
            0.25:  '\u00bc beat',
            0.125: '\u215b beat',
        }
        for btn, label, beats in self._step_btns:
            ms  = abs(beats) * self._beat_ms
            ms_str = f'{ms:.0f} ms' if ms == int(ms) else f'{ms:.1f} ms'
            frac   = _FRAC_NAMES.get(abs(beats), f'{abs(beats)} beats')
            btn.setToolTip(f'{label}  {frac}  ({ms_str})')

    def _on_bpm_changed(self, bpm: int) -> None:
        self._beat_ms = round(60_000 / bpm)
        self._update_step_tips()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_pos(self, ms: int) -> None:
        if not self._seeking and not self._lbl_pos.hasFocus():
            self._seek.setValue(ms)
            self._lbl_pos.setText(_fmt(ms))

    def _on_dur(self, ms: int) -> None:
        self._seek.setMaximum(ms)
        self._lbl_dur.setText(_fmt(ms))

    def _on_playing(self, playing: bool) -> None:
        self._btn_pp.setText("⏸" if playing else "▶")


    # ── Public API ────────────────────────────────────────────────────────────

    def set_timesig(self, num: int, den: int) -> None:
        """Push a complete time signature into the transport bar widgets."""
        self._ts_num.setValue(num)
        den_str = str(den)
        idx = self._ts_den.findText(den_str)
        if idx >= 0:
            self._ts_den.setCurrentIndex(idx)

    def _on_moved(self, value: int) -> None:
        if not self._lbl_pos.hasFocus():
            self._lbl_pos.setText(_fmt(value))
        self.seek_requested.emit(value)

    def _on_release(self) -> None:
        self._seeking = False
        pos = self._seek.value()
        self._player.seek(pos)
        self.seek_requested.emit(pos)

    def _on_pos_entered(self) -> None:
        """Parse the typed timestamp and seek, then give focus back to the window."""
        from core.script_engine import _parse_timestamp
        raw = self._lbl_pos.text().strip()
        ms  = _parse_timestamp(raw)
        if ms is None:
            # Accept plain seconds too: "15" or "15.5"
            try:
                ms = int(float(raw) * 1000)
            except ValueError:
                ms = None
        if ms is not None:
            ms = max(0, min(ms, self._player.duration or ms))
            self._player.seek(ms)
            self.seek_requested.emit(ms)
            self._lbl_pos.setText(_fmt(ms))
        else:
            # Restore current position on bad input
            self._lbl_pos.setText(_fmt(self._player.position))
        self._lbl_pos.clearFocus()

    def _toggle(self) -> None:
        if self._player.is_playing:
            self._player.pause()
        else:
            self._player.play()

    def _step(self, beats: float) -> None:
        """Step forward (positive beats) or backward (negative beats)."""
        was_paused = not self._player.is_playing
        skip_ms    = self._beat_ms * beats
        pos        = round(self._player.position + skip_ms)
        pos        = max(0, pos)
        dur        = self._player.duration
        if dur > 0 and beats > 0:
            pos = min(pos, dur)
        self._player.seek(pos)
        self.seek_requested.emit(pos)
        if was_paused and beats > 0:
            self._beat_preview.stop()
            self._player.play()
            self._beat_preview.start(round(abs(skip_ms)))
