"""
Macro store + planner — content behind the Command Toolbox macro buttons 1-9.

A macro is a plain-text block of lines.  Two kinds of line are accepted:

  • Commands — a full Echoes command WITHOUT its timestamp, e.g.
        FADE 255 255 127 99 22
    When the macro is triggered, each command is stamped with an insertion
    time (starting at the current playback position) and added to the
    target track, exactly like the ADD button does for a single line.

  • Skip steps — timing-only lines that advance the insertion cursor:
        SKIP 1/4
    The cursor moves forward by ``x / y`` of a bar, where one bar is
    (beats per bar) × (60 s / BPM) taken from the transport bar.  Nothing
    is inserted for a skip step — it only spaces out the following commands
    in time.  Example (4/4 at 120 BPM → bar = 2 s)::

        FADE 255 255 127 99 22      →  T
        SKIP 1/4                    →  +500 ms
        FADE 255 255 127 99 24      →  T + 0.5 s
        SKIP 2/4                    →  +1 s
        FADE 255 255 127 99 22      →  T + 1.5 s

Blank lines and ``#`` comments are ignored.

Macros persist as JSON in ``~/.echoes/macros.json`` (same folder as the
chart-import preferences).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

NUM_MACROS: int = 9

# ── Default content ──────────────────────────────────────────────────────────
# Macro 1 ships pre-filled with a sample three-flash pattern so the button /
# hotkey path works out of the box.  The remaining macros start empty and can
# be filled in through the per-button editor (right-click a button).

_DEFAULT_NAME_1 = "Flash pattern (sample)"
_DEFAULT_CONTENT_1 = """FADE 255 255 127 99 22
SKIP 1/4
FADE 255 255 127 99 24
SKIP 2/4
FADE 255 255 127 99 22"""


def default_macros() -> list[dict]:
    """Return the nine macros used when no saved file exists yet."""
    return [
        {"name": _DEFAULT_NAME_1, "content": _DEFAULT_CONTENT_1},
        {"name": "Macro 2", "content": ""},
        {"name": "Macro 3", "content": ""},
        {"name": "Macro 4", "content": ""},
        {"name": "Macro 5", "content": ""},
        {"name": "Macro 6", "content": ""},
        {"name": "Macro 7", "content": ""},
        {"name": "Macro 8", "content": ""},
        {"name": "Macro 9", "content": ""},
    ]


# ── Parsing ──────────────────────────────────────────────────────────────────

# "SKIP 1/4", "skip 2/6", "4/4" — the SKIP keyword is optional, the fraction
# is required.  Denominator must be ≥ 1 (a 0 would divide by zero).
_SKIP_RE = re.compile(r'^(?:SKIP\s+)?(\d+)\s*/\s*([1-9]\d*)$', re.IGNORECASE)


def iter_steps(text: str):
    """
    Yield the parsed steps of a macro body, in order.

    Each item is a tuple:
        ('cmd',  line_text)   — command to insert (no timestamp yet)
        ('skip', x, y)        — advance the cursor by x/y of a bar
    Blank lines and '#' comments are dropped.  Lines that start with the
    word SKIP but are not a valid "x/y" fraction are yielded as commands so
    they surface visibly in the editor instead of being silently lost.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = _SKIP_RE.match(line)
        if m:
            yield ('skip', int(m.group(1)), int(m.group(2)))
        else:
            yield ('cmd', line)


def stats(text: str) -> tuple[int, int]:
    """Return (command_count, skip_count) for a macro body."""
    commands = skips = 0
    for step in iter_steps(text):
        if step[0] == 'cmd':
            commands += 1
        else:
            skips += 1
    return commands, skips


# ── Planner ──────────────────────────────────────────────────────────────────

def plan_insertions(steps, start_ms: int, bar_ms: float) -> list[tuple[int, str]]:
    """
    Expand parsed *steps* into timed insertions.

    *start_ms* is the current playback position (the first command lands
    there) and *bar_ms* the length of one bar.  Every skip step advances a
    floating cursor by ``(x / y) * bar_ms``; the following command is stamped
    with the rounded cursor and returned as ``(time_ms, command_text)``.
    """
    out: list[tuple[int, str]] = []
    cursor = float(start_ms)
    for step in steps:
        if step[0] == 'cmd':
            out.append((int(round(cursor)), step[1]))
        else:
            _, x, y = step
            cursor += (x / y) * bar_ms
    return out


# ── Persistence ──────────────────────────────────────────────────────────────

def macros_path() -> Path:
    """~/.echoes/macros.json — mirrors core/chart_import_dialog prefs."""
    return Path.home() / '.echoes' / 'macros.json'


def load_macros(path=None) -> list[dict]:
    """
    Load the nine macros from JSON.  Missing/corrupt files fall back to
    ``default_macros()``; when no file exists the defaults are written out
    first so the user's edits have a stable home.
    """
    p = Path(path) if path else macros_path()
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            data = data.get('macros')
        if isinstance(data, list) and data:
            macros = [dict(m) for m in data]
            while len(macros) < NUM_MACROS:
                macros.append({"name": f"Macro {len(macros) + 1}",
                               "content": ""})
            return macros[:NUM_MACROS]
    except (OSError, ValueError):
        pass

    defaults = default_macros()
    if not p.exists():
        try:
            save_macros(defaults, p)
        except OSError:
            pass   # read-only home folder — macros still work in memory
    return defaults


def save_macros(macros: list[dict], path=None) -> bool:
    """Persist the macros (and their names) to JSON.  Returns success."""
    p = Path(path) if path else macros_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "note": "Echoes command-toolbox macros.  Lines are SET/RAMP/FADE "
                    "commands (no timestamp) or 'SKIP x/y' timing steps.",
            "macros": macros[:NUM_MACROS],
        }
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                     encoding='utf-8')
        return True
    except OSError:
        return False
