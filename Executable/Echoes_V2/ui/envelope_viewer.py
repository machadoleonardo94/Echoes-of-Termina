"""
EnvelopeViewer — collapsible/stretchable, time-zoomable audio envelope.

Shows a loudness (RMS) envelope of the currently loaded audio with a faint
peak outline behind it, plus a playhead that tracks playback.  The RMS layer
reveals the song structure even for hot / clipped masters whose peaks sit at
full scale for most of the track.  Interactions:

  • wheel over the body      — zoom the time axis (X) around the cursor
  • drag the body            — pan the visible window (stops following)
  • double-click the body    — zoom back out to the whole track, re-follow
  • drag the top grip        — collapse / stretch the height (persisted)
  • double-click the grip    — toggle between collapsed and last height

While playing (or scrubbing the progress bar) the view follows the playhead
edge-to-edge: as long as the cursor is inside the follow band the view stays
put and the cursor moves across it, and when it reaches the band edge the
view pans just enough to keep it inside — the zoom level is never changed.
The band is deliberately narrow so the view catches up with the cursor
quickly (small dead zone).

The audio is decoded with one fast ffmpeg pass (QAudioDecoder fallback); the
envelope is a per-time-bucket peak/RMS list rebuilt on source changes.
"""
from __future__ import annotations

import os
from array import array

from PySide6.QtCore    import QPointF, QRectF, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui     import QBrush, QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat
from PySide6.QtWidgets import QWidget

# ── Height control ──────────────────────────────────────────────────────────

_MIN_H  = 22    # fully collapsed (thin strip)
_MAX_H  = 220   # fully stretched
_DEF_H  = 64    # default height
_GRIP_H = 8     # top drag handle height

# ── Colours ─────────────────────────────────────────────────────────────────

_BG      = QColor("#161616")
_GRIP_BG = QColor("#252526")
_GRIP_LN = QColor("#3c3c3c")
_ENV     = QColor(78, 201, 176)      # teal — matches the timestamp colour
_PLAY    = QColor("#007acc")         # accent blue — matches the seek bar
_DIM     = QColor("#666666")
_CENTER  = QColor(255, 255, 255, 26)

_BUCKET_MS = 10   # envelope resolution: one peak bucket per 10 ms of audio
_MIN_VIEW_MS = 100  # minimum zoomed-in time window width (X axis)

# Follow band: while the playhead is inside these margins of the visible
# window the view stays static and the cursor moves across it; when it
# reaches a margin the view pans just enough to keep it inside.  Narrower
# margins = smaller dead zone = the view follows the cursor sooner.
_FOLLOW_MARGIN_L = 0.25   # left edge of the follow band (fraction of view)
_FOLLOW_MARGIN_R = 0.25   # right edge of the follow band (fraction of view)


class _EnvelopeWorker(QThread):
    """
    Builds the envelope off the GUI thread with ffmpeg (fast, single pass) —
    used instead of QAudioDecoder, whose tiny-buffer pipeline takes ~20 s for
    a 2.5-minute song.  Falls back to None when ffmpeg is absent.
    """

    done = Signal(int, object, object, int)   # (gen, peaks|None, rms|None, duration_ms)

    def __init__(self, gen: int, path: str, bucket_ms: int, parent=None) -> None:
        super().__init__(parent)
        self._gen = gen
        self._path = path
        self._bucket_ms = bucket_ms

    def run(self) -> None:
        from core.audio_converter import extract_envelope
        res = extract_envelope(self._path, self._bucket_ms)
        if res is None:
            self.done.emit(self._gen, None, None, 0)
        else:
            peaks, rms, duration_ms = res
            self.done.emit(self._gen, peaks, rms, duration_ms)


class EnvelopeViewer(QWidget):
    """
    Loudness-envelope strip (RMS with a faint peak outline) and a draggable
    top grip.  The RMS layer keeps the song structure visible even for hot,
    clipped masters whose peaks sit at full scale.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._peaks: list[float] = []        # per-bucket peak (0..1)
        self._rms:   list[float] = []        # per-bucket RMS level (0..1)
        self._bucket_ms: int = _BUCKET_MS
        self._duration_ms: int = 0
        self._pos_ms: int = 0
        self._frame_idx: int = 0             # running decoded frame counter
        self._busy: bool = False
        self._gen: int = 0                   # decode generation (source changes)
        self._decoder: QAudioDecoder | None = None   # QAudioDecoder fallback
        self._worker: _EnvelopeWorker | None = None  # ffmpeg worker (current)
        self._stale_workers: list = []       # replaced workers awaiting finish

        # ── resizing state ─────────────────────────────────────────────────
        self._resizing = False
        self._grip_down_y = 0.0
        self._h_at_down = 0
        self._stretched_h = _DEF_H

        # ── time-zoom (X axis) state ───────────────────────────────────────
        self._view_start_ms: float = 0.0   # left edge of the visible window
        self._view_dur_ms: float = 0.0     # 0 = whole track visible
        self._panning = False
        self._pan_x0 = 0.0
        self._pan_view_start = 0.0
        self._pan_moved = False
        self._follow = True                # keep the playhead in the follow band

        saved = QSettings().value("envelope/height", _DEF_H)
        try:
            h = max(_MIN_H, min(_MAX_H, int(saved)))
        except (TypeError, ValueError):
            h = _DEF_H
        self._height = h
        self._apply_height(h)
        self.setMouseTracking(True)
        self.setToolTip(
            "Audio envelope\n"
            "wheel: zoom time  \u00b7  drag: pan  \u00b7  "
            "double-click: reset zoom\n"
            "follows playback \u2014 drag the envelope to stop following\n"
            "grip: drag to collapse / stretch  \u00b7  "
            "double-click grip: toggle")

    # ── Public API ─────────────────────────────────────────────────────────

    def set_audio_source(self, path: str) -> None:
        """(Re)build the envelope from *path*; '' or URLs clear the view."""
        self._gen += 1
        gen = self._gen
        self._peaks = []
        self._rms = []
        self._frame_idx = 0
        self._duration_ms = 0
        self._view_start_ms = 0.0
        self._view_dur_ms = 0.0
        self._follow = True
        self._busy = False
        if self._worker is not None:
            self._stale_workers.append(self._worker)
            self._worker = None
        self._stop_decoder()
        if path and os.path.isfile(path):
            self._busy = True
            from core.audio_converter import ffmpeg_available
            if ffmpeg_available():
                # Fast path: one ffmpeg pass in a worker thread.
                w = _EnvelopeWorker(gen, path, self._bucket_ms, self)
                w.done.connect(self._on_worker_done)
                w.finished.connect(self._reap_worker)
                self._worker = w
                w.start()
            else:
                # Slow fallback for machines without ffmpeg.
                dec = QAudioDecoder(self)
                dec.setSource(QUrl.fromLocalFile(path))
                dec.bufferReady.connect(lambda: self._on_buffer(gen))
                dec.finished.connect(lambda: self._on_finished(gen))
                dec.error.connect(lambda _err: self._on_error(gen))
                self._decoder = dec
                dec.start()
        self.update()

    def set_duration(self, ms: int) -> None:
        self._duration_ms = max(0, ms)
        self.update()

    def set_position(self, pos_ms: int) -> None:
        self._pos_ms = max(0, pos_ms)
        if self._follow:
            self._follow_keep_visible(self._pos_ms)
        self.update()

    def follow_position(self, pos_ms: int) -> None:
        """
        Move the cursor to *pos_ms* and keep edge-following from there.

        Used while scrubbing the progress bar — the cursor moves across the
        view and the envelope pans only when it reaches the follow band.
        """
        self._follow = True
        self.set_position(pos_ms)

    # ── Decoding ───────────────────────────────────────────────────────────

    def _on_worker_done(self, gen: int, peaks, rms, duration_ms: int) -> None:
        if gen != self._gen:
            return
        self._busy = False
        if rms:
            self._peaks = peaks or []
            self._rms = rms
            if not self._duration_ms:
                self._duration_ms = duration_ms or len(rms) * self._bucket_ms
        self.update()

    def _reap_worker(self) -> None:
        w = self.sender()
        if w in self._stale_workers:
            self._stale_workers.remove(w)
        if w is self._worker:
            self._worker = None
        w.deleteLater()

    def _stop_decoder(self) -> None:
        dec = self._decoder
        self._decoder = None
        if dec is not None:
            try:
                dec.stop()
            except Exception:
                pass
            dec.deleteLater()

    def _on_buffer(self, gen: int) -> None:
        if gen != self._gen or self._decoder is None:
            return
        buf = self._decoder.read()
        if buf is not None and buf.isValid():
            self._consume_buffer(buf)

    def _on_finished(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._busy = False
        if not self._duration_ms and (self._rms or self._peaks):
            self._duration_ms = (len(self._rms) or len(self._peaks)) * self._bucket_ms
        self.update()

    def _on_error(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._busy = False
        self._peaks = []
        self._rms = []
        self._stop_decoder()
        self.update()

    def _consume_buffer(self, buf) -> None:
        """
        Accumulate per-bucket peak + RMS from one decoded buffer.

        Processes one time-bucket at a time using C-speed min/max over array
        slices (instead of a per-sample Python loop), so a full song decodes
        in well under a second.
        """
        fmt = buf.format()
        if not fmt.isValid():
            return
        ch   = max(1, fmt.channelCount())
        rate = max(1, fmt.sampleRate())
        sf   = fmt.sampleFormat()
        data = bytes(buf.data())

        if sf == QAudioFormat.SampleFormat.Int16:
            samples = array('h')
            samples.frombytes(data)
            div, center = 32768.0, 0
        elif sf == QAudioFormat.SampleFormat.Int32:
            samples = array('i')
            samples.frombytes(data)
            div, center = 2147483648.0, 0
        elif sf == QAudioFormat.SampleFormat.Float:
            samples = array('f')
            samples.frombytes(data)
            div, center = 1.0, 0
        elif sf == QAudioFormat.SampleFormat.UInt8:
            samples = array('B')
            samples.frombytes(data)
            div, center = 128.0, 128
        else:
            return

        fpb = rate * self._bucket_ms / 1000.0   # frames per bucket (may be fractional)
        n_frames = len(samples) // ch
        peaks = self._peaks
        rms   = self._rms
        idx   = self._frame_idx

        start = idx
        while start < idx + n_frames:
            bucket = int(start / fpb)
            end = min(idx + n_frames, int((bucket + 1) * fpb))
            if end <= start:                 # guard fractional-drift edge cases
                end = start + 1
            s0, s1 = start * ch, end * ch

            m = 0.0
            for c in range(ch):
                sl = samples[s0 + c:s1:ch]
                if not sl:
                    continue
                if center:
                    m = max(m, abs(min(sl) - center) / div,
                            abs(max(sl) - center) / div)
                else:
                    m = max(m, abs(min(sl)) / div, abs(max(sl)) / div)
            if m > 1.0:
                m = 1.0

            # RMS over all channels in this bucket
            sl_all = samples[s0:s1]
            count = len(sl_all)
            if count:
                if center:
                    r = (sum((v - center) * (v - center) for v in sl_all)
                         / count) ** 0.5 / div
                else:
                    r = (sum(v * v for v in sl_all) / count) ** 0.5 / div
                if r > 1.0:
                    r = 1.0
            else:
                r = 0.0

            if bucket >= len(peaks):
                peaks.extend([0.0] * (bucket + 1 - len(peaks)))
                rms.extend([0.0] * (bucket + 1 - len(rms)))
            if m > peaks[bucket]:
                peaks[bucket] = m
            if r > rms[bucket]:
                rms[bucket] = r
            start = end

        self._frame_idx = idx + n_frames

    # ── Height control (grip) ──────────────────────────────────────────────

    def _apply_height(self, h: int) -> None:
        h = max(_MIN_H, min(_MAX_H, int(h)))
        if h == self._height and self.height() == h:
            return
        self._height = h
        self.setFixedHeight(h)
        QSettings().setValue("envelope/height", h)
        self.updateGeometry()

    def _on_grip(self, y: float) -> bool:
        return y <= _GRIP_H

    def _env_len(self) -> int:
        """Number of envelope buckets available (rms or peak)."""
        return len(self._rms) or len(self._peaks)

    def _eff_view(self) -> tuple[float, float]:
        """Return (view_start_ms, view_dur_ms) — the visible time window."""
        dur = self._duration_ms or (self._env_len() * self._bucket_ms)
        if dur <= 0:
            return 0.0, 0.0
        vd = self._view_dur_ms if self._view_dur_ms > 0 else dur
        vd = max(1.0, min(vd, dur))
        vs = max(0.0, min(self._view_start_ms, dur - vd))
        return vs, vd

    def _reset_view(self) -> None:
        """Zoom back out to the whole track."""
        self._view_start_ms = 0.0
        self._view_dur_ms = 0.0
        self.update()

    def _pan_span(self):
        """
        Return (duration_ms, view_dur_ms) when the view can pan (zoomed in),
        else None.  view_dur_ms is clamped to a valid value.
        """
        dur = self._duration_ms or (self._env_len() * self._bucket_ms)
        vd  = self._view_dur_ms
        if dur <= 0 or vd <= 0 or vd >= dur - 0.5:
            return None
        return dur, max(1.0, min(vd, dur))

    def _follow_keep_visible(self, pos_ms: float) -> None:
        """
        Pan the view just enough to keep *pos_ms* inside the follow band,
        without re-centring: while the playhead is visible the view stays
        static and the cursor moves across it (a static track bar with the
        envelope scrolling underneath).  The zoom level (view duration) is
        never changed.
        """
        span = self._pan_span()
        if span is None:
            return
        dur, vd = span
        vs   = max(0.0, min(self._view_start_ms, dur - vd))
        band = vs + _FOLLOW_MARGIN_L * vd, vs + (1.0 - _FOLLOW_MARGIN_R) * vd
        if pos_ms < band[0]:
            vs = pos_ms - _FOLLOW_MARGIN_L * vd
        elif pos_ms > band[1]:
            vs = pos_ms - (1.0 - _FOLLOW_MARGIN_R) * vd
        self._view_start_ms = max(0.0, min(vs, dur - vd))

    def mousePressEvent(self, event) -> None:
        if self._on_grip(event.position().y()):
            self._resizing = True
            self._grip_down_y = event.globalPosition().y()
            self._h_at_down = self._height
            event.accept()
            return
        # body: start a pan (only moves the view when zoomed in)
        self._panning = True
        self._pan_moved = False
        self._pan_x0 = event.position().x()
        vs, _vd = self._eff_view()
        self._pan_view_start = vs
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            dy = event.globalPosition().y() - self._grip_down_y
            h  = max(_MIN_H, min(_MAX_H, int(self._h_at_down - dy)))
            if h != self._height:
                self._apply_height(h)
            event.accept()
            return
        if self._panning:
            if not self._pan_moved and \
                    abs(event.position().x() - self._pan_x0) > 3:
                self._pan_moved = True
                self._follow = False     # manual pan overrides follow mode
            vs, vd = self._eff_view()
            if vd > 0:
                dx = (event.position().x() - self._pan_x0) / max(1, self.width())
                self._view_start_ms = self._pan_view_start - dx * vd
                self.update()
            event.accept()
            return
        # hover cursor
        self.setCursor(Qt.CursorShape.SizeVerCursor
                       if self._on_grip(event.position().y())
                       else Qt.CursorShape.OpenHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            self._resizing = False
            event.accept()
            return
        if self._panning:
            self._panning = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._on_grip(event.position().y()):
            if self._height > _MIN_H + 4:
                self._stretched_h = self._height
                self._apply_height(_MIN_H)
            else:
                self._apply_height(self._stretched_h)
            event.accept()
            return
        # body double-click: zoom back out to the whole track and re-follow
        self._follow = True
        self._reset_view()
        event.accept()

    def wheelEvent(self, event) -> None:
        """Zoom the time axis in/out, anchored at the cursor position."""
        if self._on_grip(event.position().y()) or not self._env_len():
            super().wheelEvent(event)
            return
        vs, vd = self._eff_view()
        dur = self._duration_ms or (self._env_len() * self._bucket_ms)
        if vd <= 0 or dur <= 0:
            return
        x      = event.position().x() / max(1, self.width())
        anchor = vs + x * vd
        steps  = event.angleDelta().y() / 120.0
        new_vd = max(_MIN_VIEW_MS, min(dur, vd * (0.85 ** steps)))
        self._view_start_ms = anchor - x * new_vd
        self._view_dur_ms   = new_vd
        self.update()
        event.accept()

    def leaveEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, _BG)

        # ── top grip ───────────────────────────────────────────────────────
        p.fillRect(0, 0, w, _GRIP_H, _GRIP_BG)
        p.setPen(QPen(_GRIP_LN, 1.0))
        p.drawLine(0, _GRIP_H - 1, w, _GRIP_H - 1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_GRIP_LN))
        cx = w // 2
        for i in range(5):
            p.drawEllipse(QRectF(cx - 12 + i * 6, _GRIP_H * 0.5 - 1.5, 3, 3))

        top  = _GRIP_H
        area = h - _GRIP_H
        mid  = top + area / 2.0

        if not self._env_len():
            msg = "loading envelope\u2026" if self._busy else "no audio"
            p.setPen(_DIM)
            p.drawText(QRectF(0, top, w, area),
                       Qt.AlignmentFlag.AlignCenter, msg)
            p.end()
            return

        n   = self._env_len()
        dur = self._duration_ms or (n * self._bucket_ms)
        if dur <= 0:
            p.end()
            return
        vs, vd = self._eff_view()

        # Normalise both layers to the file's peak so the envelope fills the
        # height; the RMS layer then reveals structure even when the peaks
        # are pinned at full scale (hot / clipped masters).
        scale = max(self._peaks) if self._peaks else 1.0
        if scale <= 0:
            scale = 1.0

        # ── envelope: faint peak outline + bright RMS level ────────────────
        pen_peak = QPen(QColor(_ENV.red(), _ENV.green(), _ENV.blue(), 45), 1.0)
        pen_rms  = QPen(QColor(_ENV.red(), _ENV.green(), _ENV.blue(), 165), 1.0)
        for x in range(w):
            t0 = vs + x * vd / w
            t1 = vs + (x + 1) * vd / w
            b0 = int(t0 / self._bucket_ms)
            b1 = min(n, max(b0 + 1, int(t1 / self._bucket_ms)))
            peak = 0.0
            rms_v = 0.0
            for b in range(b0, b1):
                if self._peaks and self._peaks[b] > peak:
                    peak = self._peaks[b]
                if self._rms and self._rms[b] > rms_v:
                    rms_v = self._rms[b]
            if self._peaks:
                p.setPen(pen_peak)
                half = (peak / scale) * area / 2.0
                p.drawLine(QPointF(x + 0.5, mid - half),
                           QPointF(x + 0.5, mid + half))
            if self._rms:
                p.setPen(pen_rms)
                half = (rms_v / scale) * area / 2.0
                p.drawLine(QPointF(x + 0.5, mid - half),
                           QPointF(x + 0.5, mid + half))

        # ── centre line ────────────────────────────────────────────────────
        p.setPen(QPen(_CENTER, 1.0))
        p.drawLine(QPointF(0.0, mid), QPointF(float(w), mid))

        # ── playhead (only when inside the visible window) ─────────────────
        px = (self._pos_ms - vs) / vd * w
        if 0.0 <= px <= w:
            p.setPen(QPen(_PLAY, 1.2))
            p.drawLine(QPointF(px, float(top)), QPointF(px, float(h)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_PLAY))
            p.drawPolygon([
                QPointF(px - 4, float(top)),
                QPointF(px + 4, float(top)),
                QPointF(px,     float(top) + 5),
            ])

        p.end()
