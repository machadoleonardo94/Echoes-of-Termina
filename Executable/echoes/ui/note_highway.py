"""
Note Highway — top-down scrolling LED timeline, Clone Hero–style.

Visual reference: assets/echoes_timesheet.dxf
  – Rectangle 166 × 162 units wide with 28 evenly-spaced circles near the
    bottom edge; that row represents the "now" line.

Screen layout (bottom → top = now → future)
─────────────────────────────────────────────────────────
  top edge  ──────────────────────────── ← pos_ms + 2 beats
            ── ── ── quarter-beat marks (dim horizontal lines)
            ━━━━━━━━ beat marks (brighter horizontal lines)
            ●  ●    ●  command markers (coloured per LED)
  now line  ════════════════════════════ ← pos_ms  (blue rule)
  LED row   ○  ○  ○  …  ○  (28 circles, filled with current colour)
  bottom edge ────────────────────────────────────────────
─────────────────────────────────────────────────────────

The widget receives:
  update_position(pos_ms)  — current playback position
  update_commands(commands) — full parsed command list from ScriptEngine
  update_leds(states)       — current LED colour snapshot {zone_id:(r,g,b)}
  set_beat_ms(beat_ms)      — milliseconds per beat (from BPM spinbox)
"""
from __future__ import annotations

import math

from PySide6.QtCore    import Qt, QRectF, QPointF
from PySide6.QtGui     import (
    QBrush, QColor, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import QWidget

from core.script_engine import FadeCommand, RampCommand, SetCommand

# ── Constants ────────────────────────────────────────────────────────────────

_NUM_LEDS       = 28
_LOOKAHEAD      = 2.0    # beats visible ahead of the now-line
_LED_R_FRAC     = 0.30   # LED-circle radius as fraction of lane width

_BG             = QColor("#1a1a1a")
_LANE_A         = QColor("#1c1c1c")
_LANE_B         = QColor("#202020")
_NOW_LINE       = QColor("#3a7ab8")
_BEAT_LINE      = QColor("#4a4a4a")
_HALF_LINE      = QColor("#383838")
_QBEAT_LINE     = QColor("#2c2c2c")
_LED_RING_DIM   = QColor("#3c3c3c")
_LED_FILL_DIM   = QColor("#252525")

# marker sizes relative to lane width
_MARKER_R_FRAC  = 0.28   # radius of SET/FADE dot markers
_RAMP_W_FRAC    = 0.55   # width of RAMP bar as fraction of lane width


class NoteHighway(QWidget):
    """
    Scrolling top-down LED note highway.

    X axis — 28 LED lanes, left to right (LED 1 → LED 28).
    Y axis — time: bottom = now, top = now + 2 beats.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pos_ms    : int   = 0
        self._beat_ms   : float = 500.0   # 120 BPM default
        self._commands  : list  = []
        self._led_states: dict  = {}      # {zone_id: (r, g, b)}
        self.setMinimumSize(200, 60)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_position(self, pos_ms: int) -> None:
        self._pos_ms = pos_ms
        self.update()

    def update_commands(self, commands: list) -> None:
        self._commands = commands
        self.update()

    def update_leds(self, states: dict) -> None:
        self._led_states = states
        self.update()

    def set_beat_ms(self, beat_ms: float) -> None:
        self._beat_ms = max(1.0, float(beat_ms))
        self.update()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _time_to_y(self, t_ms: float, highway_h: float, window_ms: float) -> float:
        """Map an absolute timestamp to a Y screen coordinate.
        now → highway_h (bottom), now + window_ms → 0 (top).
        """
        t_rel  = t_ms - self._pos_ms
        y_frac = t_rel / window_ms           # 0 = bottom, 1 = top
        return highway_h * (1.0 - y_frac)

    def _lane_cx(self, led_index: int, lane_w: float) -> float:
        """Centre-X of lane for LED with 0-based index."""
        return led_index * lane_w + lane_w * 0.5

    # ── Qt overrides ─────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h     = self.width(), self.height()
        lane_w   = w / _NUM_LEDS
        led_r    = lane_w * _LED_R_FRAC

        # LED strip occupies a fixed band at the bottom
        strip_h  = max(led_r * 2.6, 26.0)
        hw_h     = max(h - strip_h, 1.0)   # highway height (note area)
        window_ms = _LOOKAHEAD * self._beat_ms

        # ── Background ────────────────────────────────────────────────────────
        painter.fillRect(0, 0, w, h, _BG)

        # ── Alternating lane backgrounds ──────────────────────────────────────
        for i in range(_NUM_LEDS):
            col = _LANE_A if i % 2 == 0 else _LANE_B
            painter.fillRect(QRectF(i * lane_w, 0.0, lane_w, hw_h), col)

        # ── Beat / quarter-beat grid lines ────────────────────────────────────
        # Align lines to the actual beat grid so they stay fixed while notes scroll
        quarter_ms = self._beat_ms / 4.0

        # First quarter-beat tick that is >= pos_ms
        if quarter_ms > 0:
            first_tick = math.ceil(self._pos_ms / quarter_ms) * quarter_ms
        else:
            first_tick = self._pos_ms

        t = first_tick
        while t <= self._pos_ms + window_ms:
            y = self._time_to_y(t, hw_h, window_ms)
            if 0.0 <= y <= hw_h:
                # What subdivision is this tick?
                beat_idx = round(t / self._beat_ms)
                half_idx = round(t / (self._beat_ms * 0.5))
                q_idx    = round(t / quarter_ms)

                if abs(t - beat_idx * self._beat_ms) < 0.5:
                    pen = QPen(_BEAT_LINE, 1.2)
                elif abs(t - half_idx * self._beat_ms * 0.5) < 0.5:
                    pen = QPen(_HALF_LINE, 0.8)
                else:
                    pen = QPen(_QBEAT_LINE, 0.5)

                painter.setPen(pen)
                painter.drawLine(QPointF(0.0, y), QPointF(float(w), y))
            t += quarter_ms

        # ── Command markers ───────────────────────────────────────────────────
        now_ms = self._pos_ms
        end_ms = now_ms + window_ms
        marker_r = lane_w * _MARKER_R_FRAC
        ramp_w   = lane_w * _RAMP_W_FRAC

        painter.setPen(Qt.PenStyle.NoPen)

        for cmd in self._commands:
            t_start = cmd.time_ms

            # ── Visibility (FADE has its own check below) ─────────────────────
            if isinstance(cmd, RampCommand):
                if not (t_start < end_ms and t_start + cmd.duration_ms > now_ms):
                    continue
            elif isinstance(cmd, FadeCommand):
                pass   # handled per-block
            else:
                if not (now_ms <= t_start <= end_ms):
                    continue

            y_trig = self._time_to_y(t_start, hw_h, window_ms)

            # ── Per-type rendering ────────────────────────────────────────────
            if isinstance(cmd, SetCommand):
                r, g, b = cmd.r, cmd.g, cmd.b
                if r == 0 and g == 0 and b == 0:
                    continue   # transparent — nothing to draw
                # Solid bar from the trigger up to the top of the highway.
                # SET holds at full brightness indefinitely (no decay), so the
                # bar has no gradient; it extends to the window edge to convey
                # that the colour will persist past the visible lookahead.
                bar_w = lane_w * _RAMP_W_FRAC
                bar_h = max(y_trig, 2.0)        # y_trig = pixels from top to trigger
                # Body: slightly transparent so beat-grid lines remain visible
                painter.setBrush(QBrush(QColor(r, g, b, 180)))
                for led in cmd.leds:
                    idx = led - 1
                    if not (0 <= idx < _NUM_LEDS):
                        continue
                    cx = self._lane_cx(idx, lane_w)
                    painter.drawRect(QRectF(cx - bar_w * 0.5, 0.0, bar_w, bar_h))
                # Bright cap at the trigger edge (note-head)
                painter.setBrush(QBrush(QColor(r, g, b, 255)))
                for led in cmd.leds:
                    idx = led - 1
                    if not (0 <= idx < _NUM_LEDS):
                        continue
                    cx = self._lane_cx(idx, lane_w)
                    painter.drawRect(QRectF(
                        cx - bar_w * 0.5, y_trig - 2.0, bar_w, 3.0))

            elif isinstance(cmd, FadeCommand):
                r, g, b = cmd.r, cmd.g, cmd.b
                if r == 0 and g == 0 and b == 0:
                    continue   # transparent
                # Duration until integer-truncated decay reaches ≤ 2
                max_val = max(r, g, b)
                if cmd.percent > 0 and cmd.percent < 100 and max_val > 2:
                    decay = (100 - cmd.percent) / 100.0
                    t_s   = math.log(2.0 / max_val) / math.log(decay)
                    fade_end_ms = t_start + t_s * 1000.0
                else:
                    fade_end_ms = t_start + 50.0

                # Skip if not yet started or fully decayed
                if t_start > end_ms or fade_end_ms < now_ms:
                    continue

                # Anchor the bar bottom to max(trigger, now) so the decaying
                # tail stays visible above the now-line after the note fires
                eff_start = max(float(t_start), float(now_ms))
                elapsed_s = (eff_start - t_start) / 1000.0
                if elapsed_s > 0 and 0 < decay < 1:
                    fac   = decay ** elapsed_s
                    r_now = int(r * fac)
                    g_now = int(g * fac)
                    b_now = int(b * fac)
                else:
                    r_now, g_now, b_now = r, g, b

                y_bot    = self._time_to_y(eff_start, hw_h, window_ms)
                y_top    = self._time_to_y(fade_end_ms, hw_h, window_ms)
                rect_top = max(0.0, min(y_bot, y_top))
                rect_bot = min(hw_h, max(y_bot, y_top))
                rect_h   = max(rect_bot - rect_top, 2.0)
                bar_w    = lane_w * _RAMP_W_FRAC

                for led in cmd.leds:
                    idx = led - 1
                    if not (0 <= idx < _NUM_LEDS):
                        continue
                    cx = self._lane_cx(idx, lane_w)
                    # Y gradient: current decayed colour at bottom → transparent at top
                    grad = QLinearGradient(cx, rect_bot, cx, rect_top)
                    grad.setColorAt(0.0, QColor(r_now, g_now, b_now, 230))
                    grad.setColorAt(1.0, QColor(r_now, g_now, b_now, 0))
                    painter.setBrush(QBrush(grad))
                    painter.drawRect(QRectF(cx - bar_w * 0.5, rect_top, bar_w, rect_h))

            elif isinstance(cmd, RampCommand):
                r,  g,  b  = cmd.r0, cmd.g0, cmd.b0
                r1, g1, b1 = cmd.r1, cmd.g1, cmd.b1

                t_end    = t_start + cmd.duration_ms
                y_end    = self._time_to_y(t_end, hw_h, window_ms)
                rect_top = max(0.0, min(y_trig, y_end))
                rect_bot = min(hw_h, max(y_trig, y_end))
                rect_h   = max(rect_bot - rect_top, 2.0)

                c0 = QColor(r,  g,  b,  0 if (r  == 0 and g  == 0 and b  == 0) else 230)
                c1 = QColor(r1, g1, b1, 0 if (r1 == 0 and g1 == 0 and b1 == 0) else 230)

                for led in cmd.leds:
                    idx = led - 1
                    if not (0 <= idx < _NUM_LEDS):
                        continue
                    cx = self._lane_cx(idx, lane_w)
                    x0 = cx - ramp_w * 0.5
                    x1 = cx + ramp_w * 0.5
                    # Diagonal gradient: bottom-left=color0, top-right=color1
                    grad = QLinearGradient(x0, rect_bot, x1, rect_top)
                    grad.setColorAt(0.0, c0)
                    grad.setColorAt(1.0, c1)
                    painter.setBrush(QBrush(grad))
                    painter.drawRect(QRectF(x0, rect_top, ramp_w, rect_h))

        # ── "Now" line ────────────────────────────────────────────────────────
        painter.setPen(QPen(_NOW_LINE, 2.0))
        painter.drawLine(QPointF(0.0, hw_h), QPointF(float(w), hw_h))

        # ── LED strip ─────────────────────────────────────────────────────────
        strip_top = hw_h
        cy = strip_top + strip_h * 0.5

        for i in range(_NUM_LEDS):
            cx    = self._lane_cx(i, lane_w)
            state = self._led_states.get(i + 1)

            if state and any(c > 0 for c in state):
                r, g, b = state
                # Glow halo
                glow = QColor(r, g, b, 55)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(glow))
                painter.drawEllipse(QRectF(
                    cx - led_r * 1.6, cy - led_r * 1.6,
                    led_r * 3.2, led_r * 3.2))
                # Filled circle
                painter.setBrush(QBrush(QColor(r, g, b)))
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(_LED_FILL_DIM))

            painter.setPen(QPen(_LED_RING_DIM, 1.0))
            painter.drawEllipse(QRectF(
                cx - led_r, cy - led_r,
                led_r * 2.0, led_r * 2.0))

        painter.end()
