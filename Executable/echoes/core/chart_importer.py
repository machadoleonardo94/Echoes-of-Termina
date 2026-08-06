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
  Short note  (clamped to ≥ 100 ms)  →  FADE 255 255 255 80 <leds>
  Long  note  (≥ SHORT_MS threshold) →  SET  255 255 255 <leds>
                                         FADE 255 255 255 80 <leds>   (FADE_LEAD_MS before end)
"""
from __future__ import annotations

import re
from pathlib import Path
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
FADE_PERCENT   = 80     # decay % per second for FADE command
FADE_LEAD_MS   = 400    # how early before a long note's end to issue FADE


# ── .chart parser ─────────────────────────────────────────────────────────────

def _parse_chart(text: str) -> tuple[dict, list, list]:
    """
    Returns (song_meta, sync_entries, note_entries).

    sync_entries : list of (tick: int, millibpm: int)
    note_entries : list of (tick: int, fret: int, dur_ticks: int)
    """
    song_meta: dict[str, str] = {}
    sync_entries: list[tuple[int, int]] = []
    note_entries: list[tuple[int, int, int]] = []

    section   = None
    brace_lvl = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue

        # Section header
        m = re.match(r'^\[(\w+)\]$', line)
        if m:
            section = m.group(1)
            continue

        if line == '{':
            brace_lvl += 1
            continue
        if line == '}':
            brace_lvl -= 1
            section = None
            continue

        if section == 'Song':
            m = re.match(r'^(\w+)\s*=\s*"?([^"]*)"?$', line)
            if m:
                song_meta[m.group(1)] = m.group(2).strip('"')

        elif section == 'SyncTrack':
            # tick = B millibpm
            m = re.match(r'^(\d+)\s*=\s*B\s+(\d+)$', line)
            if m:
                sync_entries.append((int(m.group(1)), int(m.group(2))))

        elif section == 'ExpertSingle':
            # tick = N fret duration
            m = re.match(r'^(\d+)\s*=\s*N\s+(\d+)\s+(\d+)$', line)
            if m:
                tick, fret, dur = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if fret in FRET_TO_LEDS:          # ignore modifiers (5,6,7)
                    note_entries.append((tick, fret, dur))

    return song_meta, sync_entries, note_entries


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


def convert(chart_text: str) -> tuple[str, Optional[str]]:
    """
    Convert .chart text to an echoes script string.

    Returns (script_text, audio_filename_or_None).
    audio_filename is the bare MusicStream value from [Song] if present.
    """
    song_meta, sync_entries, note_entries = _parse_chart(chart_text)

    resolution = int(song_meta.get('Resolution', '192'))
    audio_file = song_meta.get('MusicStream') or None

    tempomap = _build_tempomap(sync_entries, resolution)

    # Convert ticks → ms and compute real durations
    timed: list[tuple[float, int, float]] = []   # (start_ms, fret, dur_ms)
    for tick, fret, dur_ticks in note_entries:
        start_ms = _tick_to_ms(tick, tempomap)
        end_ms   = _tick_to_ms(tick + dur_ticks, tempomap)
        dur_ms   = max(end_ms - start_ms, MIN_FLASH_MS)
        timed.append((start_ms, fret, dur_ms))

    timed.sort(key=lambda x: x[0])

    # Generate echoes commands
    header_parts = []
    if song_meta.get('Name'):
        header_parts.append(f'# {song_meta["Name"]}')
    if song_meta.get('Artist'):
        header_parts.append(f'# {song_meta["Artist"]}')
    header_parts.append('#')
    header_parts.append('# Imported from Clone Hero .chart')
    header_parts.append('# Fret 0=5,6  1=4,7  2=3,8  3=2,9  4=1,10  (V1 spike mapping)')
    header_parts.append('')

    cmd_lines: list[tuple[float, str]] = []  # (sort_key_ms, line)

    for start_ms, fret, dur_ms in timed:
        leds = FRET_TO_LEDS[fret]
        t    = _fmt_time(start_ms)

        if dur_ms < SHORT_MS:
            # Flash note: single FADE
            cmd_lines.append((
                start_ms,
                f'{t}  FADE 255 255 255 {FADE_PERCENT}  {leds}'
            ))
        else:
            # Sustain: SET at start, FADE near end
            cmd_lines.append((
                start_ms,
                f'{t}  SET 255 255 255  {leds}'
            ))
            fade_ms = max(start_ms + dur_ms - FADE_LEAD_MS, start_ms + 50)
            cmd_lines.append((
                fade_ms,
                f'{_fmt_time(fade_ms)}  FADE 255 255 255 {FADE_PERCENT}  {leds}'
            ))

    # Sort all commands by time, then deduplicate adjacent identical lines
    cmd_lines.sort(key=lambda x: x[0])
    seen_at: dict[str, float] = {}
    final: list[str] = []
    for ms, line in cmd_lines:
        key = line.split('  ', 1)[1]          # command body without timestamp
        last = seen_at.get(key, -9999)
        if ms - last > 50:                     # suppress if same command < 50 ms ago
            final.append(line)
            seen_at[key] = ms

    script = '\n'.join(header_parts + final)
    return script, audio_file
