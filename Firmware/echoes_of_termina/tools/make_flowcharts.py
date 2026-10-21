"""Draw the two-button interface flowcharts used by docs/two-button-ui.md.

Reproducible on purpose: the diagrams are generated, not hand-drawn, so a change to
the gesture map or the state machine can be reflected by editing this file and
re-running it:

    python tools/make_flowcharts.py

Writes PNG (200 dpi) and SVG into docs/diagrams/. Requires matplotlib.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "diagrams")

# Palette: one colour per KIND of thing, so the eye can follow structure instead of
# reading every box.
C_STATE = "#dbe9f7"   # a state the machine can be in
C_DECIDE = "#fdf0d5"  # a decision
C_EVENT = "#dff0d8"   # something emitted to the action layer
C_ACT = "#e8e0f5"     # an action (controls.h)
C_FB = "#d9f2f0"      # a feedback channel
C_NOTE = "#f2f2f2"
EDGE = "#4a4a4a"
ACCENT = "#1f6fb2"


def canvas(w_in, h_in, x_max, y_max):
    fig, ax = plt.subplots(figsize=(w_in, h_in))
    ax.set_xlim(0, x_max)
    ax.set_ylim(0, y_max)
    ax.axis("off")
    fig.patch.set_facecolor("white")
    # Geometry registry for the self-check below. The author of these diagrams
    # cannot see them (no image input on this model), so the script lays them out and
    # then PROVES the layout: no two boxes overlap, no text is wider than its box,
    # nothing sticks out of the canvas.
    global _RECTS, _VIEW, _ARROWS
    _RECTS = []
    _ARROWS = []
    _VIEW = (w_in, h_in, x_max, y_max)
    return fig, ax


_RECTS = []
_ARROWS = []
_VIEW = (1, 1, 100, 100)


def _points_per_unit(axis):
    w_in, h_in, x_max, y_max = _VIEW
    ppu_x = (w_in * 72.0) / x_max
    ppu_y = (h_in * 72.0) / y_max
    return ppu_x, ppu_y


def _register(x, y, w, h, text, fontsize, kind):
    _RECTS.append(dict(x=x, y=y, w=w, h=h, text=text, fontsize=fontsize, kind=kind))


def box(ax, x, y, w, h, text, face=C_STATE, fontsize=10, weight="normal", radius=0.6,
        edge=EDGE, lw=1.1, mono=False, text_color="#111111"):
    """Rounded box centred on (x, y)."""
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                                boxstyle=f"round,pad=0,rounding_size={radius}",
                                linewidth=lw, edgecolor=edge, facecolor=face, zorder=2))
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, zorder=3,
            weight=weight, color=text_color,
            family="DejaVu Sans Mono" if mono else "DejaVu Sans")
    _register(x, y, w, h, text, fontsize, "box")


def diamond(ax, x, y, w, h, text, face=C_DECIDE, fontsize=9):
    ax.add_patch(plt.Polygon([[x, y + h / 2], [x + w / 2, y], [x, y - h / 2], [x - w / 2, y]],
                             closed=True, facecolor=face, edgecolor=EDGE, linewidth=1.1, zorder=2))
    ax.text(x, y, text, ha="center", va="center", fontsize=fontsize, zorder=3)
    _register(x, y, w, h, text, fontsize, "diamond")


def arrow(ax, p1, p2, label="", rad=0.0, color=ACCENT, lw=1.6, fontsize=9,
          label_dx=0, label_dy=0, style="-|>", ls="-"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=14,
                                 connectionstyle=f"arc3,rad={rad}", color=color,
                                 linewidth=lw, zorder=1, linestyle=ls,
                                 shrinkA=2, shrinkB=2))
    if label:
        mx, my = (p1[0] + p2[0]) / 2 + label_dx, (p1[1] + p2[1]) / 2 + label_dy
        ax.text(mx, my, label, ha="center", va="center", fontsize=fontsize, color="#333333",
                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.9), zorder=4)
    _ARROWS.append((p1, p2, label))


def note(ax, x, y, text, fontsize=9, ha="left"):
    ax.text(x, y, text, ha=ha, va="top", fontsize=fontsize, color="#555555", style="italic")
    ppu_x, ppu_y = _points_per_unit(None)
    lines = text.splitlines()
    w = max(len(l) for l in lines) * 0.55 * fontsize / ppu_x
    h = len(lines) * fontsize * 1.25 / ppu_y
    cx = x + w / 2 if ha == "left" else x - w / 2
    _RECTS.append(dict(x=cx, y=y - h / 2, w=w, h=h, text=lines[0], fontsize=fontsize,
                       kind="note"))


def title(ax, x, y, text, fontsize=15):
    ax.text(x, y, text, ha="left", va="top", fontsize=fontsize, weight="bold", color="#1a1a1a")


def check(name):
    """Prove the layout instead of trusting it (see the note in canvas())."""
    ppu_x, ppu_y = _points_per_unit(None)
    problems = []

    # 1. Boxes and notes must not overlap. Notes are included because they are the
    #    one thing that used to be invisible to this check, and an italic caption
    #    sitting on top of a box is exactly the kind of mistake the author cannot see.
    for i, a in enumerate(_RECTS):
        for b in _RECTS[i + 1:]:
            ox = min(a["x"] + a["w"] / 2, b["x"] + b["w"] / 2) - max(a["x"] - a["w"] / 2, b["x"] - b["w"] / 2)
            oy = min(a["y"] + a["h"] / 2, b["y"] + b["h"] / 2) - max(a["y"] - a["h"] / 2, b["y"] - b["h"] / 2)
            if ox > 0.4 and oy > 0.4:
                problems.append(f"OVERLAP {ox:.1f}x{oy:.1f} units: "
                                f"{a['text'].splitlines()[0]!r} <-> {b['text'].splitlines()[0]!r}")

    # 2. Text must fit its box. DejaVu Sans averages ~0.60 em per character at this
    #    weight; monospace a touch more. A 1.2 line height is the usual leading.
    #    Notes are exempt: their registered rectangle IS the text, so the real checks
    #    for them are the overlap and canvas tests below.
    for r in _RECTS:
        if r["kind"] == "note":
            continue
        for line in r["text"].splitlines():
            est_w = len(line) * (0.66 if r["kind"] == "diamond" else 0.60) * r["fontsize"] / ppu_x
            if r["kind"] == "diamond":
                # A diamond's usable width at mid-height is about half its box.
                est_w *= 2.0
            if est_w > r["w"] - 1.2:
                problems.append(f"TEXT WIDE ({est_w:.1f} > {r['w'] - 1.2:.1f}): {line!r}")
        lines = max(1, len(r["text"].splitlines()))
        est_h = lines * r["fontsize"] * 1.25 / ppu_y
        if est_h > r["h"] - 0.8:
            problems.append(f"TEXT TALL ({est_h:.1f} > {r['h'] - 0.8:.1f}): {r['text'].splitlines()[0]!r}")

    # 3. Everything inside the canvas.
    _, _, x_max, y_max = _VIEW
    for r in _RECTS:
        if r["x"] - r["w"] / 2 < 0 or r["x"] + r["w"] / 2 > x_max or \
           r["y"] - r["h"] / 2 < 0 or r["y"] + r["h"] / 2 > y_max:
            problems.append(f"OUT OF CANVAS: {r['text'].splitlines()[0]!r}")

    # 4. Every arrow must start and end on a box. An arrow into empty space is the
    #    other mistake a blind author cannot catch, and it is worse than a cosmetic
    #    one: it says something about the flow that is not true.
    def _dist_to_rect(p, r):
        dx = max(r["x"] - r["w"] / 2 - p[0], 0, p[0] - (r["x"] + r["w"] / 2))
        dy = max(r["y"] - r["h"] / 2 - p[1], 0, p[1] - (r["y"] + r["h"] / 2))
        return (dx * dx + dy * dy) ** 0.5

    for p1, p2, label in _ARROWS:
        for tag, p in (("start", p1), ("end", p2)):
            best = min((_dist_to_rect(p, r) for r in _RECTS if r["kind"] != "note"), default=99)
            if best > 2.0:
                problems.append(f"DANGLING ARROW ({tag}, {best:.1f} units away): "
                                f"({p[0]:.1f},{p[1]:.1f}) {label!r}")

    print(f"[{name}] {len(_RECTS)} boxes, {len(_ARROWS)} arrows — " +
          ("OK" if not problems else f"{len(problems)} PROBLEM(S)"))
    for p in problems:
        print("   ", p)
    return not problems


# ---------------------------------------------------------------------------
# 1. The gesture state machine (services/buttons.h)
# ---------------------------------------------------------------------------

def diagram_state_machine():
    fig, ax = canvas(13.5, 8.1, 100, 60)
    title(ax, 2, 59, "buttons.h — the gesture state machine (one instance per button)")
    note(ax, 2, 55,
         "Polled once per loop: one digitalRead() per button. A level must hold for 25 ms before it is believed,\n"
         "and a press must last 50 ms before it counts as a human rather than electrical noise.")

    # ── Row 1: the release path (click / double / triple) ──────────────────────
    box(ax, 10, 44, 17, 8, "IDLE\nlevel released")
    box(ax, 30, 44, 17, 8, "PRESSED\nsettled pressed")
    arrow(ax, (18.6, 44), (21.4, 44), "press\n(≥ 25 ms)", label_dy=6.2, fontsize=8.5)

    diamond(ax, 52, 44, 20, 11, "multi_gap_ms\n== 0 ?", face=C_DECIDE, fontsize=9)
    arrow(ax, (38.6, 44), (42, 44), "release,\nheld ≥ 50 ms", label_dy=6.4, fontsize=8.5)

    box(ax, 79, 51, 26, 8, "CLICK — emitted now\n(no DOUBLE / TRIPLE exists\nto wait for)",
        face=C_EVENT, fontsize=9)
    arrow(ax, (62, 46.5), (66, 50.4), "yes  ·  PROG", label_dx=1.5, label_dy=4.6, fontsize=8.5)

    box(ax, 79, 36, 26, 9, "BURST: count clicks,\nwait 400 ms, then emit\n1 CLICK · 2 DOUBLE · 3 TRIPLE",
        face=C_EVENT, fontsize=9)
    arrow(ax, (61, 40), (66, 39), "no  ·  EXT", label_dx=0.5, label_dy=3.6, fontsize=8.5)

    # ── Row 2: the hold ladder ────────────────────────────────────────────────
    box(ax, 30, 18, 20, 8, "HOLD\n(at 600 ms)", face=C_EVENT)
    arrow(ax, (30, 39.8), (30, 22.2), "still down", label_dx=8.4, fontsize=8.5, rad=0.0)

    box(ax, 58, 18, 22, 8, "REPEAT\nevery 150 ms", face=C_EVENT)
    arrow(ax, (40.2, 18), (46.8, 18), "volume ramps", label_dy=3.2, fontsize=8.5)

    box(ax, 85, 18, 28, 8, "VERY_LONG (at 4000 ms)\nthe repeat train stops here",
        face=C_EVENT, fontsize=9)
    arrow(ax, (69.2, 18), (70.8, 18), "4 s", label_dy=3.0, fontsize=8.5)

    box(ax, 58, 6, 22, 7, "RELEASE\n(ramp stops,\nlevel persisted)", face=C_EVENT, fontsize=9)
    arrow(ax, (52, 14.2), (56, 9.4), "let go", label_dx=-3.0, label_dy=1.6, fontsize=8.5)
    arrow(ax, (63, 13.9), (60, 9.6), "let go", label_dx=3.6, label_dy=1.4, fontsize=8.5)

    note(ax, 2, 36.5,
         "A press shorter than 50 ms is discarded as\n"
         "electrical noise, and leaves an open burst\n"
         "untouched so a real tap is not disturbed.")
    note(ax, 2, 12,
         "A HOLD swallows the burst it interrupted:\n"
         "press-press-hold is a HOLD, never a DOUBLE\n"
         "followed by a hold.  Letting go of any hold\n"
         "emits RELEASE — HOLD, REPEAT and VERY_LONG\n"
         "alike.")

    fig.savefig(os.path.join(OUT_DIR, "button-state-machine.png"), dpi=200,
                bbox_inches="tight", facecolor="white")
    fig.savefig(os.path.join(OUT_DIR, "button-state-machine.svg"), bbox_inches="tight",
                facecolor="white")
    ok = check("button-state-machine")
    plt.close(fig)
    return ok


# ---------------------------------------------------------------------------
# 2. The shipped gesture map (services/controls.h)
# ---------------------------------------------------------------------------

def diagram_gesture_map():
    fig, ax = canvas(14.0, 9.0, 100, 84)
    title(ax, 2, 83, "The interface as shipped — gesture → action")
    note(ax, 2, 79,
         "PROG is single-purpose: its clicks are instant (multi_gap_ms = 0, so it has no double/triple).\n"
         "EXT carries the ladder, so its clicks wait up to 400 ms for a possible second tap.")

    # headings
    box(ax, 25, 71, 34, 7, "PROG   GPIO0\nactive LOW, press to GND", face="#cfe0f5", weight="bold")
    box(ax, 70, 71, 44, 7, "EXT   GPIO4\nactive HIGH, press to 3V3", face="#cfe0f5", weight="bold")

    rows = [
        ("click", 62,
         "play / pause\n(nothing loaded → play a show)",
         "play when idle, else next song"),
        ("double", 52,
         "— not available —\n(two taps = two clicks)",
         "cycle LED animation (blinks say which)"),
        ("triple", 42,
         "— not available —",
         "switch mode  (reboots the board)"),
        ("hold\n+ repeat", 30,
         "volume DOWN\nramp, strip bar, falling ticks",
         "volume UP\nramp, strip bar, rising ticks"),
        ("very long\n(4 s)", 16,
         "restart the current song\n(the ramp it interrupted is undone)",
         "bring WiFi back after a standalone show"),
    ]

    for name, y, left, right in rows:
        box(ax, 7, y, 12, 8.5, name, face="#eeeeee", fontsize=10, weight="bold")
        box(ax, 29, y, 32, 8.5, left, face=C_ACT, fontsize=9.5)
        box(ax, 72, y, 50, 8.5, right, face=C_ACT, fontsize=9.5)

    note(ax, 2, 8.5,
         "Every action resolves per mode: Bluetooth mode drives the phone over AVRCP (no playlist, no seek,\n"
         "no exact restart); WiFi mode drives the local SD player — and while it plays, the radio is DOWN, so\n"
         "only the buttons can bring WiFi back. \"Restart\" was confirmed to mean the song, not the device.")
    fig.savefig(os.path.join(OUT_DIR, "gesture-map.png"), dpi=200, bbox_inches="tight",
                facecolor="white")
    fig.savefig(os.path.join(OUT_DIR, "gesture-map.svg"), bbox_inches="tight", facecolor="white")
    ok = check("gesture-map")
    plt.close(fig)
    return ok


# ---------------------------------------------------------------------------
# 3. Feedback paths
# ---------------------------------------------------------------------------

def diagram_feedback():
    fig, ax = canvas(14.0, 8.2, 100, 74)
    title(ax, 2, 73, "How a gesture answers back — two feedback channels")
    note(ax, 2, 69,
         "With no screen, these are the only ways an action can be confirmed. The strip works in both modes;\n"
         "the tone rides the Bluetooth stream, so it only exists while the phone is actually sending audio.")

    box(ax, 14, 58, 24, 8, "button gesture\n(buttons.h)", face=C_STATE)
    box(ax, 46, 58, 26, 8, "action\n(controls.h)", face=C_ACT)
    arrow(ax, (26.2, 58), (32.8, 58))

    # channel 1: strip
    box(ax, 20, 40, 34, 10, "STRIP OVERLAY  —  ui_feedback.h\nvolume bar · N blue blinks (N = animation + 1)\n3 white flashes before a mode switch",
        face=C_FB, fontsize=9.5)
    arrow(ax, (40, 53.8), (28, 45.4), "both modes, always", label_dx=-14, label_dy=3.6, fontsize=8.5)

    # channel 2: cue
    box(ax, 68, 40, 44, 10, "A2DP CUE  —  audio_cues.h\nmixed AFTER volume control (equal loudness)\nvolume tick reports level by PITCH, not loudness",
        face=C_FB, fontsize=9.5)
    arrow(ax, (52, 53.8), (62, 45.4), "Bluetooth only,\nwhile audio flows", label_dx=11, label_dy=4.0,
          fontsize=8.5)

    # channel 3: log
    box(ax, 14, 20, 24, 8, "serial log\n[BTN] [CTRL] [CUE]", face=C_NOTE, fontsize=9.5)
    arrow(ax, (44, 53), (20, 24.4), "always", label_dx=-16, label_dy=-6.0, fontsize=8.5, rad=0.12)

    box(ax, 62, 20, 50, 9, "run_lightshow() and run_led_animation() YIELD\nwhile the overlay owns the strip:\n"
                           "each frame ends in show() (~4 ms at 144 LEDs)",
        face=C_NOTE, fontsize=9)
    note(ax, 2, 10,
         "Not shown: the cue cannot play while the phone is paused (no stream to mix into) — including the\n"
         "pause cue itself, because the phone stops sending in response to that very command. The bar covers it.")
    fig.savefig(os.path.join(OUT_DIR, "feedback-paths.png"), dpi=200, bbox_inches="tight",
                facecolor="white")
    fig.savefig(os.path.join(OUT_DIR, "feedback-paths.svg"), bbox_inches="tight", facecolor="white")
    ok = check("feedback-paths")
    plt.close(fig)
    return ok


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {
        "button-state-machine": diagram_state_machine(),
        "gesture-map": diagram_gesture_map(),
        "feedback-paths": diagram_feedback(),
    }
    print()
    for f in sorted(os.listdir(OUT_DIR)):
        p = os.path.join(OUT_DIR, f)
        print(f"{f:32s} {os.path.getsize(p) // 1024:5d} KB")
    print()
    if all(results.values()):
        print("layout self-check: all diagrams clean")
    else:
        raise SystemExit("layout self-check found problems: " +
                         ", ".join(k for k, v in results.items() if not v))
