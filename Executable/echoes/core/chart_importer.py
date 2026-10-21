"""
Clone Hero .chart → echoes script converter.

Reads [Song], [SyncTrack] and [ExpertSingle] sections and produces
echoes SET / FADE commands timed to match the note stream.

Fret → LED mapping  (V1 hardware, symmetric spikes)
────────────────────────────────────────────────────
  LED order: central (1–16), spikes (17–26), eyes (27–28)

  Fret 0  →  26, 17  (bottom spikes)
  Fret 1  →  25, 18
  Fret 2  →  24, 19
  Fret 3  →  23, 20
  Fret 4  →  22, 21  (top spikes)

Note handling
─────────────
  Short note  (clamped to ≥ 100 ms)  →  FADE <color> <pct> <leds>
  Long  note  (≥ SHORT_MS threshold) →  SET   <color> <leds>
                                         FADE <color> <pct> <leds>   (FADE_LEAD_MS before end)

  The FADE percent is computed per note so the LED reaches black exactly when
  the note ends (the LED brightness matches the note timeline).  Notes too
  short for even the fastest decay (99 %/s) fall back to 99 %.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional
# ── Constants ─────────────────────────────────────────────────────────────────

FRET_TO_LEDS: dict[int, str] = {
    0: '26,17',
    1: '25,18',
    2: '24,19',
    3: '23,20',
    4: '22,21',
}

SHORT_MS       = 150    # notes shorter than this are "flash" notes
MIN_FLASH_MS   = 100    # minimum duration for a flash note
FADE_PERCENT   = 80     # fallback decay % per second for FADE command
FADE_LEAD_MS   = 400    # how early before a long note's end to issue FADE


def _fade_percent_for_black(max_val: int, t_ms: float, fallback: int) -> int:
    """
    FADE percent (1-99) whose exponential decay reaches black (integer
    brightness 0) within *t_ms* milliseconds.

    This makes the LED brightness match the note: it is lit while the note
    plays and reaches black when the note ends.  The user-configured
    *fallback* rate is kept as a floor (fades never decay slower than asked);
    the computed value is capped at 99 (the fastest possible) when the note is
    too short to reach black in time.
    """
    if max_val <= 0 or t_ms <= 0:
        return max(1, min(99, fallback))
    t = t_ms / 1000.0
    # brightness(t) = max_val * ((100 - pct)/100) ** t  <  1  →  decay < max_val ** (-1/t)
    pct = math.ceil(100.0 * (1.0 - max_val ** (-1.0 / t)))
    pct = max(pct, max(1, min(99, fallback)))
    return max(1, min(99, pct))

# ── Fret / LED presets ────────────────────────────────────────────────────────

VALID_FRETS: frozenset[int] = frozenset({0, 1, 2, 3, 4})

# Named presets for fret → LED mapping.
# LED order: central (1–16), spikes (17–26), eyes (27–28)
LED_PRESETS: dict[str, dict[int, str]] = {
    'Spikes': {0: '26,17', 1: '25,18', 2: '24,19', 3: '23,20', 4: '22,21'},
    'Center': {0: '8,9',   1: '7,10',  2: '6,11',  3: '5,12',  4: '4,13'},
    'Eyes':   {0: '27,28', 1: '27,28', 2: '27,28', 3: '27,28', 4: '27,28'},
    'All':    {0: '*',     1: '*',     2: '*',     3: '*',     4: '*'},
}

# Clone Hero standard fret colors used as import defaults
GH_FRET_COLORS: dict[int, tuple[int, int, int]] = {
    0: ( 45, 181,  45),   # green
    1: (224,  32,  32),   # red
    2: (224, 200,   0),   # yellow
    3: ( 32,  96, 224),   # blue
    4: (224, 112,   0),   # orange
}


# ── Per-track import configuration ───────────────────────────────────────────

@dataclass
class TrackConfig:
    """Configuration for importing one .chart track section."""
    track_name:   str
    enabled:      bool = True
    fret_colors:  dict[int, tuple[int, int, int]] = field(
        default_factory=lambda: dict(GH_FRET_COLORS))
    fret_leds:    dict[int, str] = field(
        default_factory=lambda: dict(LED_PRESETS['Spikes']))
    short_ms:     int  = SHORT_MS
    fade_percent: int  = FADE_PERCENT
    fade_lead_ms: int  = FADE_LEAD_MS

    def get_fret_map(self) -> dict[int, str]:
        return self.fret_leds

    def get_fret_color(self, fret: int) -> tuple[int, int, int]:
        return self.fret_colors.get(fret, (255, 255, 255))


# ── .chart parser ─────────────────────────────────────────────────────────────

def get_chart_info(
    text: str,
) -> tuple[dict[str, str], list[tuple[str, int]], float, int, int]:
    """
    Quick scan of a .chart file.

    Returns
    -------
    song_meta    : dict  — {key: value} from [Song]
    tracks       : list  — (section_name, note_count) in file order,
                           only sections that contain playable N entries (frets 0–4)
    initial_bpm  : float — BPM at tick 0 from [SyncTrack] (default 120.0)
    ts_num       : int   — time-signature numerator at tick 0 (default 4)
    ts_den       : int   — time-signature denominator at tick 0 (default 4)

    Tempo format (spec): ``<tick> = B <millibpm>``
    where the last three decimal digits of millibpm are the fractional part
    (e.g. 140000 → 140.000 BPM).

    Time-signature format (spec): ``<tick> = TS <numerator> [<denominator_exp>]``
    where denominator = 2 ** denominator_exp  (default exp = 2 → x/4).
    """
    song_meta:   dict[str, str] = {}
    track_order: list[str]      = []
    note_counts: dict[str, int] = {}

    initial_bpm = 120.0
    ts_num      = 4
    ts_den      = 4
    _bpm_found  = False
    _ts_found   = False
    section: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^\[(\w+)\]$', line)
        if m:
            section = m.group(1)
            continue
        if line == '{':
            continue
        if line == '}':
            section = None
            continue

        if section == 'Song':
            m = re.match(r'^(\w+)\s*=\s*"?([^"]*)"?$', line)
            if m:
                song_meta[m.group(1)] = m.group(2).strip('"')

        elif section == 'SyncTrack':
            # Initial BPM — only the tick-0 entry (spec requires one at tick 0)
            if not _bpm_found:
                m = re.match(r'^0\s*=\s*B\s+(\d+)$', line)
                if m:
                    initial_bpm = int(m.group(1)) / 1000.0
                    _bpm_found  = True

            # Initial time signature — only the tick-0 entry
            if not _ts_found:
                m = re.match(r'^0\s*=\s*TS\s+(\d+)(?:\s+(\d+))?$', line)
                if m:
                    ts_num   = int(m.group(1))
                    exp      = int(m.group(2)) if m.group(2) else 2
                    ts_den   = 2 ** exp
                    _ts_found = True

        elif section and section != 'SyncTrack':
            m = re.match(r'^\d+\s*=\s*N\s+(\d+)\s+\d+$', line)
            if m and int(m.group(1)) in VALID_FRETS:
                if section not in note_counts:
                    note_counts[section] = 0
                    track_order.append(section)
                note_counts[section] += 1

    return (
        song_meta,
        [(n, note_counts[n]) for n in track_order],
        initial_bpm,
        ts_num,
        ts_den,
    )


def _parse_song_and_sync(text: str) -> tuple[dict[str, str], list[tuple[int, int]]]:
    """Extract [Song] metadata and [SyncTrack] BPM entries."""
    song_meta:    dict[str, str]       = {}
    sync_entries: list[tuple[int,int]] = []
    section: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^\[(\w+)\]$', line)
        if m:
            section = m.group(1)
            continue
        if line in ('{', '}'):
            if line == '}':
                section = None
            continue
        if section == 'Song':
            m = re.match(r'^(\w+)\s*=\s*"?([^"]*)"?$', line)
            if m:
                song_meta[m.group(1)] = m.group(2).strip('"')
        elif section == 'SyncTrack':
            m = re.match(r'^(\d+)\s*=\s*B\s+(\d+)$', line)
            if m:
                sync_entries.append((int(m.group(1)), int(m.group(2))))

    return song_meta, sync_entries


def _parse_notes_for_section(
    text: str, section_name: str
) -> list[tuple[int, int, int]]:
    """Return (tick, fret, dur_ticks) for playable notes in *section_name*."""
    entries: list[tuple[int, int, int]] = []
    section: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^\[(\w+)\]$', line)
        if m:
            section = m.group(1)
            continue
        if line in ('{', '}'):
            if line == '}':
                section = None
            continue
        if section == section_name:
            m = re.match(r'^(\d+)\s*=\s*N\s+(\d+)\s+(\d+)$', line)
            if m:
                tick, fret, dur = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if fret in VALID_FRETS:
                    entries.append((tick, fret, dur))

    return entries


# ── Tempo map ─────────────────────────────────────────────────────────────────

def _build_tempomap(
    sync_entries: list[tuple[int, int]],
    resolution: int,
) -> list[tuple[int, float, float]]:
    """
    Returns list of (tick, cumulative_ms, ms_per_tick) sorted by tick.
    Default BPM = 120 if no sync entries exist.
    """
    entries = sorted(sync_entries) or [(0, 120_000)]
    result: list[tuple[int, float, float]] = []
    cum_ms   = 0.0
    prev_tick = 0
    prev_mpt  = 60_000.0 / 120.0 / resolution   # ms per tick at 120 BPM

    for tick, millibpm in entries:
        bpm = millibpm / 1000.0
        mpt = 60_000.0 / bpm / resolution
        cum_ms += (tick - prev_tick) * prev_mpt
        result.append((tick, cum_ms, mpt))
        prev_tick = tick
        prev_mpt  = mpt

    return result


def _tick_to_ms(tick: int, tempomap: list[tuple[int, float, float]]) -> float:
    ms        = tempomap[0][1] if tempomap else 0.0
    mpt       = tempomap[0][2] if tempomap else 60_000.0 / 120.0 / 192
    last_tick = tempomap[0][0] if tempomap else 0

    for t, t_ms, t_mpt in tempomap:
        if t > tick:
            break
        ms        = t_ms
        mpt       = t_mpt
        last_tick = t

    return ms + (tick - last_tick) * mpt


# ── Script generation ─────────────────────────────────────────────────────────

def _fmt_time(ms: float) -> str:
    """Format milliseconds as 'M:SS.mmm'."""
    ms_int = max(0, int(round(ms)))
    mmm    = ms_int % 1000
    total_s = ms_int // 1000
    m       = total_s // 60
    s       = total_s % 60
    return f'{m}:{s:02d}.{mmm:03d}'


def convert_tracks_per_track(
    chart_text: str,
    configs: list[TrackConfig],
) -> tuple[list[tuple[str, str]], Optional[str]]:
    """
    Convert a .chart file into separate per-track echoes script bodies.

    Returns (track_list, audio_filename_or_None) where track_list is a list
    of (track_name, script_body) for each enabled TrackConfig.
    """
    song_meta, sync_entries = _parse_song_and_sync(chart_text)

    resolution = int(song_meta.get('Resolution', '192'))
    audio_file = song_meta.get('MusicStream') or None
    tempomap   = _build_tempomap(sync_entries, resolution)

    track_scripts: list[tuple[str, str]] = []

    for cfg in configs:
        if not cfg.enabled:
            continue

        fret_map     = cfg.get_fret_map()
        note_entries = _parse_notes_for_section(chart_text, cfg.track_name)
        cmd_lines: list[tuple[float, str]] = []

        for tick, fret, dur_ticks in note_entries:
            leds = fret_map.get(fret, '').strip()
            if not leds:
                continue
            r, g, b  = cfg.get_fret_color(fret)
            start_ms = _tick_to_ms(tick, tempomap)
            end_ms   = _tick_to_ms(tick + dur_ticks, tempomap)
            dur_ms   = max(end_ms - start_ms, MIN_FLASH_MS)
            leds     = fret_map[fret]
            t        = _fmt_time(start_ms)

            if dur_ms < cfg.short_ms:
                pct = _fade_percent_for_black(max(r, g, b), dur_ms,
                                              cfg.fade_percent)
                cmd_lines.append((
                    start_ms,
                    f'{t}  FADE {r} {g} {b} {pct}  {leds}'
                ))
            else:
                cmd_lines.append((
                    start_ms,
                    f'{t}  SET {r} {g} {b}  {leds}'
                ))
                fade_ms = max(start_ms + dur_ms - cfg.fade_lead_ms, start_ms + 50)
                t_avail = max(start_ms + dur_ms - fade_ms, 1)
                pct = _fade_percent_for_black(max(r, g, b), t_avail,
                                              cfg.fade_percent)
                cmd_lines.append((
                    fade_ms,
                    f'{_fmt_time(fade_ms)}  FADE {r} {g} {b} {pct}  {leds}'
                ))

        cmd_lines.sort(key=lambda x: x[0])
        seen_at: dict[str, float] = {}
        final: list[str] = []
        for ms, line in cmd_lines:
            key  = line.split('  ', 1)[1]
            last = seen_at.get(key, -9999)
            if ms - last > 50:
                final.append(line)
                seen_at[key] = ms

        header_parts = ['# Imported from Clone Hero .chart', '']
        body = '\n'.join(header_parts + final)
        track_scripts.append((cfg.track_name, body))

    return track_scripts, audio_file
