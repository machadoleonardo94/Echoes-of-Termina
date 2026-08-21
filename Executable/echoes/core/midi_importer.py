"""
Clone Hero / Rock Band notes.mid → echoes script converter.

Parses MIDI type-1 binary files and provides the same public API as
chart_importer (get_midi_info / convert_midi_tracks), so the existing
ChartImportDialog and convert flow work unchanged.

MIDI format summary (from thenathannator.github.io/GuitarGame_ChartFormats)
───────────────────────────────────────────────────────────────────────────
  File type:    MIDI type-1  (multi-track)
  Track 0:      Tempo map — BPM (0xFF 0x51) + time-sig (0xFF 0x58) meta events
  Track 1+:     Instrument tracks, named via 0xFF 0x03 track-name event
                  "PART GUITAR"  "PART BASS"  "PART RHYTHM"  "PART KEYS"  …

5-fret Expert note mapping
──────────────────────────
  MIDI 96  → fret 0  (Green)
  MIDI 97  → fret 1  (Red)
  MIDI 98  → fret 2  (Yellow)
  MIDI 99  → fret 3  (Blue)
  MIDI 100 → fret 4  (Orange)

Time conversion (same as .chart)
─────────────────────────────────
  millibpm = round(60_000_000_000 / µs_per_beat)
  The existing _build_tempomap / _tick_to_ms from chart_importer are reused.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from core.chart_importer import (
    GH_FRET_COLORS, LED_PRESETS, MIN_FLASH_MS,
    TrackConfig,
    _build_tempomap, _tick_to_ms, _fmt_time,
)

# ── Expert 5-fret MIDI note range ─────────────────────────────────────────────

_EXPERT_MIN = 96   # Green  (fret 0)
_EXPERT_MAX = 100  # Orange (fret 4)


# ── Parsed MIDI file ──────────────────────────────────────────────────────────

@dataclass
class _MidiFile:
    ticks: int                                  # ticks per quarter note
    sync:  list[tuple[int, int]]                # (tick, millibpm)
    ts_num: int = 4                             # initial time-sig numerator
    ts_den: int = 4                             # initial time-sig denominator
    tracks: list[tuple[str, list]] = field(     # (name, [(tick, fret, dur)])
        default_factory=list)


# ── Variable-length quantity ──────────────────────────────────────────────────

def _vlq(data: bytes, pos: int) -> tuple[int, int]:
    """Read a MIDI VLQ integer. Returns (value, next_pos)."""
    v = 0
    while pos < len(data):
        b = data[pos]; pos += 1
        v = (v << 7) | (b & 0x7F)
        if not (b & 0x80):
            return v, pos
    return v, pos


# ── Core binary parser ────────────────────────────────────────────────────────

def _parse(data: bytes) -> _MidiFile:
    """
    Parse a MIDI type-0 or type-1 binary stream.

    Collects:
      • BPM tempo changes and time signatures from the first track (or
        any track with no instrument notes — the tempo-map track).
      • Expert 5-fret note data from every named instrument track.
    """
    if data[:4] != b'MThd':
        raise ValueError("Not a MIDI file — missing MThd marker")

    hdr_len = struct.unpack_from('>I', data, 4)[0]
    fmt, _ntracks, tpq = struct.unpack_from('>HHH', data, 8)
    if fmt not in (0, 1):
        raise ValueError(f"MIDI format {fmt} not supported (need type 0 or 1)")
    if tpq & 0x8000:
        raise ValueError("SMPTE time-code resolution is not supported")

    result = _MidiFile(ticks=tpq, sync=[], ts_num=4, ts_den=4)
    _ts_set  = False
    _bpm_set = False

    pos = 8 + hdr_len
    track_index = 0

    while pos + 8 <= len(data):
        tag      = data[pos:pos + 4]
        chk_len  = struct.unpack_from('>I', data, pos + 4)[0]
        pos     += 8
        end      = pos + chk_len

        if tag != b'MTrk' or end > len(data):
            pos = end
            continue

        # ── Parse one MTrk chunk ──────────────────────────────────────────────
        track_name  = ''
        note_on: dict[int, int] = {}                  # MIDI-note → start_tick
        notes:   list[tuple[int, int, int]] = []      # (tick, fret, dur_ticks)

        abs_tick = 0
        status   = 0   # running-status byte

        p = pos
        while p < end:
            delta, p = _vlq(data, p)
            if p >= end:
                break
            abs_tick += delta

            b = data[p]

            # ── Meta event (0xFF) ─────────────────────────────────────────────
            if b == 0xFF:
                p += 1
                if p >= end:
                    break
                mtype = data[p]; p += 1
                mlen, p = _vlq(data, p)
                mdata = data[p:p + mlen]; p += mlen
                status = 0   # meta resets running status

                if mtype == 0x03:   # track name
                    track_name = mdata.decode('latin-1', errors='replace').strip()

                elif mtype == 0x51 and mlen == 3:   # tempo
                    us = (mdata[0] << 16) | (mdata[1] << 8) | mdata[2]
                    if us > 0:
                        mbpm = round(60_000_000_000 / us)
                        result.sync.append((abs_tick, mbpm))
                        if not _bpm_set:
                            _bpm_set = True

                elif mtype == 0x58 and mlen == 4:   # time signature
                    if not _ts_set:
                        result.ts_num = mdata[0]
                        result.ts_den = 2 ** mdata[1]
                        _ts_set = True

                elif mtype == 0x2F:   # end of track
                    break
                continue

            # ── SysEx (0xF0 / 0xF7) ──────────────────────────────────────────
            if b in (0xF0, 0xF7):
                p += 1
                slen, p = _vlq(data, p)
                p += slen
                status = 0
                continue

            # ── Channel voice message ─────────────────────────────────────────
            if b & 0x80:
                status = b; p += 1

            msg = status & 0xF0

            if msg == 0x90:   # Note On
                if p + 1 >= end:
                    break
                note = data[p]; vel = data[p + 1]; p += 2
                if _EXPERT_MIN <= note <= _EXPERT_MAX:
                    if vel > 0:
                        note_on[note] = abs_tick
                    else:
                        # velocity-0 Note On = Note Off
                        start = note_on.pop(note, abs_tick)
                        notes.append((start, note - _EXPERT_MIN,
                                      abs_tick - start))

            elif msg == 0x80:   # Note Off
                if p + 1 >= end:
                    break
                note = data[p]; p += 2
                if _EXPERT_MIN <= note <= _EXPERT_MAX:
                    start = note_on.pop(note, abs_tick)
                    notes.append((start, note - _EXPERT_MIN,
                                  abs_tick - start))

            elif msg in (0xA0, 0xB0, 0xE0):   # 2-byte payload
                p += 2
            elif msg in (0xC0, 0xD0):          # 1-byte payload
                p += 1
            else:
                p += 1   # unknown / skip

        # Flush open note_on entries (note-off missing before end-of-track)
        for note, start in note_on.items():
            if _EXPERT_MIN <= note <= _EXPERT_MAX:
                notes.append((start, note - _EXPERT_MIN, 0))

        # Only keep tracks that have a name or instrument notes
        if track_name or notes:
            result.tracks.append((track_name, notes))

        track_index += 1
        pos = end

    return result


# ── Public API ────────────────────────────────────────────────────────────────

def get_midi_info(
    data: bytes,
) -> tuple[dict[str, str], list[tuple[str, int]], float, int, int]:
    """
    Quick scan of a notes.mid binary.

    Returns (song_meta, tracks, initial_bpm, ts_num, ts_den) with the
    same shape as chart_importer.get_chart_info().

    song_meta only contains Resolution (and derived _Tempo/_TimeSig fields).
    The caller should augment it from song.ini the same way _import_chart does.
    """
    mid = _parse(data)

    initial_bpm = 120.0
    if mid.sync:
        # sync[0] is the tick-0 entry (smallest tick, since BPM comes first)
        first_mbpm = sorted(mid.sync)[0][1]
        initial_bpm = first_mbpm / 1000.0

    song_meta = {'Resolution': str(mid.ticks)}

    # Instrument tracks = those with at least one Expert note
    tracks = [
        (name, len(notes))
        for name, notes in mid.tracks
        if notes
    ]

    return song_meta, tracks, initial_bpm, mid.ts_num, mid.ts_den


def convert_midi_tracks(
    data:    bytes,
    configs: list[TrackConfig],
) -> tuple[str, None]:
    """
    Convert a notes.mid binary to an echoes script using per-track config.

    Returns (script_text, None) — MIDI files carry no embedded audio path;
    the caller discovers audio via the reserved-filename search.
    """
    mid      = _parse(data)
    tempomap = _build_tempomap(mid.sync, mid.ticks)

    # Build a name → notes lookup from parsed tracks
    note_lookup: dict[str, list[tuple[int, int, int]]] = {}
    for name, notes in mid.tracks:
        note_lookup[name] = notes

    cmd_lines: list[tuple[float, str]] = []

    for cfg in configs:
        if not cfg.enabled:
            continue
        fret_map = cfg.get_fret_map()
        notes    = note_lookup.get(cfg.track_name, [])

        for tick, fret, dur_ticks in notes:
            leds = fret_map.get(fret, '').strip()
            if not leds:
                continue
            r, g, b  = cfg.get_fret_color(fret)
            start_ms = _tick_to_ms(tick, tempomap)
            end_ms   = _tick_to_ms(tick + dur_ticks, tempomap)
            dur_ms   = max(end_ms - start_ms, MIN_FLASH_MS)
            t        = _fmt_time(start_ms)

            if dur_ms < cfg.short_ms:
                cmd_lines.append((
                    start_ms,
                    f'{t}  FADE {r} {g} {b} {cfg.fade_percent}  {leds}'
                ))
            else:
                cmd_lines.append((
                    start_ms,
                    f'{t}  SET {r} {g} {b}  {leds}'
                ))
                fade_ms = max(start_ms + dur_ms - cfg.fade_lead_ms, start_ms + 50)
                cmd_lines.append((
                    fade_ms,
                    f'{_fmt_time(fade_ms)}  FADE {r} {g} {b} {cfg.fade_percent}  {leds}'
                ))

    # Sort + deduplicate (same logic as convert_tracks)
    cmd_lines.sort(key=lambda x: x[0])
    seen_at: dict[str, float] = {}
    final:   list[str]        = []
    for ms, line in cmd_lines:
        key  = line.split('  ', 1)[1]
        last = seen_at.get(key, -9999)
        if ms - last > 50:
            final.append(line)
            seen_at[key] = ms

    header = [
        '#',
        '# Imported from notes.mid',
        '',
    ]

    return '\n'.join(header + final), None


def convert_midi_tracks_per_track(
    data:    bytes,
    configs: list[TrackConfig],
) -> tuple[list[tuple[str, str]], None]:
    """
    Convert a notes.mid binary into separate per-track echoes script bodies.

    Returns (track_list, None) where track_list is a list of
    (track_name, script_body) for each enabled TrackConfig.
    """
    mid      = _parse(data)
    tempomap = _build_tempomap(mid.sync, mid.ticks)

    note_lookup: dict[str, list[tuple[int, int, int]]] = {}
    for name, notes in mid.tracks:
        note_lookup[name] = notes

    track_scripts: list[tuple[str, str]] = []

    for cfg in configs:
        if not cfg.enabled:
            continue

        fret_map = cfg.get_fret_map()
        notes    = note_lookup.get(cfg.track_name, [])
        cmd_lines: list[tuple[float, str]] = []

        for tick, fret, dur_ticks in notes:
            leds = fret_map.get(fret, '').strip()
            if not leds:
                continue
            r, g, b  = cfg.get_fret_color(fret)
            start_ms = _tick_to_ms(tick, tempomap)
            end_ms   = _tick_to_ms(tick + dur_ticks, tempomap)
            dur_ms   = max(end_ms - start_ms, MIN_FLASH_MS)
            t        = _fmt_time(start_ms)

            if dur_ms < cfg.short_ms:
                cmd_lines.append((
                    start_ms,
                    f'{t}  FADE {r} {g} {b} {cfg.fade_percent}  {leds}'
                ))
            else:
                cmd_lines.append((
                    start_ms,
                    f'{t}  SET {r} {g} {b}  {leds}'
                ))
                fade_ms = max(start_ms + dur_ms - cfg.fade_lead_ms, start_ms + 50)
                cmd_lines.append((
                    fade_ms,
                    f'{_fmt_time(fade_ms)}  FADE {r} {g} {b} {cfg.fade_percent}  {leds}'
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

        header_parts = ['# Imported from notes.mid', '']
        body = '\n'.join(header_parts + final)
        track_scripts.append((cfg.track_name, body))

    return track_scripts, None
