"""Verify NoteHighway paints the same LED levels the script engine evaluates.

The note highway is a *preview of the LED strip over time*: the shade of a lane
at any visible instant must equal what ScriptEngine.evaluate_at() returns for
that instant.  This tool renders the widget offscreen at a position, then walks
every lane/time sample of the visible window and compares the painted pixel
against the engine value (composited at the body alpha the widget uses).

    python _verify_highway_fade.py [-s script.echoes] [pos_ms ...]

Exit code 0 = every sample within tolerance, 1 = mismatch.
"""
from __future__ import annotations

import bisect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from core.script_engine import ScriptEngine
from ui.note_highway import (
    NoteHighway, _NUM_LEDS, _LOOKAHEAD, _LED_R_FRAC, _BODY_ALPHA,
)

SCRIPT = os.path.join(
    "projects", "Koji Kondo (Erik Correll) - Lost Woods／Saria's Song (Marcky)",
    "lost_woods_stereo.echoes")
BPM = 140.0
BEAT_MS = 60_000.0 / BPM

TOL = 12            # max channel error accepted (0-255 scale)


def _alpha_for(cmd) -> int:
    """Body alpha the widget uses for each command type."""
    return _BODY_ALPHA


def index_commands(eng) -> dict:
    """{led: ([times], [commands])} sorted by time, for O(log n) lookup."""
    table: dict = {led: ([], []) for led in range(1, _NUM_LEDS + 1)}
    for cmd in eng.commands:
        for led in cmd.leds:
            entry = table.get(led)
            if entry is not None:
                entry[0].append(cmd.time_ms)
                entry[1].append(cmd)
    return table


def sample_window(app, eng, table, pos: int):
    W, H = 28 * 24, 900
    hw = NoteHighway()
    hw.resize(W, H)
    hw.set_beat_ms(BEAT_MS)
    hw.update_commands(eng.commands)
    hw.update_leds(eng.evaluate_at(pos))
    hw.update_position(pos)

    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor("#000000"))
    hw.render(img)

    lane_w = W / _NUM_LEDS
    strip_h = max(lane_w * _LED_R_FRAC * 2.6, 26.0)
    hw_h = max(H - strip_h, 1.0)
    window_ms = _LOOKAHEAD * BEAT_MS

    def composited(rgb, alpha, bg):
        return tuple(int(c * alpha / 255 + b * (255 - alpha) / 255)
                     for c, b in zip(rgb, bg))

    worst = (None, None, -1)
    total, n = 0.0, 0
    over = []
    for led in range(1, _NUM_LEDS + 1):
        cx = int((led - 1) * lane_w + lane_w * 0.5)
        bg_val = 28 if (led - 1) % 2 == 0 else 32     # lane A / lane B
        bg = (bg_val, bg_val, bg_val)
        times, cmds = table[led]
        # Sample inside each command segment (avoiding its 1px edges) so a
        # sample never straddles two commands.
        edges = [pos] + [t for t in times if pos < t < pos + window_ms] \
            + [pos + window_ms]
        for seg_lo, seg_hi in zip(edges, edges[1:]):
            if seg_hi - seg_lo < 8:          # too thin to sample reliably
                continue
            i = bisect.bisect_right(times, seg_lo + 0.5) - 1
            cmd = cmds[i] if i >= 0 else None
            # Keep samples clear of segment edges: the widget draws a 3px note
            # head at a SET trigger and the pixel rows there legitimately blend.
            edge_ms = (window_ms / hw_h) * 5.0
            n_samples = 9
            for k in range(1, n_samples + 1):
                t = seg_lo + (seg_hi - seg_lo) * k / (n_samples + 1)
                if t - seg_lo < edge_ms or seg_hi - t < edge_ms:
                    continue
                y = int(round(hw_h * (1.0 - (t - pos) / window_ms)))
                y = max(3, min(int(hw_h) - 4, y))
                # median of 5 rows so 1px grid lines cannot bias the sample
                cols = sorted(
                    (img.pixelColor(cx, yy) for yy in range(y - 2, y + 3)),
                    key=lambda c: max(c.red(), c.green(), c.blue()))
                painted = cols[2]
                pr, pg, pb = painted.red(), painted.green(), painted.blue()

                e = eng.evaluate_at(int(t)).get(led, (0, 0, 0))
                if max(e) == 0:
                    # Fully decayed/off: only the lane background may show (a
                    # black bar at body alpha is indistinguishable from it).
                    err = max(0, max(pr, pg, pb) - bg_val)
                else:
                    er, eg, eb = composited(e, _alpha_for(cmd), bg)
                    err = max(abs(pr - er), abs(pg - eg), abs(pb - eb))
                total += err
                n += 1
                if err > worst[2]:
                    worst = (led, t, err)
                if err > TOL:
                    over.append((led, t, (pr, pg, pb), e, err))
    return worst, total / n, over


def main() -> int:
    app = QApplication([])
    argv = sys.argv[1:]
    script = SCRIPT
    if "-s" in argv:
        i = argv.index("-s")
        script = argv[i + 1]
        del argv[i:i + 2]
    eng = ScriptEngine()
    eng.parse(open(script, encoding="utf-8").read())
    table = index_commands(eng)

    positions = [int(a) for a in argv] or [4000, 9000, 15000, 30000]
    rc = 0
    print(f"script: {os.path.basename(script)}  ({len(eng.commands)} commands)")
    for pos in positions:
        worst, mean, over = sample_window(app, eng, table, pos)
        status = "FAIL" if over else "ok"
        print(f"pos {pos:>7} ms  worst LED {worst[0]} @ {worst[1]:.0f} ms "
              f"err {worst[2]:>3}  mean err {mean:5.1f}  "
              f"{len(over):3d} samples over tolerance  -> {status}")
        for led, t, painted, expected, err in over[:5]:
            print(f"    LED {led:2d} t={t:8.0f} painted={painted} "
                  f"engine={expected} err={err}")
        if over:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
