"""
Three-row audio transport bar:
  ⏹  ⏮ ⏭  ▶  ⊙   🔊 ─●──  BPM [___]  [4/4]   ← controls (⊙ = Sync)
  0:15.50  ─────●──────  3:45.00             ← time · progress · end time
  ══ audio envelope (drag grip to collapse/stretch) ══
"""
from __future__ import annotations

from PySide6.QtCore    import QPointF, QRectF, Qt, QSettings, QTimer, Signal
from PySide6.QtGui     import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
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
        row.addSpacing(2)

        # ── Sync — snap the current time to the nearest beat on the fret board
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
            "Sync \u2014 snap the current time to the nearest beat\n"
            "on the fret board grid (BPM-based).")
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
        self._bpm.setToolTip("Beats per minute — controls beat-step size")
        self._bpm.setStyleSheet(_spin_style)
        row.addWidget(self._lbl_bpm)
        row.addWidget(self._bpm)
        row.addSpacing(4)

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
        self._btn_back_1.clicked.connect(lambda: self._step(-1.0))
        self._btn_back_h.clicked.connect(lambda: self._step(-0.5))
        self._btn_back_q.clicked.connect(lambda: self._step(-0.25))
        self._btn_back_e.clicked.connect(lambda: self._step(-0.125))
        self._btn_next_e.clicked.connect(lambda: self._step(0.125))
        self._btn_next_q.clicked.connect(lambda: self._step(0.25))
        self._btn_next_h.clicked.connect(lambda: self._step(0.5))
        self._btn_next_1.clicked.connect(lambda: self._step(1.0))
        self._btn_sync.clicked.connect(self._on_sync)
        self._vol.valueChanged.connect(self._on_volume_changed)
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

    def _on_sync(self) -> None:
        """
        Snap the current position to the nearest beat of the fret-board grid.

        The grid on the note highway is anchored at 0 with the exact BPM, so
        the snap uses 60_000 / bpm (not the rounded step size).
        """
        beat = 60_000.0 / max(1, self._bpm.value())
        pos  = self._player.position
        snapped = int(round(pos / beat) * beat)
        snapped = max(0, snapped)
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

    # ── Slots ─────────────────────────────────────────────────────────────────

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


    # ── Public API ────────────────────────────────────────────────────────────

    def set_timesig(self, num: int, den: int) -> None:
        """Push a complete time signature into the transport bar widgets."""
        self._ts_num.setValue(num)
        den_str = str(den)
        idx = self._ts_den.findText(den_str)
        if idx >= 0:
            self._ts_den.setCurrentIndex(idx)

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
