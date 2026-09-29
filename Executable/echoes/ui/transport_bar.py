"""
Three-row audio transport bar:
  ⏹  ⏮ ⏭  ▶  ⊙   🔊 ─●──  BPM [___]  [X/Y]   ← controls (⊙ = Sync)
  0:15.50  ─────●──────  3:45.00               ← time · progress · end time
  ══ audio envelope (drag grip to collapse/stretch) ══

Grid model
──────────
The time signature is read as  X/Y  =  X beats per bar, each beat divided
into Y equal GRID SLICES (a subdivision grid).  Y is editable, so triplet /
sextuplet / 12-tuplet grids are possible (e.g. 4/6 → 4 beats per bar with
six slices per beat, 8/12 → eight beats with twelve slices per beat).

  beat  = 60 000 / BPM ms          bar = X × beat
  slice = beat / Y

Everything snaps to the grid: the step buttons move by whole slices
(1×, 2×, 3×, 4× a slice), the ←/→ arrow keys nudge by one slice, Sync snaps
to the nearest slice and macro SKIP steps are quantised to the slice grid.
"""
from __future__ import annotations

from PySide6.QtCore    import QPointF, QRectF, Qt, QSettings, QTimer, Signal
from PySide6.QtGui     import (QBrush, QColor, QIcon, QIntValidator, QPainter,
                                QPen, QPixmap)
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QSlider, QSpinBox, QVBoxLayout,
                                QWidget)

from core.audio_player import AudioPlayer
from ui.envelope_viewer import EnvelopeViewer


def _fmt(ms: int) -> str:
    """Format as M:SS.cc  (centiseconds = 2 digits)."""
    ms      = max(0, ms)
    total_s = ms // 1000
    cs      = (ms % 1000) // 10
    return f"{total_s // 60}:{total_s % 60:02d}.{cs:02d}"


def _target_icon() -> QIcon:
    """A small crosshair / target logo for the Sync button."""
    size = 18
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#9cdcfe"), 1.2)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    c = size / 2.0
    p.drawEllipse(QRectF(c - 6.0, c - 6.0, 12.0, 12.0))   # outer ring
    p.drawEllipse(QRectF(c - 2.5, c - 2.5,  5.0,  5.0))   # inner ring
    p.drawLine(QPointF(c - 8.0, c), QPointF(c + 8.0, c))  # crosshair
    p.drawLine(QPointF(c, c - 8.0), QPointF(c, c + 8.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#9cdcfe")))
    p.drawEllipse(QRectF(c - 1.0, c - 1.0, 2.0, 2.0))     # centre dot
    p.end()
    return QIcon(pm)


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

# Unicode fraction glyphs used on step buttons where a clean glyph exists;
# anything else falls back to plain "a/b" text.
_FRACTION_GLYPHS = {
    (1, 2):  '\u00bd',   # ½
    (1, 3):  '\u2153',   # ⅓
    (2, 3):  '\u2154',   # ⅔
    (1, 4):  '\u00bc',   # ¼
    (3, 4):  '\u00be',   # ¾
    (1, 5):  '\u2155',   # ⅕
    (2, 5):  '\u2156',   # ⅖
    (3, 5):  '\u2157',   # ⅗
    (4, 5):  '\u2158',   # ⅘
    (1, 6):  '\u2159',   # ⅙
    (5, 6):  '\u215A',   # ⅚
    (1, 8):  '\u215B',   # ⅛
    (3, 8):  '\u215C',   # ⅜
    (5, 8):  '\u215D',   # ⅝
    (7, 8):  '\u215E',   # ⅞
    (1, 7):  '\u2150',   # ⅐
    (1, 9):  '\u2151',   # ⅑
    (1, 10): '\u2152',   # ⅒
}


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _slice_label(slices: int, per_beat: int) -> str:
    """
    Human label for *slices* of a beat when the beat has *per_beat* slices:
    returns the reduced fraction (e.g. 2 slices of 8 → "¼", 3 of 6 → "½").
    """
    a, b = slices, per_beat
    g = _gcd(a, b)
    a, b = a // g, b // g
    if b == 1:
        return str(a)
    return _FRACTION_GLYPHS.get((a, b), f'{a}/{b}')


class TransportBar(QWidget):
    """Compact single-row transport control attached to an *AudioPlayer*."""

    seek_requested = Signal(int)   # position in ms — fired while scrubbing & on release

    # Step buttons move 1/2/3/4 grid slices per press (both directions).
    _STEP_SLICES = (4, 3, 2, 1)

    def __init__(self, player: AudioPlayer, parent=None) -> None:
        super().__init__(parent)
        self._player   = player
        self._seeking  = False

        # Audition controller: while PAUSED, stepping forward plays the audio
        # and a fast poll stops it exactly at the step boundary (instead of a
        # wall-clock timer, which drifts because seeks are asynchronous).
        self._beat_preview = QTimer(self)
        self._beat_preview.setInterval(10)
        self._beat_preview.timeout.connect(self._on_audition_tick)
        self._preview_end_ms   = 0     # boundary the audition stops at
        self._preview_ticks    = 0     # safety cap so it can never run forever

        # Time signature as a subdivision grid: X beats per bar, Y slices per
        # beat (editable: 2, 3, 4, 6, 8, 12, 16 … or any typed value).
        self._beats_per_bar = 4
        self._slices_per_beat = 4

        self._build()
        self._wire()
        self._update_step_buttons()
        # Apply the restored volume to the player: the slider's setValue() in
        # _build() ran before the valueChanged connection existed, so without
        # this the audio would stay at the player's default (100 %) on boot.
        self._player.set_volume(self._vol.value() / 100.0)

    # ── Construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 2, 10, 2)
        root.setSpacing(2)

        # ── Row 1 — transport controls ────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(6)

        # ── Stop / Play-Pause
        self._btn_stop = QPushButton("⏹")
        self._btn_pp   = QPushButton("▶")
        for b in (self._btn_stop, self._btn_pp):
            b.setFixedSize(34, 30)
            b.setStyleSheet("font-size: 14px; padding: 0;")

        # ── Step buttons
        #
        # Every button steps by a whole number of GRID SLICES (a slice =
        # one Y-th of a beat), so each press lands exactly on the subdivision
        # grid:  [-4][-3][-2][-1]  ▶  [+1][+2][+3][+4]  slices.
        # Labels show the reduced fraction of a beat each step moves.
        _skip_style = (
            "QPushButton { font-size: 11px; padding: 0; "
            "background: #2a2a2a; color: #cccccc; "
            "border: 1px solid #444; border-radius: 3px; }"
            "QPushButton:hover { background: #3a3a3a; border-color: #007acc; }"
            "QPushButton:pressed { background: #1a1a1a; }"
        )
        self._btn_back_1 = QPushButton("-1")   # - 4 slices  (outermost)
        self._btn_back_h = QPushButton("-1")   # - 3 slices
        self._btn_back_q = QPushButton("-1")   # - 2 slices
        self._btn_back_e = QPushButton("-1")   # - 1 slice   (innermost)
        self._btn_next_e = QPushButton("+1")   # + 1 slice
        self._btn_next_q = QPushButton("+1")   # + 2 slices
        self._btn_next_h = QPushButton("+1")   # + 3 slices
        self._btn_next_1 = QPushButton("+1")   # + 4 slices
        # (button, back?, slice-count)
        self._step_btns: list[tuple[QPushButton, bool, int]] = [
            (self._btn_back_1, True,  4),
            (self._btn_back_h, True,  3),
            (self._btn_back_q, True,  2),
            (self._btn_back_e, True,  1),
            (self._btn_next_e, False, 1),
            (self._btn_next_q, False, 2),
            (self._btn_next_h, False, 3),
            (self._btn_next_1, False, 4),
        ]
        for b, _back, _n in self._step_btns:
            b.setFixedSize(30, 28)
            b.setStyleSheet(_skip_style)

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

        # ── Sync — snap the current time to the nearest grid slice
        self._btn_sync = QPushButton()
        self._btn_sync.setIcon(_target_icon())
        self._btn_sync.setFixedSize(30, 28)
        self._btn_sync.setStyleSheet(
            "QPushButton { background:#2a2a2a; border:1px solid #444; "
            "border-radius:3px; padding:0; }"
            "QPushButton:hover { background:#3a3a3a; border-color:#007acc; }"
            "QPushButton:pressed { background:#1a1a1a; }"
        )
        self._btn_sync.setToolTip(
            "Sync \u2014 snap the current time to the nearest grid slice\n"
            "(one Y-th of a beat \u2014 see the time signature / grid).")
        row.addWidget(self._btn_sync)

        row.addSpacing(4)
        row.addStretch(1)     # pushes the right-side controls to the edge

        # ── Volume (persisted across sessions)
        self._lbl_vol = QLabel("🔊")
        self._lbl_vol.setStyleSheet("font-size: 12px; color: #666666;")
        self._vol = QSlider(Qt.Orientation.Horizontal)
        self._vol.setRange(0, 100)
        try:
            saved_vol = int(QSettings().value("audio/volume", 80))
        except (TypeError, ValueError):
            saved_vol = 80
        self._vol.setValue(max(0, min(100, saved_vol)))
        self._vol.setFixedWidth(70)
        self._vol.setStyleSheet(_VOL_GROOVE)
        row.addWidget(self._lbl_vol)
        row.addWidget(self._vol)
        row.addSpacing(6)

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
        self._bpm.setToolTip("Beats per minute — controls beat and slice size")
        self._bpm.setStyleSheet(_spin_style)
        row.addWidget(self._lbl_bpm)
        row.addWidget(self._bpm)
        row.addSpacing(4)

        # ── Time-signature / subdivision grid  (numerator · denominator)
        #
        # Read as  X/Y = X beats per bar, each beat split into Y grid slices.
        # Y is the subdivision grid (standard 2/4/8/16, or triplet-style
        # grids such as 3, 6, 12 — type any value, 1-64).
        self._lbl_ts_sep = QLabel("/")
        self._lbl_ts_sep.setStyleSheet("color:#555555; font:13px 'Segoe UI';")
        self._ts_num = QSpinBox()
        self._ts_num.setRange(2, 32)
        self._ts_num.setValue(4)
        self._ts_num.setFixedWidth(40)
        self._ts_num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ts_num.setToolTip("Beats per bar — numerator")
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
        self._ts_den.setEditable(True)
        self._ts_den.addItems(["2", "3", "4", "6", "8", "12", "16"])
        self._ts_den.setCurrentText("4")
        self._ts_den.setFixedWidth(46)
        validator = QIntValidator(1, 64, self._ts_den)
        self._ts_den.lineEdit().setValidator(validator)
        self._ts_den.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._ts_den.setToolTip(
            "Grid slices per beat \u2014 the subdivision grid (denominator)\n"
            "Common: 2/4/8/16 \u00b7 triplet-style: 3, 6, 12 \u2014 editable, "
            "e.g. 4/6 or 8/12")
        self._ts_den.setStyleSheet(_den_style)
        row.addWidget(self._ts_num)
        row.addWidget(self._lbl_ts_sep)
        row.addWidget(self._ts_den)

        root.addLayout(row)

        # ── Row 2 — current time · progress bar · end time ───────────────────
        trow = QHBoxLayout()
        trow.setSpacing(8)

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

        # ── Seek slider
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setMinimum(0)
        self._seek.setMaximum(0)
        self._seek.setSingleStep(10)     # 10 ms
        self._seek.setPageStep(1_000)    # 1 s
        self._seek.setStyleSheet(_SEEK_GROOVE)

        # ── Duration label  (M:SS.cc)
        self._lbl_dur = QLabel("0:00.00")
        self._lbl_dur.setFixedWidth(62)
        self._lbl_dur.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_dur.setStyleSheet(
            "font: 11px 'Consolas', monospace; color: #555555;")

        trow.addWidget(self._lbl_pos)
        trow.addWidget(self._seek, stretch=2)
        trow.addWidget(self._lbl_dur)

        root.addLayout(trow)

        # ── Row 3 — audio envelope (collapsible / stretchable) ───────────────
        self._envelope = EnvelopeViewer()
        root.addWidget(self._envelope, stretch=0)

    def _wire(self) -> None:
        self._player.position_changed.connect(self._on_pos)
        self._player.duration_changed.connect(self._on_dur)
        self._player.playing_changed.connect(self._on_playing)
        self._player.source_changed.connect(self._envelope.set_audio_source)

        # An audio file may already be loaded before this bar was built
        self._envelope.set_audio_source(self._player.source_path)

        self._seek.sliderPressed.connect(self._on_slider_pressed)
        self._seek.sliderReleased.connect(self._on_release)
        self._seek.sliderMoved.connect(self._on_moved)

        self._btn_pp.clicked.connect(self._toggle)
        self._btn_stop.clicked.connect(self._player.stop)
        for b, back, slices in self._step_btns:
            b.clicked.connect(
                lambda _=False, back=back, slices=slices:
                self._step_slices(back, slices))
        self._btn_sync.clicked.connect(self._on_sync)
        self._vol.valueChanged.connect(self._on_volume_changed)
        self._bpm.valueChanged.connect(self._on_bpm_changed)
        self._ts_num.valueChanged.connect(self._on_ts_num_changed)
        self._ts_den.currentTextChanged.connect(self._on_ts_den_changed)
        self._lbl_pos.returnPressed.connect(self._on_pos_entered)
        self._lbl_pos.editingFinished.connect(self._on_pos_entered)

    # ── Step-button label / tooltip refresh ─────────────────────────────────

    def _update_step_buttons(self) -> None:
        """
        Refresh the step buttons after BPM / time-signature changes: labels
        show the reduced fraction of a beat (slices/Y), tooltips the slice
        count and duration, and the Sync tip reflects the current grid.
        """
        y = self._slices_per_beat
        for btn, back, slices in self._step_btns:
            sign = '-' if back else '+'
            btn.setText(sign + _slice_label(slices, y))
            ms    = slices * self.slice_ms()
            ms_str = (f'{ms:.0f} ms' if abs(ms - round(ms)) < 1e-6
                      else f'{ms:.1f} ms')
            label = _slice_label(slices, y).lstrip('-')
            btn.setToolTip(
                f"Step {'back' if back else 'forward'}  "
                f"{slices} grid slice{'s' if slices != 1 else ''}  "
                f"({label} beat \u00b7 {ms_str})\n"
                f"Grid: {self._beats_per_bar} beats per bar, "
                f"{y} slices per beat \u2014 always stays on the grid"
            )
        self._btn_sync.setToolTip(
            "Sync \u2014 snap the current time to the nearest grid slice\n"
            f"(1/{y} beat \u00b7 {self.slice_ms():.1f} ms)")

    # ── Slots ────────────────────────────────────────────────────────────────

    def _on_bpm_changed(self, bpm: int) -> None:
        self._update_step_buttons()

    def _on_ts_num_changed(self, beats_per_bar: int) -> None:
        """Numerator: beats per bar."""
        self._beats_per_bar = beats_per_bar
        self._update_step_buttons()

    def _on_ts_den_changed(self, den_text: str) -> None:
        """
        Denominator: grid slices per beat (any integer 1-64 — popup lists
        common values, the field is editable for others).
        """
        try:
            y = int(den_text)
        except ValueError:
            y = 0
        if not (1 <= y <= 64):
            # Invalid / empty while typing — revert to the last good value.
            self._ts_den.blockSignals(True)
            self._ts_den.setCurrentText(str(self._slices_per_beat))
            self._ts_den.blockSignals(False)
            return
        self._slices_per_beat = y
        self._update_step_buttons()

    def _step_slices(self, back: bool, slices: int) -> None:
        """
        Step the playhead by *slices* grid slices — grid-synced.

        The position is converted to a grid-slice INDEX (snapping to the
        nearest slice first, like Sync) and the step adds/subtracts whole
        slices in that index space, then converts back with the exact slice
        length (60 000 / BPM / Y).  Repeated presses therefore never drift:
        every press lands back exactly on the beat/subdivision grid.
        """
        slice_ms = self.slice_ms()          # exact grid slice length
        pos      = self._player.position
        k        = int(round(pos / slice_ms))   # nearest slice index (sync)
        if not back:
            k += slices
        else:
            k -= slices
        target = int(round(k * slice_ms))
        target = max(0, target)
        dur    = self._player.duration
        if dur > 0:
            target = min(target, dur)

        was_paused = not self._player.is_playing
        self._player.seek(target)
        self.seek_requested.emit(target)

        if was_paused and not back and target > pos:
            # Audition the skipped section: play from the new position and
            # stop EXACTLY at the step boundary (the seek is asynchronous, so
            # a wall-clock timer would let the audio overshoot and desync).
            self._beat_preview.stop()
            self._preview_end_ms = target
            self._preview_ticks  = int((target - pos + 2000) / 10) + 20
            self._player.play()
            self._beat_preview.start()

    def _on_sync(self) -> None:
        """
        Snap the current position to the nearest grid slice.

        The grid is anchored at 0 with the exact BPM, so the snap uses the
        exact slice length (60 000 / BPM / Y), not the rounded step size.
        """
        pos = self._player.position
        snapped = self.snap(pos)
        dur = self._player.duration
        if dur > 0:
            snapped = min(snapped, dur)
        self._player.seek(snapped)
        self.seek_requested.emit(snapped)
        self._lbl_pos.setText(_fmt(snapped))

    def _on_volume_changed(self, volume: int) -> None:
        """Apply the volume slider and remember it for the next session."""
        self._player.set_volume(volume / 100.0)
        QSettings().setValue("audio/volume", volume)

    # ── Transport slots ──────────────────────────────────────────────────────

    def _on_pos(self, ms: int) -> None:
        self._envelope.set_position(ms)
        if not self._seeking and not self._lbl_pos.hasFocus():
            self._seek.setValue(ms)
            self._lbl_pos.setText(_fmt(ms))

    def _on_dur(self, ms: int) -> None:
        self._envelope.set_duration(ms)
        self._seek.setMaximum(ms)
        self._lbl_dur.setText(_fmt(ms))

    def _on_playing(self, playing: bool) -> None:
        self._btn_pp.setText("⏸" if playing else "▶")

    # ── Public API ───────────────────────────────────────────────────────────

    def set_timesig(self, num: int, den: int) -> None:
        """Push a time signature into the transport (X beats/bar · Y slices)."""
        self._ts_num.setValue(num)
        den = max(1, min(64, int(den)))
        den_str = str(den)
        idx = self._ts_den.findText(den_str)
        if idx >= 0:
            self._ts_den.setCurrentIndex(idx)
        else:
            # Custom (typed-only) value — e.g. 5, 7, 24.
            self._ts_den.setCurrentText(den_str)
        self._beats_per_bar   = num
        self._slices_per_beat = den
        self._update_step_buttons()

    def nudge(self, back: bool) -> None:
        """
        Step by ONE grid slice (the smallest step) — used by the Left/Right
        arrow keys.  Forward nudges while paused audition that slice of the
        audio (play + auto-pause at the exact step boundary); during playback
        the playhead jumps and audio keeps playing.
        """
        self._step_slices(back, 1)

    def bpm(self) -> int:
        """Current BPM value from the spinbox."""
        return self._bpm.value()

    def beats_per_bar(self) -> int:
        """Beats per bar — numerator of the time signature."""
        return self._beats_per_bar

    def slices_per_beat(self) -> int:
        """Grid slices per beat — denominator of the time signature."""
        return self._slices_per_beat

    def beat_ms(self) -> float:
        """Exact millisecond length of one beat (60 000 / BPM)."""
        return 60_000.0 / max(1, self._bpm.value())

    def slice_ms(self) -> float:
        """Exact millisecond length of one grid slice (beat / Y)."""
        return self.beat_ms() / max(1, self._slices_per_beat)

    def bar_ms(self) -> float:
        """Millisecond length of one bar (beats per bar × beat)."""
        return self._beats_per_bar * self.beat_ms()

    def snap(self, ms: int) -> int:
        """Snap *ms* to the nearest grid slice."""
        step = self.slice_ms()
        k = round(ms / step)
        return int(round(k * step))

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_moved(self, value: int) -> None:
        # Move the cursor with the drag; the envelope pans only when the
        # cursor reaches the (narrow) follow band edge.
        self._envelope.follow_position(value)
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

    def _on_audition_tick(self) -> None:
        """
        Poll the playhead during a paused-step audition.  Stops the audio the
        moment it reaches the step boundary (or when playback was interrupted
        by the user / safety cap) and snaps the playhead to the boundary so
        the lights, markers and readout stay in sync.
        """
        if not self._player.is_playing:
            # User pressed stop/pause (or the media ended) mid-audition.
            self._beat_preview.stop()
            return
        if self._player.position >= self._preview_end_ms \
                or self._preview_ticks <= 0:
            self._beat_preview.stop()
            self._player.pause()
            self._player.seek(self._preview_end_ms)   # snap to the boundary
            self.seek_requested.emit(self._preview_end_ms)
        else:
            self._preview_ticks -= 1
