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

The highway is a preview of the LED strip over time: the shade of a lane at any
height equals what ScriptEngine.evaluate_at() reports for that height's
timestamp, so it always agrees with the LED canvas mask (SET holds, RAMP
interpolation, and the geometric FADE decay are all reproduced exactly).

The widget receives:
  update_position(pos_ms)  — current playback position
  update_commands(commands) — full parsed command list from ScriptEngine
  update_leds(states)       — current LED colour snapshot {zone_id:(r,g,b)}
  set_beat_ms(beat_ms)      — milliseconds per beat (from BPM spinbox)
"""
from __future__ import annotations

import bisect
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

# Every note body is painted with the SAME alpha, so a lane's shade depends
# only on the LED value the engine reports at that instant — a SET and the FADE
# that follows it no longer jump brighter at the note edge.
_BODY_ALPHA = 200
_FADE_GRAD_STOPS = 24


def _lerp_ch(a: int, b: int, f: float) -> int:
    """Interpolate one colour channel the way ScriptEngine._lerp does."""
    return max(0, min(255, int(a + (b - a) * f)))


def fade_value(r: int, g: int, b: int, rate: float, t_start_ms: int,
               t_ms: float) -> tuple:
    """
    Colour of a FADE at *t_ms*, byte-for-byte identical to the decay used by
    ``ScriptEngine.evaluate_at`` (geometric, truncated to int per channel).

    ``rate`` is the per-second multiplier ``(100 - percent) / 100``.
    """
    if not (0.0 < rate < 1.0):
        return (r, g, b)
    elapsed_s = (t_ms - t_start_ms) / 1000.0
    if elapsed_s <= 0.0:
        return (r, g, b)
    m = rate ** elapsed_s
    return (int(r * m), int(g * m), int(b * m))


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
        self._led_cmd_times: dict = {}    # {led: [sorted command time_ms...]}
        self._led_winner   : dict = {}    # {(led, time_ms): winning command}
        # Keep the minimum small so the main splitter can hand the script
        # editor a much wider pane when the user drags it.
        self.setMinimumSize(160, 60)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_position(self, pos_ms: int) -> None:
        self._pos_ms = pos_ms
        self.update()

    def update_commands(self, commands: list) -> None:
        self._commands = commands
        # Per-LED sorted command timestamps, used to cap each command's drawn
        # hold/tail at the next command that overrides the same LED.
        self._led_cmd_times = {i: [] for i in range(1, _NUM_LEDS + 1)}
        for cmd in commands:
            for led in cmd.leds:
                times = self._led_cmd_times.get(led)
                if times is not None:
                    times.append(cmd.time_ms)
        for times in self._led_cmd_times.values():
            times.sort()
        # Charts merged from several tracks often carry two identical commands
        # at the same timestamp.  Painting both stacks their alpha and makes
        # the lane read brighter than the LED really is, so remember which
        # command actually wins each (LED, timestamp) pair.
        self._led_winner: dict = {}
        for cmd in commands:
            for led in cmd.leds:
                self._led_winner[(led, cmd.time_ms)] = cmd
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

    def _next_cmd_ms(self, led: int, t_start: int) -> int | None:
        """Time of the next command affecting *led* after *t_start*, or None."""
        times = self._led_cmd_times.get(led)
        if not times:
            return None
        i = bisect.bisect_right(times, t_start)
        return times[i] if i < len(times) else None

    def _visible_span(self, led: int, t_start: int, hold_end: int | None,
                      now_ms: int, end_ms: int) -> tuple | None:
        """
        Visible time span of one command's effect on *led*.

        The effect starts at *t_start* and runs until *hold_end* (None = the
        command holds indefinitely, e.g. a SET with nothing after it).  It is
        capped at the next command on the same LED, because that command
        overrides the colour and the previous one is no longer visible.

        Returns (vis_bottom_t, vis_top_t) — the portion overlapping the
        visible window [now_ms, end_ms] — or None when nothing is visible.
        """
        nxt = self._next_cmd_ms(led, t_start)
        if hold_end is not None and nxt is not None:
            hold_end = min(hold_end, nxt)
        elif hold_end is None:
            hold_end = nxt          # overridden (or None = persists forever)

        bot_t = max(t_start, now_ms)
        top_t = end_ms if hold_end is None else min(hold_end, end_ms)
        if top_t <= bot_t or top_t <= now_ms or bot_t >= end_ms:
            return None
        return (bot_t, top_t)

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
        ramp_w = lane_w * _RAMP_W_FRAC

        painter.setPen(Qt.PenStyle.NoPen)

        def _rect_from_span(span) -> tuple | None:
            """Map a (bot_t, top_t) time span to a clamped (rect_top, rect_bot)."""
            bot_t, top_t = span
            y_bot = self._time_to_y(bot_t, hw_h, window_ms)
            y_top = self._time_to_y(top_t, hw_h, window_ms)
            rect_top = max(0.0, min(y_bot, y_top))
            rect_bot = min(hw_h, max(y_bot, y_top))
            if rect_bot - rect_top < 0.5:
                return None
            return (rect_top, rect_bot)

        for cmd in self._commands:
            t_start = cmd.time_ms

            # ── Per-type rendering ────────────────────────────────────────────
            if isinstance(cmd, SetCommand):
                r, g, b = cmd.r, cmd.g, cmd.b
                if r == 0 and g == 0 and b == 0:
                    continue   # transparent — nothing to draw
                # SET holds at full brightness until the next command on the
                # same LED (or indefinitely).  Draw that hold so the lane stays
                # lit after the note fires instead of vanishing at the trigger.
                bar_w = lane_w * _RAMP_W_FRAC
                for led in cmd.leds:
                    idx = led - 1
                    if not (0 <= idx < _NUM_LEDS):
                        continue
                    if self._led_winner.get((led, t_start)) is not cmd:
                        continue   # a duplicate command at this instant wins
                    span = self._visible_span(led, t_start, None, now_ms, end_ms)
                    if span is None:
                        continue
                    rect = _rect_from_span(span)
                    if rect is None:
                        continue
                    rect_top, rect_bot = rect
                    cx = self._lane_cx(idx, lane_w)
                    # Body: slightly transparent so beat-grid lines stay visible
                    painter.setBrush(QBrush(QColor(r, g, b, _BODY_ALPHA)))
                    painter.drawRect(QRectF(
                        cx - bar_w * 0.5, rect_top, bar_w,
                        rect_bot - rect_top))
                    # Bright cap at the trigger edge while the note approaches
                    if t_start >= now_ms:
                        y_trig = self._time_to_y(t_start, hw_h, window_ms)
                        if 0.0 <= y_trig <= hw_h:
                            painter.setBrush(QBrush(QColor(r, g, b, 255)))
                            painter.drawRect(QRectF(
                                cx - bar_w * 0.5, y_trig - 2.0, bar_w, 3.0))

            elif isinstance(cmd, FadeCommand):
                r, g, b = cmd.r, cmd.g, cmd.b
                if r == 0 and g == 0 and b == 0:
                    continue   # transparent
                # Time until the integer-truncated decay reaches black (0),
                # matching ScriptEngine.evaluate_at / the LED strip below.
                rate    = (100 - cmd.percent) / 100.0
                max_val = max(r, g, b)
                if 0 < rate < 1 and max_val > 0:
                    t_black_s = math.log(1.0 / max_val) / math.log(rate)
                    fade_end_ms = int(t_start + t_black_s * 1000.0)
                else:
                    fade_end_ms = int(t_start + 50.0)

                for led in cmd.leds:
                    idx = led - 1
                    if not (0 <= idx < _NUM_LEDS):
                        continue
                    if self._led_winner.get((led, t_start)) is not cmd:
                        continue   # a duplicate command at this instant wins
                    # Tail ends at the real black point — or earlier when the
                    # next command on this LED takes over — so the fade ends
                    # in sync with the LED (and the note grid).
                    span = self._visible_span(
                        led, t_start, fade_end_ms, now_ms, end_ms)
                    if span is None:
                        continue
                    rect = _rect_from_span(span)
                    if rect is None:
                        continue
                    bot_t, top_t = span
                    rect_top, rect_bot = rect
                    cx = self._lane_cx(idx, lane_w)
                    # Y gradient following the *real* decay: the shade at every
                    # height equals the engine's LED value at that timestamp.
                    # A two-stop ramp over-reads the much faster exponential
                    # falloff and turns each note edge into a hard step.
                    grad = QLinearGradient(cx, rect_bot, cx, rect_top)
                    n_stops = max(2, min(_FADE_GRAD_STOPS,
                                         int(rect_bot - rect_top) // 2))
                    span_ms = top_t - bot_t
                    for k in range(n_stops + 1):
                        f = k / n_stops
                        fr, fg, fb = fade_value(
                            r, g, b, rate, t_start, bot_t + span_ms * f)
                        grad.setColorAt(f, QColor(fr, fg, fb, _BODY_ALPHA))
                    painter.setBrush(QBrush(grad))
                    painter.drawRect(QRectF(
                        cx - ramp_w * 0.5, rect_top, ramp_w,
                        rect_bot - rect_top))

            elif isinstance(cmd, RampCommand):
                t_end = t_start + cmd.duration_ms
                dur   = float(max(cmd.duration_ms, 1))

                for led in cmd.leds:
                    idx = led - 1
                    if not (0 <= idx < _NUM_LEDS):
                        continue
                    if self._led_winner.get((led, t_start)) is not cmd:
                        continue   # a duplicate command at this instant wins
                    cx = self._lane_cx(idx, lane_w)

                    # ── Interpolation segment ────────────────────────────────
                    span = self._visible_span(led, t_start, t_end, now_ms, end_ms)
                    rect = _rect_from_span(span) if span else None
                    if rect is not None:
                        bot_t, top_t = span
                        rect_top, rect_bot = rect
                        # Stops follow the *whole* ramp, not the clipped part of
                        # it, so a ramp longer than the look-ahead is not
                        # stretched across the visible window.
                        f_lo = min(1.0, max(0.0, (bot_t - t_start) / dur))
                        f_hi = min(1.0, max(0.0, (top_t - t_start) / dur))
                        # Vertical gradient: bottom=color0, top=color1 — same
                        # "shade == engine value" contract as the other notes.
                        grad = QLinearGradient(cx, rect_bot, cx, rect_top)
                        grad.setColorAt(0.0, QColor(
                            _lerp_ch(cmd.r0, cmd.r1, f_lo),
                            _lerp_ch(cmd.g0, cmd.g1, f_lo),
                            _lerp_ch(cmd.b0, cmd.b1, f_lo), _BODY_ALPHA))
                        grad.setColorAt(1.0, QColor(
                            _lerp_ch(cmd.r0, cmd.r1, f_hi),
                            _lerp_ch(cmd.g0, cmd.g1, f_hi),
                            _lerp_ch(cmd.b0, cmd.b1, f_hi), _BODY_ALPHA))
                        painter.setBrush(QBrush(grad))
                        painter.drawRect(QRectF(
                            cx - ramp_w * 0.5, rect_top, ramp_w,
                            rect_bot - rect_top))

                    # ── Hold after the ramp ──────────────────────────────────
                    # The engine keeps the end colour until the next command on
                    # this LED, so the lane must stay lit past the ramp end —
                    # unless another command cut the ramp short, in which case
                    # that command owns the lane and no hold is drawn (which
                    # would otherwise stack alpha on top of it).
                    if max(cmd.r1, cmd.g1, cmd.b1) == 0:
                        continue
                    nxt = self._next_cmd_ms(led, t_start)
                    if nxt is not None and nxt < t_end:
                        continue
                    hspan = self._visible_span(led, t_end, nxt, now_ms, end_ms)
                    hrect = _rect_from_span(hspan) if hspan else None
                    if hrect is None:
                        continue
                    h_top, h_bot = hrect
                    painter.setBrush(QBrush(QColor(
                        cmd.r1, cmd.g1, cmd.b1, _BODY_ALPHA)))
                    painter.drawRect(QRectF(
                        cx - ramp_w * 0.5, h_top, ramp_w, h_bot - h_top))

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
