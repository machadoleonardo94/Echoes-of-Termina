"""
FFT spectrum analyser — a separate, self-contained window.

Two views of the same pre-computed analysis (core/spectrum.py):

  Bars       classic analyser: one bar per log-spaced band, with a peak-hold
             line that decays, driven by the transport position.
  Waterfall  scrolling spectrogram: time on X, log frequency on Y, colour =
             level.  Wheel zooms the time span, drag pans (and stops
             following), double-click resumes following, click seeks.

The window is deliberately standalone: it can be launched on its own
(``python spectrum.py``) or opened from the main window's Tools menu, in which
case it drives the very same AudioPlayer and needs no audio of its own.

Why pre-analysed instead of live: QMediaPlayer never exposes PCM to Python,
so the spectrum is decoded and analysed once per file in a worker thread and
then rendered against the player's position signal (see core/spectrum.py).
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore    import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui     import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from core.audio_converter import SOURCE_EXTS, ffmpeg_available
from core.audio_player    import AudioPlayer
from core.spectrum        import (DEFAULT_BANDS, SpectrumWorker, byte_to_db)

# ── Look ────────────────────────────────────────────────────────────────────

_BG      = QColor("#161616")
_GRID    = QColor(255, 255, 255, 26)
_TEXT    = QColor("#9d9d9d")
_DIM     = QColor("#666666")
_ACCENT  = QColor("#007acc")     # playhead — matches the seek bar
_HOLD    = QColor("#e8e8e8")     # peak-hold line

# Frequency grid lines drawn on both views.
_TICKS_HZ = (50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0)

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

_SEEK_STYLE = """
    QSlider::groove:horizontal   { height: 4px; background: #404040; border-radius: 2px; }
    QSlider::sub-page:horizontal { background: #007acc; border-radius: 2px; }
    QSlider::handle:horizontal   { width: 12px; height: 12px; margin: -4px 0;
                                   background: #ffffff; border-radius: 6px; }
"""

_MIN_SPAN_MS = 500.0        # closest waterfall zoom
_DEFAULT_SPAN_MS = 8000.0   # default waterfall time window
_FOLLOW_AT = 0.80           # playhead position inside the waterfall when following
_REFRESH_MS = 33            # ~30 fps while playing


# ── Helpers ─────────────────────────────────────────────────────────────────

def _colormap() -> list:
    """256-entry magma-like colour table (dark → purple → orange → white)."""
    stops = ((0.00, (0, 0, 4)),      (0.15, (40, 11, 84)),
             (0.30, (101, 21, 110)), (0.45, (159, 42, 99)),
             (0.60, (212, 72, 66)),  (0.75, (245, 125, 21)),
             (0.90, (250, 193, 39)), (1.00, (252, 255, 200)))
    table = []
    for i in range(256):
        t = i / 255.0
        for k in range(len(stops) - 1):
            t0, c0 = stops[k]
            t1, c1 = stops[k + 1]
            if t <= t1 or k == len(stops) - 2:
                u = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
                u = max(0.0, min(1.0, u))
                r = int(c0[0] + (c1[0] - c0[0]) * u)
                g = int(c0[1] + (c1[1] - c0[1]) * u)
                b = int(c0[2] + (c1[2] - c0[2]) * u)
                table.append((0xFF << 24) | (r << 16) | (g << 8) | b)
                break
    return table


_COLORS = _colormap()


def _fmt_ms(ms: int) -> str:
    """Format as M:SS.cc."""
    ms = max(0, int(ms))
    total_s = ms // 1000
    return f"{total_s // 60}:{total_s % 60:02d}.{(ms % 1000) // 10:02d}"


def _fmt_hz(hz: float) -> str:
    """Compact frequency label: 440 Hz / 1.2 kHz / 11 kHz."""
    if hz <= 0:
        return ""
    if hz < 1000:
        return f"{hz:.0f} Hz"
    if hz < 10000:
        return f"{hz / 1000.0:.1f} kHz"
    return f"{hz / 1000.0:.0f} kHz"


def _note_name(hz: float) -> str:
    """Nearest note name (and cent offset) for a frequency, or ''."""
    if hz < 25.0 or hz > 12000.0:
        return ""
    midi = int(round(12.0 * math.log2(hz / 440.0))) + 69
    if not (0 <= midi <= 127):
        return ""
    exact = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    cents = 1200.0 * math.log2(hz / exact)
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1} {cents:+.0f}\u00a2"


# ── The view ────────────────────────────────────────────────────────────────

class SpectrumView(QWidget):
    """
    Paints one :class:`~core.spectrum.Spectrogram` as bars or a waterfall.

    All state that the window can change (mode, dB range, band count, time
    span) lives here; the widget never touches the player, it only reports a
    :attr:`seek_requested` when the user clicks the waterfall.
    """

    seek_requested = Signal(int)      # milliseconds

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._spec = None
        self._pos_ms = 0
        self._mode = "bars"                 # 'bars' | 'waterfall'
        self._db_floor = -90.0
        self._span_ms = _DEFAULT_SPAN_MS
        self._view_start_ms = 0.0
        self._follow = True

        self._hold: list = []               # peak-hold levels (0..1 per band)
        self._xlat = bytes(256)             # stored byte → colour index
        self._img = None                    # cached waterfall QImage
        self._img_bytes = b""
        self._img_key = None

        self._panning = False
        self._pan_x = 0.0
        self._pan_start = 0.0
        self._pan_moved = False
        self._hover = None                  # (frame, band) under the cursor

        self.setMouseTracking(True)
        self.setMinimumHeight(240)
        self._rebuild_xlat()
        self.setToolTip(
            "FFT spectrum\n"
            "waterfall: wheel zooms time \u00b7 drag pans \u00b7 click seeks\n"
            "double-click: fit the whole track and resume following")

    # ── Public API ─────────────────────────────────────────────────────────

    def set_spectrogram(self, spec) -> None:
        """Show *spec* (or clear the view when None)."""
        self._spec = spec
        self._hold = [0.0] * (spec.bands if spec else 0)
        self._view_start_ms = 0.0
        self._follow = True
        self._hover = None                  # indexed into the old matrix
        self._img_key = None
        self.update()

    def set_position(self, ms: int) -> None:
        """Advance the playhead (the only call needed while playing)."""
        self._pos_ms = max(0, int(ms))
        self._decay_hold()
        if self._mode == "waterfall" and self._follow:
            self._follow_keep_visible()
        self.update()

    def set_view_mode(self, mode: str) -> None:
        self._mode = "waterfall" if mode == "waterfall" else "bars"
        self._follow = True
        self.update()

    def set_db_floor(self, db: float) -> None:
        """Display floor in dBFS (top of the range is always 0 dBFS)."""
        self._db_floor = float(db)
        self._rebuild_xlat()
        self._img_key = None
        self.update()

    def set_time_span(self, ms: float) -> None:
        """Waterfall time window, in milliseconds."""
        self._span_ms = max(_MIN_SPAN_MS, float(ms))
        self._img_key = None
        self.update()

    def reset_view(self) -> None:
        """Fit the whole track and start following again."""
        self._span_ms = max(_MIN_SPAN_MS, self._duration_ms() or _DEFAULT_SPAN_MS)
        self._view_start_ms = 0.0
        self._follow = True
        self._img_key = None
        self.update()

    # ── Internals ──────────────────────────────────────────────────────────

    def _duration_ms(self) -> float:
        if self._spec is None:
            return 0.0
        return self._spec.duration_ms or self._spec.frames * self._spec.frame_ms

    def _rebuild_xlat(self) -> None:
        """
        Byte → colour-index translation table for the current dB range.

        Stored bytes span -100…0 dBFS; the display may show only the top of
        that range, so every stored byte is rescaled once here and the
        waterfall then uses a C-speed ``bytes.translate`` per column.
        """
        span = -self._db_floor if self._db_floor < 0 else 1.0
        table = bytearray(256)
        for b in range(256):
            db = byte_to_db(b)
            v = int((db - self._db_floor) / span * 255.0 + 0.5)
            table[b] = 0 if v < 0 else (255 if v > 255 else v)
        self._xlat = bytes(table)

    def _norm(self, db: float) -> float:
        """dBFS → 0..1 within the current display range."""
        span = -self._db_floor if self._db_floor < 0 else 1.0
        v = (db - self._db_floor) / span
        return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)

    def _decay_hold(self) -> None:
        """Drop the peak-hold lines a little on every position update."""
        if not self._hold or self._spec is None:
            return
        drop = 0.010
        for i, h in enumerate(self._hold):
            if h > 0.0:
                self._hold[i] = 0.0 if h <= drop else h - drop

    def _band_x(self, band: int, w: float) -> float:
        return (band + 0.5) / self._spec.bands * w

    def _band_y(self, band: int, h: float) -> float:
        """Y of a band in the waterfall (band 0 = lowest → bottom)."""
        return (1.0 - (band + 0.5) / self._spec.bands) * h

    def _band_at_hz(self, hz: float) -> int:
        """Index of the band containing *hz* (nearest when out of range)."""
        if self._spec is None:
            return 0
        for b, (f0, f1, _b0, _b1) in enumerate(self._spec.plan):
            if f0 <= hz < f1:
                return b
        return 0 if hz < self._spec.plan[0][0] else self._spec.bands - 1

    def _band_at_y(self, y: float, h: float) -> int:
        frac = 1.0 - max(0.0, min(1.0, y / max(1.0, h)))
        return max(0, min(self._spec.bands - 1, int(frac * self._spec.bands)))

    def _band_at_x(self, x: float, w: float) -> int:
        frac = max(0.0, min(1.0, x / max(1.0, w)))
        return max(0, min(self._spec.bands - 1, int(frac * self._spec.bands)))

    def _effective_span(self) -> float:
        """Waterfall span, never longer than the track."""
        dur = self._duration_ms()
        if dur <= 0:
            return self._span_ms
        return max(_MIN_SPAN_MS, min(self._span_ms, dur))

    def _view_window(self) -> tuple:
        """``(t0, span)`` of the visible time window, in ms."""
        dur = self._duration_ms()
        span = self._effective_span()
        t0 = self._view_start_ms
        if dur > 0:
            t0 = max(0.0, min(t0, max(0.0, dur - span)))
        return t0, span

    def _follow_keep_visible(self) -> None:
        """Scroll so the playhead sits at :data:`_FOLLOW_AT` of the window."""
        dur = self._duration_ms()
        span = self._effective_span()
        if dur <= span:
            self._view_start_ms = 0.0
            return
        self._view_start_ms = self._pos_ms - _FOLLOW_AT * span

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _BG)

        if self._spec is None or self._spec.frames <= 0:
            p.setPen(_DIM)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                       "no analysis \u2014 open an audio file")
            p.end()
            return

        if self._mode == "waterfall":
            self._paint_waterfall(p, w, h)
        else:
            self._paint_bars(p, w, h)

        self._paint_playhead(p, w, h)
        self._paint_readout(p, w, h)
        p.end()

    # ── Bars ───────────────────────────────────────────────────────────────

    def _paint_bars(self, p: QPainter, w: int, h: int) -> None:
        spec = self._spec
        frame = spec.frame_at(self._pos_ms)
        col = spec.column_db(frame)
        n = spec.bands
        bw = w / float(n)

        base = h - 18                       # room for the frequency labels
        p.setPen(Qt.PenStyle.NoPen)
        for b in range(n):
            level = self._norm(col[b])
            x = b * bw
            bw_px = max(1.0, bw - (1.0 if bw > 3 else 0.0))
            bars = int(level * base)
            if bars > 0:
                # Colour follows level, so shape *and* intensity are readable.
                p.setBrush(_COLORS[min(255, int(level * 255))])
                p.drawRect(QRectF(x, base - bars, bw_px, bars))
            if self._hold[b] > 0.0:
                p.setPen(QPen(_HOLD, 1.0))
                hy = base - self._hold[b] * base
                p.drawLine(QPointF(x, hy), QPointF(x + bw_px, hy))
                p.setPen(Qt.PenStyle.NoPen)
            if level > self._hold[b]:
                self._hold[b] = level

        self._paint_freq_grid(p, w, base, vertical=True)
        p.setPen(QPen(QColor("#3c3c3c"), 1.0))
        p.drawLine(QPointF(0, base), QPointF(w, base))

    # ── Waterfall ──────────────────────────────────────────────────────────

    def _paint_waterfall(self, p: QPainter, w: int, h: int) -> None:
        img = self._waterfall_image(w, h)
        if img is not None:
            p.drawImage(QRectF(0, 0, w, h), img)
        self._paint_freq_grid(p, w, h, vertical=False)

    def _waterfall_image(self, w: int, h: int):
        """
        Build the spectrogram image for the visible window.

        The matrix is stored as one byte per (frame, band), and
        ``Format_Indexed8`` lets Qt blit that straight to screen: the buffer
        is filled with one *strided slice assignment* per screen column
        (``buf[x::w] = column``), which transposes frame-major data into
        row-major image data without a Python loop per pixel.
        """
        spec = self._spec
        t0, span = self._view_window()
        bands = spec.bands
        key = (w, h, bands, round(t0, 2), round(span, 2), self._xlat)
        if key == self._img_key:
            return self._img

        buf = bytearray(w * bands)
        dur = self._duration_ms()
        fpp = span / max(1, w) / spec.frame_ms      # frames per pixel column
        for x in range(w):
            t = t0 + (x + 0.5) * span / w
            if dur > 0 and t > dur:
                break                                # past the end: leave dark
            f0 = spec.frame_at(t)
            if fpp >= 2.0:
                # Zoomed out: keep the loudest sample of the covered frames so
                # transients stay visible instead of being skipped.
                f1 = min(spec.frames, f0 + max(1, int(fpp)))
                column = bytes(map(max, *[spec.raw_column(f)
                                          for f in range(f0, f1)]))
            else:
                column = spec.raw_column(f0)
            buf[x::w] = column.translate(self._xlat)[::-1]   # top = high band

        self._img_bytes = bytes(buf)
        img = QImage(self._img_bytes, w, bands, w,
                     QImage.Format.Format_Indexed8)
        img.setColorTable(_COLORS)
        self._img = img
        self._img_key = key
        return img

    # ── Shared overlays ────────────────────────────────────────────────────

    def _paint_freq_grid(self, p: QPainter, w: int, h: int,
                         vertical: bool) -> None:
        """Frequency grid lines + labels (log-spaced bands → even spacing)."""
        p.setPen(QPen(_GRID, 1.0))
        for hz in _TICKS_HZ:
            band = self._band_at_hz(hz)
            if vertical:
                x = self._band_x(band, w)
                p.drawLine(QPointF(x, 0), QPointF(x, h))
            else:
                y = self._band_y(band, h)
                p.drawLine(QPointF(0, y), QPointF(w, y))
        p.setPen(_DIM)
        for hz in _TICKS_HZ:
            band = self._band_at_hz(hz)
            if vertical:
                x = self._band_x(band, w)
                p.drawText(QPointF(x + 3, h - 5), _fmt_hz(hz))
            else:
                y = self._band_y(band, h)
                p.drawText(QPointF(4, max(11.0, y - 3)), _fmt_hz(hz))

    def _paint_playhead(self, p: QPainter, w: int, h: int) -> None:
        if self._mode != "waterfall":
            return
        t0, span = self._view_window()
        if span <= 0:
            return
        x = (self._pos_ms - t0) / span * w
        if 0.0 <= x <= w:
            p.setPen(QPen(_ACCENT, 1.2))
            p.drawLine(QPointF(x, 0), QPointF(x, h))

    def _paint_readout(self, p: QPainter, w: int, h: int) -> None:
        """Corner text: playhead time, peak, and the value under the cursor."""
        spec = self._spec
        frame = spec.frame_at(self._pos_ms)
        peak = spec.peak_hz(frame)
        parts = [f"{_fmt_ms(self._pos_ms)}"]
        if peak > 0:
            note = _note_name(peak)
            parts.append(f"peak ~{_fmt_hz(peak)}"
                         + (f" ({note})" if note else ""))
        line = "   ".join(parts)

        p.setPen(_TEXT)
        p.drawText(QRectF(6, 4, w - 12, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   line)

        if self._hover is not None:
            hf, hb = self._hover
            if 0 <= hf < spec.frames and 0 <= hb < spec.bands:
                db = spec.band_db(hf, hb)
                f0, f1 = spec.band_hz(hb)
                txt = (f"{_fmt_hz((f0 + f1) / 2.0)}   {db:.0f} dBFS"
                       f"   @ {_fmt_ms(hf * spec.frame_ms)}")
                p.setPen(_TEXT)
                p.drawText(QRectF(6, 4, w - 12, 16),
                           Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter, txt)

    # ── Interaction ────────────────────────────────────────────────────────

    def _time_at(self, x: float, w: float) -> float:
        t0, span = self._view_window()
        return t0 + max(0.0, min(1.0, x / max(1.0, w))) * span

    def mousePressEvent(self, event) -> None:
        if self._spec is None or self._mode != "waterfall" or \
                event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._panning = True
        self._pan_moved = False
        self._pan_x = event.position().x()
        self._pan_start = self._view_start_ms
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._spec is None:
            super().mouseMoveEvent(event)
            return
        if self._panning:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._panning = False
            else:
                w = max(1, self.width())
                if abs(event.position().x() - self._pan_x) > 4:
                    self._pan_moved = True
                    self._follow = False
                _t0, span = self._view_window()
                dx = (event.position().x() - self._pan_x) / w
                self._view_start_ms = self._pan_start - dx * span
                self._img_key = None
                self.update()
                event.accept()
                return
        x, y = event.position().x(), event.position().y()
        band = (self._band_at_y(y, self.height()) if self._mode == "waterfall"
                else self._band_at_x(x, self.width()))
        t = (self._time_at(x, max(1, self.width()))
             if self._mode == "waterfall" else self._pos_ms)
        self._hover = (self._spec.frame_at(t), band)
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._panning
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._panning:
            self._panning = False
            if not self._pan_moved and self._mode == "waterfall":
                self.seek_requested.emit(
                    int(self._time_at(event.position().x(), max(1, self.width()))))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_view()
        event.accept()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        """Waterfall: zoom the time window around the cursor. Bars: no-op."""
        if self._spec is None or self._mode != "waterfall":
            super().wheelEvent(event)
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        w = max(1, self.width())
        anchor_t = self._time_at(event.position().x(), w)
        frac = event.position().x() / w
        span = self._effective_span() * (0.8 ** steps)
        dur = self._duration_ms()
        span = max(_MIN_SPAN_MS, min(span, dur if dur > 0 else span))
        self._span_ms = span
        self._view_start_ms = anchor_t - frac * span
        self._follow = False
        self._img_key = None
        self.update()
        event.accept()

    def resizeEvent(self, event) -> None:
        self._img_key = None
        super().resizeEvent(event)


# ── The window ──────────────────────────────────────────────────────────────

class SpectrumWindow(QDialog):
    """
    Standalone FFT spectrum analyser window.

    Pass an existing :class:`~core.audio_player.AudioPlayer` to make the window
    follow the main window's transport (and to steer it); leave it out and the
    window creates its own player, complete with a small transport row so it is
    usable on its own.
    """

    def __init__(self, player: AudioPlayer | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Echoes \u2014 FFT Spectrum")
        self.resize(1080, 620)

        self._player = player if player is not None else AudioPlayer(self)
        self._owns_player = player is None
        self._spec = None
        self._gen = 0
        self._worker = None
        self._stale_workers: list = []
        self._path = ""
        self._scrubbing = False

        self._build_ui()
        self._wire()

        # While playing, refresh from a timer rather than from
        # position_changed: the player only notifies about 10x/s, which makes
        # the spectrum visibly step.
        self._tick = QTimer(self)
        self._tick.setInterval(_REFRESH_MS)
        self._tick.timeout.connect(self._on_tick)

        self.set_audio_source(self._player.source_path)

        # Seed the transport from the player's CURRENT state. Opened from the
        # main window the audio is already loaded, so duration_changed and
        # playing_changed fired long before this window connected: without this
        # the seek bar stays at range (0, 0) — no end time, nothing to drag.
        self._sync_transport()

    def showEvent(self, event) -> None:  # noqa: N802
        """Re-read the player state whenever the window is shown again."""
        super().showEvent(event)
        self._sync_transport()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)

        # ── Header: source ──────────────────────────────────────────────────
        head = QHBoxLayout()
        head.setSpacing(6)

        self._btn_open = QPushButton("Open Audio\u2026")
        self._btn_open.setToolTip("Analyse another audio file")
        head.addWidget(self._btn_open)

        self._lbl_file = QLabel("no audio loaded")
        self._lbl_file.setStyleSheet("color:#9d9d9d; font:12px 'Segoe UI';")
        head.addWidget(self._lbl_file, stretch=1)

        self._progress = QProgressBar()
        self._progress.setFixedWidth(180)
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(True)
        self._progress.setStyleSheet(
            "QProgressBar { background:#252526; border:1px solid #3c3c3c;"
            " border-radius:3px; color:#d4d4d4; font:11px 'Segoe UI';"
            " text-align:center; }"
            "QProgressBar::chunk { background:#007acc; }")
        self._progress.setVisible(False)
        head.addWidget(self._progress)
        vbox.addLayout(head)

        # ── Header: view options ────────────────────────────────────────────
        opts = QHBoxLayout()
        opts.setSpacing(6)

        opts.addWidget(self._mk_label("View"))
        self._cmb_view = QComboBox()
        self._cmb_view.addItem("Bars", "bars")
        self._cmb_view.addItem("Waterfall", "waterfall")
        self._cmb_view.setToolTip("Spectrum analyser bars or a scrolling "
                                  "spectrogram of the same analysis")
        opts.addWidget(self._cmb_view)

        opts.addWidget(self._mk_label("Bands"))
        self._cmb_bands = QComboBox()
        for n in (64, DEFAULT_BANDS, 256):
            self._cmb_bands.addItem(str(n), n)
        self._cmb_bands.setCurrentIndex(1)
        self._cmb_bands.setToolTip(
            "Log-spaced display bands per frame.\n"
            "Changing this re-analyses the file (a few seconds).")
        opts.addWidget(self._cmb_bands)

        opts.addWidget(self._mk_label("Range"))
        self._cmb_floor = QComboBox()
        for floor in (-60.0, -72.0, -90.0, -100.0):
            self._cmb_floor.addItem(f"{floor:.0f} dB", floor)
        self._cmb_floor.setCurrentIndex(2)
        self._cmb_floor.setToolTip("Lowest level shown (top of the scale is "
                                   "always 0 dBFS)")
        opts.addWidget(self._cmb_floor)

        opts.addWidget(self._mk_label("Span"))
        self._cmb_span = QComboBox()
        for secs in (2, 4, 8, 15, 30):
            self._cmb_span.addItem(f"{secs} s", secs * 1000)
        self._cmb_span.setCurrentIndex(2)
        self._cmb_span.setToolTip("Waterfall time window (wheel over the "
                                  "waterfall does the same)")
        opts.addWidget(self._cmb_span)

        opts.addStretch(1)
        self._lbl_info = QLabel("")
        self._lbl_info.setStyleSheet("color:#666666; font:11px 'Consolas';")
        opts.addWidget(self._lbl_info)
        vbox.addLayout(opts)

        # ── The view ────────────────────────────────────────────────────────
        self._view = SpectrumView()
        vbox.addWidget(self._view, stretch=1)

        # ── Transport (drives the shared player, or our own) ────────────────
        row = QHBoxLayout()
        row.setSpacing(6)

        self._btn_play = QPushButton("\u25b6")
        self._btn_play.setFixedWidth(34)
        self._btn_play.setToolTip("Play / pause (same transport as the main window)")
        self._btn_stop = QPushButton("\u25a0")
        self._btn_stop.setFixedWidth(34)
        self._btn_stop.setToolTip("Stop")
        row.addWidget(self._btn_play)
        row.addWidget(self._btn_stop)

        self._lbl_pos = QLabel("0:00.00")
        self._lbl_pos.setFixedWidth(68)
        self._lbl_pos.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_pos.setStyleSheet(
            "font:11px 'Consolas', monospace; color:#aaaaaa;")
        row.addWidget(self._lbl_pos)

        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 0)
        self._seek.setStyleSheet(_SEEK_STYLE)
        row.addWidget(self._seek, stretch=1)

        self._lbl_dur = QLabel("0:00.00")
        self._lbl_dur.setFixedWidth(62)
        self._lbl_dur.setStyleSheet(
            "font:11px 'Consolas', monospace; color:#555555;")
        row.addWidget(self._lbl_dur)
        vbox.addLayout(row)

    @staticmethod
    def _mk_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#777777; font:11px 'Segoe UI';")
        return lbl

    def _wire(self) -> None:
        self._btn_open.clicked.connect(self._on_open)
        self._cmb_view.currentIndexChanged.connect(
            lambda _i: self._view.set_view_mode(self._cmb_view.currentData()))
        self._cmb_floor.currentIndexChanged.connect(
            lambda _i: self._view.set_db_floor(self._cmb_floor.currentData()))
        self._cmb_span.currentIndexChanged.connect(
            lambda _i: self._view.set_time_span(self._cmb_span.currentData()))
        self._cmb_bands.currentIndexChanged.connect(self._on_bands_changed)

        self._view.seek_requested.connect(self._on_seek)

        self._btn_play.clicked.connect(self._on_play)
        self._btn_stop.clicked.connect(self._player.stop)
        self._seek.sliderPressed.connect(lambda: setattr(self, "_scrubbing", True))
        self._seek.sliderReleased.connect(self._on_seek_released)
        self._seek.sliderMoved.connect(self._on_seek_moved)
        self._seek.valueChanged.connect(self._on_seek_value)

        self._player.position_changed.connect(self._on_position)
        self._player.duration_changed.connect(self._on_duration)
        self._player.playing_changed.connect(self._on_playing)
        self._player.source_changed.connect(self.set_audio_source)
        self._player.error_occurred.connect(self._on_error)

    # ── Analysis lifecycle ─────────────────────────────────────────────────

    def load_file(self, path: str) -> None:
        """Load *path* into this window's player (standalone use)."""
        if path and os.path.isfile(path):
            self._player.load(path)

    def set_audio_source(self, path: str) -> None:
        """Analyse *path*; '' or a URL clears the view."""
        self._gen += 1
        gen = self._gen
        self._path = path or ""
        self._spec = None
        self._view.set_spectrogram(None)
        self._stop_worker()

        if not self._path or not os.path.isfile(self._path):
            self._lbl_file.setText(
                self._path or "no audio loaded")
            self._lbl_info.setText("")
            self.setWindowTitle("Echoes \u2014 FFT Spectrum")
            return

        self._lbl_file.setText(os.path.basename(self._path))
        self.setWindowTitle(f"Echoes \u2014 FFT Spectrum \u2014 "
                            f"{os.path.basename(self._path)}")
        if not ffmpeg_available():
            self._lbl_info.setText("ffmpeg missing \u2014 cannot analyse")
            return

        self._progress.setValue(0)
        self._progress.setFormat("analysing\u2026 %p%")
        self._progress.setVisible(True)
        w = SpectrumWorker(gen, self._path, self._cmb_bands.currentData(), self)
        w.progressed.connect(self._on_progress)
        w.done.connect(self._on_analysed)
        w.finished.connect(self._reap_worker)
        self._worker = w
        w.start()

    def _stop_worker(self) -> None:
        """Cancel the running analysis (it is superseded by a new source)."""
        w = self._worker
        self._worker = None
        if w is not None:
            w.cancel()
            self._stale_workers.append(w)

    def _reap_worker(self) -> None:
        w = self.sender()
        if w in self._stale_workers:
            self._stale_workers.remove(w)
        if w is self._worker:
            self._worker = None
        w.deleteLater()

    def _on_progress(self, percent: int, stage: str) -> None:
        self._progress.setFormat(f"{stage}\u2026 %p%")
        self._progress.setValue(percent)

    def _on_analysed(self, gen: int, spec) -> None:
        if gen != self._gen:
            return                                  # superseded
        self._progress.setVisible(False)
        self._spec = spec
        self._view.set_spectrogram(spec)
        if spec is None:
            self._lbl_info.setText("analysis failed")
            return
        self._view.set_db_floor(self._cmb_floor.currentData())
        self._view.set_time_span(self._cmb_span.currentData())
        self._lbl_info.setText(
            f"{spec.frames} frames \u00b7 {spec.frame_ms:.0f} ms \u00b7 "
            f"{spec.bin_hz:.1f} Hz/bin")

    def _on_bands_changed(self, _index: int) -> None:
        if self._path and os.path.isfile(self._path):
            self.set_audio_source(self._path)

    def _on_open(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SOURCE_EXTS))
        start = os.path.dirname(self._path) if self._path else ""
        path, _sel = QFileDialog.getOpenFileName(
            self, "Open audio for analysis", start,
            f"Audio files ({exts});;All files (*)")
        if path:
            self._player.load(path)

    # ── Transport slots ────────────────────────────────────────────────────

    def _sync_transport(self) -> None:
        """
        Push the player's current duration / position / play state into the
        transport row.

        Needed because the player may have been loaded (and already emitted
        ``duration_changed``) before this window existed — the signals this
        window connects are not replayed by Qt.
        """
        self._on_duration(self._player.duration)
        self._on_position(self._player.position)
        self._on_playing(self._player.is_playing)

    def _on_play(self) -> None:
        if self._player.is_playing:
            self._player.pause()
        else:
            self._player.play()

    def _on_playing(self, playing: bool) -> None:
        self._btn_play.setText("\u23f8" if playing else "\u25b6")
        if playing:
            self._tick.start()
        else:
            self._tick.stop()
            self._on_tick()

    def _on_tick(self) -> None:
        """Poll the position ~30x/s for a smooth spectrum."""
        self._on_position(self._player.position)

    def _on_position(self, ms: int) -> None:
        self._lbl_pos.setText(_fmt_ms(ms))
        if not self._scrubbing:
            self._seek.blockSignals(True)
            self._seek.setValue(int(ms))
            self._seek.blockSignals(False)
        self._view.set_position(int(ms))

    def _on_duration(self, ms: int) -> None:
        self._lbl_dur.setText(_fmt_ms(ms))
        self._seek.setRange(0, max(0, int(ms)))

    def _on_seek(self, ms: int) -> None:
        self._player.seek(int(ms))
        self._view.set_position(int(ms))

    def _on_seek_moved(self, value: int) -> None:
        self._lbl_pos.setText(_fmt_ms(value))
        self._view.set_position(int(value))

    def _on_seek_value(self, value: int) -> None:
        """
        Seek on user-driven slider changes — a click on the groove or the
        mouse wheel, which change the value without a handle drag.

        Programmatic updates run with signals blocked (see ``_on_position``),
        and a handle drag is previewed by ``_on_seek_moved`` and committed
        once on release, so this only handles the remaining interactions.
        """
        if self._scrubbing:
            return
        self._lbl_pos.setText(_fmt_ms(value))
        self._view.set_position(int(value))
        self._player.seek(int(value))

    def _on_seek_released(self) -> None:
        self._scrubbing = False
        self._player.seek(int(self._seek.value()))

    def _on_error(self, msg: str) -> None:
        if msg:
            self._lbl_info.setText(f"audio error: {msg}")

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        """
        Stop the analysis thread before the widgets go away.

        A running worker would otherwise emit into a half-destroyed window;
        it polls its cancel flag every 64 frames, so the wait is short.
        """
        self._tick.stop()
        self._gen += 1                       # ignore any in-flight result
        for w in [self._worker, *self._stale_workers]:
            if w is not None:
                w.cancel()
        for w in [self._worker, *self._stale_workers]:
            if w is not None and w.isRunning():
                w.wait(3000)
        self._worker = None
        self._stale_workers = []
        super().closeEvent(event)
