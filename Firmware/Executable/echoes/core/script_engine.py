"""
Echoes script engine — parser and evaluator for .echoes light show scripts.

Script format (one command per line):
    M:SS  COMMAND  [PARAMS...]
    0:15  SET  50 0 0  1-2

Supported commands
------------------
SET  R G B  LED-SPEC
    Set LEDs to a solid colour; holds until overwritten.

RAMP  DURATION  R0 G0 B0  R1 G1 B1  LED-SPEC
    Linearly interpolate from colour (R0,G0,B0) to (R1,G1,B1) over DURATION,
    then hold the end colour.  DURATION uses M:SS format.
    Example:  0:05 RAMP 0:03 255 0 0  0 0 255  1-2

FADE  R G B  PERCENT  LED-SPEC
    Exponential (geometric) decay: every second each channel is multiplied by
    (100 - PERCENT) / 100 and truncated to int.  Reaches black naturally.
    PERCENT: integer 1–99  (50 = half-brightness per second).
    Example:  0:05 FADE 100 0 0 50 1-2

LED-SPEC syntax
    5           → single LED (1-based index)
    1-5         → inclusive range
    1,3,5       → comma-separated list (may mix ranges: 1-3,6,8-10)
    *           → all LEDs

Comments: anything after '#' on a line is ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NUM_LEDS: int = 28   # Majora's Mask layout: 28 LED zones

# ── Data types ──────────────────────────────────────────────────────────────


@dataclass
class ParseError:
    line_number: int   # 1-based
    message: str


@dataclass
class SetCommand:
    time_ms: int
    r: int
    g: int
    b: int
    leds: list         # list[int]  — 1-based indices
    line_number: int   # 1-based source line


@dataclass
class RampCommand:
    """Interpolate from (r0,g0,b0) to (r1,g1,b1) over duration_ms, then hold."""
    time_ms    : int
    duration_ms: int
    r0: int; g0: int; b0: int
    r1: int; g1: int; b1: int
    leds       : list
    line_number: int


@dataclass
class FadeCommand:
    """Geometric decay: channel *= (100 - percent) / 100 every second, truncated to int."""
    time_ms    : int
    r: int; g: int; b: int
    percent    : int   # decay % per second (1–99)
    leds       : list
    line_number: int


# ── Low-level helpers ────────────────────────────────────────────────────────

_TS_RE = re.compile(r'^(\d+):([0-5]\d)(?:\.(\d{1,3}))?$')


def _parse_timestamp(token: str) -> int | None:
    """Parse 'M:SS' or 'M:SS.mmm' → milliseconds, or None on failure."""
    m = _TS_RE.match(token)
    if not m:
        return None
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    millis  = int(m.group(3).ljust(3, '0')) if m.group(3) else 0
    return (minutes * 60 + seconds) * 1000 + millis


def _parse_led_spec(token: str) -> list | None:
    """Parse LED spec into a sorted list of 1-based indices, or None on failure."""
    if token == '*':
        return list(range(1, NUM_LEDS + 1))

    leds: list[int] = []
    for part in token.split(','):
        part = part.strip()
        rng  = re.match(r'^(\d+)-(\d+)$', part)
        sngl = re.match(r'^(\d+)$', part)
        if rng:
            a, b = int(rng.group(1)), int(rng.group(2))
            if not (1 <= a <= b <= NUM_LEDS):
                return None
            leds.extend(range(a, b + 1))
        elif sngl:
            n = int(sngl.group(1))
            if not (1 <= n <= NUM_LEDS):
                return None
            leds.append(n)
        else:
            return None

    return leds if leds else None


def format_ms(ms: int) -> str:
    """Format as 'M:SS.cc' (centiseconds, 2 digits) for display and script insertion."""
    ms      = max(0, ms)
    total_s = ms // 1000
    cs      = (ms % 1000) // 10
    return f"{total_s // 60}:{total_s % 60:02d}.{cs:02d}"


def _clamp(v: int) -> int:
    return max(0, min(255, v))


def _lerp(a: int, b: int, t: float) -> int:
    return _clamp(int(a + (b - a) * t))


# ── Hardware-version LED remapping ────────────────────────────────────────────
#
# V1 and V2 share the same 28-zone physical strip; they differ in ordering:
#   V1 — circles 1-16, left/top spikes 17-21, right/bottom spikes 22-26, eyes 27-28
#   V2 — eyes 1-2, right/bottom spikes 3-7, left/top spikes 8-12, circles 13-28
#
# The mapping is non-linear so a lookup table is used.
# Derived from _STRIP_ORDER / _HANDLE_TO_ZONE_V1 / _HANDLE_TO_ZONE_V2 in
# core/majoras_loader.py.

_V1_TO_V2: dict[int, int] = {
    **{v1: v1 + 12 for v1 in range(1,  17)},   # circles:          V1  1-16 → V2 13-28
    **{v1: v1 -  9 for v1 in range(17, 22)},   # left/top spikes:  V1 17-21 → V2  8-12
    **{v1: v1 - 19 for v1 in range(22, 27)},   # right/bot spikes: V1 22-26 → V2  3-7
    27: 1, 28: 2,                               # eyes:             V1 27-28 → V2  1-2
}
_V2_TO_V1: dict[int, int] = {v2: v1 for v1, v2 in _V1_TO_V2.items()}


def _remap_led_number(n: int, to_v2: bool) -> int:
    """Remap one 1-based LED index between V1 and V2."""
    return (_V1_TO_V2 if to_v2 else _V2_TO_V1).get(n, n)


def _collapse_to_ranges(leds: list) -> str:
    """Collapse a sorted list of LED indices into a compact spec string."""
    if not leds:
        return ''
    parts: list[str] = []
    start = end = leds[0]
    for n in leds[1:]:
        if n == end + 1:
            end = n
        else:
            parts.append(f"{start}-{end}" if start != end else str(start))
            start = end = n
    parts.append(f"{start}-{end}" if start != end else str(start))
    return ','.join(parts)


def _remap_led_spec_str(spec: str, to_v2: bool) -> str:
    """Remap all LED numbers in a spec string and collapse back to ranges."""
    leds: list[int] = []
    for part in spec.split(','):
        rng  = re.match(r'^(\d+)-(\d+)$', part.strip())
        sngl = re.match(r'^(\d+)$', part.strip())
        if rng:
            leds.extend(range(int(rng.group(1)), int(rng.group(2)) + 1))
        elif sngl:
            leds.append(int(sngl.group(1)))
        else:
            return spec   # unrecognised fragment — leave line untouched
    return _collapse_to_ranges(sorted(set(_remap_led_number(n, to_v2) for n in leds)))


_LED_SPEC_PATTERN = re.compile(
    r'^[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*$'
)


def remap_hw_version(text: str, to_v2: bool) -> str:
    """
    Remap every LED address in an Echoes script between hardware versions.

    to_v2=True  — V1 → V2  (eyes move from positions 27-28 to 1-2)
    to_v2=False — V2 → V1  (eyes move from positions  1-2 to 27-28)

    Only SET / RAMP / FADE command lines are modified; comments, blank lines,
    and unknown commands are preserved verbatim.  Wildcard ``*`` specs are
    left unchanged because they address all 28 LEDs in both versions.
    """
    out: list[str] = []
    for raw in text.splitlines(keepends=True):
        # Separate trailing newline so we can work on the content only
        stripped = raw.rstrip('\n\r')
        nl       = raw[len(stripped):]

        # Separate inline comment
        if '#' in stripped:
            ci      = stripped.index('#')
            active  = stripped[:ci]
            comment = stripped[ci:]
        else:
            active  = stripped
            comment = ''

        # Tokenise with positions so we can do a precise in-place replacement
        token_matches = list(re.finditer(r'\S+', active))
        if len(token_matches) < 2:
            out.append(raw)
            continue

        # Require a valid timestamp at token 0 and a known command at token 1
        if not re.match(r'^\d+:[0-5]\d(?:\.\d{1,3})?$', token_matches[0].group()):
            out.append(raw)
            continue
        if token_matches[1].group().upper() not in ('SET', 'RAMP', 'FADE'):
            out.append(raw)
            continue

        # LED spec is always the last token
        led_m   = token_matches[-1]
        led_tok = led_m.group()

        if led_tok == '*' or not _LED_SPEC_PATTERN.match(led_tok):
            out.append(raw)
            continue

        new_spec   = _remap_led_spec_str(led_tok, to_v2)
        new_active = active[:led_m.start()] + new_spec + active[led_m.end():]
        out.append(new_active + comment + nl)

    return ''.join(out)


# ── Engine ───────────────────────────────────────────────────────────────────


class ScriptEngine:
    """Parse an Echoes script and evaluate LED states at arbitrary positions."""

    def __init__(self) -> None:
        self.commands: list = []   # list[SetCommand], sorted by time_ms
        self.errors:   list = []   # list[ParseError]

    # ── Parsing ───────────────────────────────────────────────────────────────

    def parse(self, text: str) -> None:
        """Replace internal state with the result of parsing *text*."""
        self.commands = []
        self.errors   = []

        for line_num, raw in enumerate(text.splitlines(), start=1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue

            tokens = line.split()
            if len(tokens) < 2:
                self.errors.append(ParseError(line_num, "Incomplete command"))
                continue

            time_ms = _parse_timestamp(tokens[0])
            if time_ms is None:
                self.errors.append(
                    ParseError(line_num, f"Invalid timestamp '{tokens[0]}'  (expected M:SS)"))
                continue

            cmd = tokens[1].upper()

            if cmd == 'SET':
                if len(tokens) < 6:
                    self.errors.append(
                        ParseError(line_num, "SET requires: R  G  B  LED-SPEC"))
                    continue
                try:
                    r, g, b = int(tokens[2]), int(tokens[3]), int(tokens[4])
                    if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
                        raise ValueError
                except ValueError:
                    self.errors.append(
                        ParseError(line_num, "R G B must be integers in 0\u2013255"))
                    continue
                leds = _parse_led_spec(tokens[5])
                if leds is None:
                    self.errors.append(
                        ParseError(line_num,
                                   f"Invalid LED spec '{tokens[5]}'  "
                                   f"(valid: '5', '1-5', '1,3,5', '*')"))
                    continue
                self.commands.append(SetCommand(time_ms, r, g, b, leds, line_num))

            elif cmd == 'RAMP':
                # RAMP DURATION R0 G0 B0 R1 G1 B1 LED-SPEC  (10 tokens total)
                if len(tokens) < 10:
                    self.errors.append(
                        ParseError(line_num,
                                   "RAMP requires: DURATION  R0 G0 B0  R1 G1 B1  LED-SPEC"))
                    continue
                dur = _parse_timestamp(tokens[2])
                if dur is None or dur <= 0:
                    self.errors.append(
                        ParseError(line_num, f"Invalid duration '{tokens[2]}'  (expected M:SS)"))
                    continue
                try:
                    r0, g0, b0 = int(tokens[3]), int(tokens[4]), int(tokens[5])
                    r1, g1, b1 = int(tokens[6]), int(tokens[7]), int(tokens[8])
                    if not all(0 <= v <= 255 for v in (r0, g0, b0, r1, g1, b1)):
                        raise ValueError
                except ValueError:
                    self.errors.append(
                        ParseError(line_num, "RGB values must be integers in 0\u2013255"))
                    continue
                leds = _parse_led_spec(tokens[9])
                if leds is None:
                    self.errors.append(
                        ParseError(line_num, f"Invalid LED spec '{tokens[9]}'"))
                    continue
                self.commands.append(
                    RampCommand(time_ms, dur, r0, g0, b0, r1, g1, b1, leds, line_num))

            elif cmd == 'FADE':
                # FADE R G B PERCENT LED-SPEC  (7 tokens total)
                if len(tokens) < 7:
                    self.errors.append(
                        ParseError(line_num,
                                   "FADE requires: R  G  B  PERCENT(1-99)  LED-SPEC"))
                    continue
                try:
                    r, g, b = int(tokens[2]), int(tokens[3]), int(tokens[4])
                    if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
                        raise ValueError
                except ValueError:
                    self.errors.append(
                        ParseError(line_num, "R G B must be integers in 0\u2013255"))
                    continue
                try:
                    pct = int(tokens[5])
                    if not (1 <= pct <= 99):
                        raise ValueError
                except ValueError:
                    self.errors.append(
                        ParseError(line_num,
                                   f"PERCENT must be an integer in 1\u201399, got '{tokens[5]}'"))
                    continue
                leds = _parse_led_spec(tokens[6])
                if leds is None:
                    self.errors.append(
                        ParseError(line_num, f"Invalid LED spec '{tokens[6]}'"))
                    continue
                self.commands.append(FadeCommand(time_ms, r, g, b, pct, leds, line_num))

            else:
                self.errors.append(
                    ParseError(line_num, f"Unknown command '{cmd}'"))

        self.commands.sort(key=lambda c: c.time_ms)

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate_at(self, position_ms: int) -> dict:
        """Return ``{led_index: (r, g, b)}`` by replaying all commands ≤ *position_ms*."""
        states: dict = {}
        for cmd in self.commands:
            if cmd.time_ms > position_ms:
                break
            if isinstance(cmd, SetCommand):
                for led in cmd.leds:
                    states[led] = (cmd.r, cmd.g, cmd.b)
            elif isinstance(cmd, RampCommand):
                end_ms = cmd.time_ms + cmd.duration_ms
                if position_ms >= end_ms:
                    color = (cmd.r1, cmd.g1, cmd.b1)
                else:
                    t = (position_ms - cmd.time_ms) / cmd.duration_ms
                    color = (_lerp(cmd.r0, cmd.r1, t),
                             _lerp(cmd.g0, cmd.g1, t),
                             _lerp(cmd.b0, cmd.b1, t))
                for led in cmd.leds:
                    states[led] = color
            elif isinstance(cmd, FadeCommand):
                # Geometric decay: value(t) = int(initial * rate^t), rate = (100-pct)/100
                t    = (position_ms - cmd.time_ms) / 1000.0
                rate = (100 - cmd.percent) / 100.0
                m    = rate ** t
                color = (int(cmd.r * m), int(cmd.g * m), int(cmd.b * m))
                for led in cmd.leds:
                    states[led] = color
        return states

    def get_active_line_at(self, position_ms: int) -> int:
        """Return 1-based line number of the last fired command, or -1 if none."""
        line = -1
        for cmd in self.commands:
            if cmd.time_ms <= position_ms:
                line = cmd.line_number
            else:
                break
        return line

    def error_line_set(self) -> set:
        """Return the set of 1-based line numbers that produced parse errors."""
        return {e.line_number for e in self.errors}
