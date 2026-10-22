"""
Verify a Clone Hero / Rock Band chart's note data and how the Echoes importer
consumes it.

Scans a .mid or .chart file and reports, per track:

  • every note number (MIDI) / lane (chart) present, with a format-spec name
    and count -- the source of truth for "what is actually in this chart";
  • the detected drum chart type (standard 4-lane / 4-lane Pro / 5-lane) using
    the heuristics from GuitarGame_ChartFormats (tom markers → Pro, 5-lane
    green note 101 → 5-lane, sustains → 5-lane);
  • which notes the 5-fret import mapping consumes (drum lanes 97–101, or
    96–100 for other instruments) and which markers it ignores;
  • a round-trip of the generated script through ScriptEngine (command count,
    parse errors, per-fret note alignment);
  • a 6th-note pedal-cue import simulation (cue LEDs 27,28), checking that
    every pedal hit becomes a cue command at the same millisecond.

Run from the project folder:

    python _verify_drum_chart.py                     # Song of Storms test song
    python _verify_drum_chart.py path\\notes.mid
    python _verify_drum_chart.py path\\notes.chart
    python _verify_drum_chart.py --track "PART DRUMS" --all
    python _verify_drum_chart.py path\\notes.mid --quiet

Exit code 0 = the file parsed, the importer produced a valid script and every
playable note mapped to a command.  1 = data/import problem.  2 = usage error.
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

from core.chart_importer import (          # noqa: E402
    CHART_DRUM_CUE_NOTE, LED_PRESETS, TrackConfig,
    _build_tempomap, _parse_cue_notes_for_section, _parse_song_and_sync,
    _tick_to_ms, convert_tracks_per_track, get_chart_info,
)
from core.midi_importer import (           # noqa: E402
    MIDI_PEDAL_NOTE, _parse, _vlq,
    convert_midi_tracks_per_track, get_midi_info,
)
from core.script_engine import ScriptEngine  # noqa: E402

# ── Format knowledge (GuitarGame_ChartFormats) ───────────────────────────────
#
# .mid drums, Expert difficulty.  Drums: 96 is the kick pedal (imported as the
# 6th-note cue) and 97–101 are the five playable lanes → frets 0–4.  For other
# instruments 96–100 are the five frets.

_MID_DRUM_NOTES: dict[int, str] = {
    95:  'Expert+ kick (2x kick)',
    96:  'kick pedal (6th-note cue)',
    97:  'red   (lane 1)',
    98:  'yellow (lane 2, hi-hat / tom)',
    99:  'blue  (lane 3, ride / tom)',
    100: 'green (lane 4, crash / tom)',
    101: '5-lane green (lane 5)',
    102: 'solo marker',
    109: 'flam marker',
    110: 'yellow-tom marker',
    111: 'blue-tom marker',
    112: 'green-tom marker',
    116: 'star-power marker',
    120: 'fill marker 1',
    121: 'fill marker 2',
    122: 'fill marker 3',
    123: 'fill marker 4',
    124: 'fill marker 5',
}

_FRET_LABELS = ['red', 'yellow', 'blue', 'green', '5-lane']
_GUITAR_FRET_LABELS = ['green', 'red', 'yellow', 'blue', 'orange']

# Note numbers that are real note lanes (as opposed to markers / phrases).
_EXTRA_LANES = {101: '5-lane green (5th lane)'}


# ── Raw scanners ─────────────────────────────────────────────────────────────

def scan_midi(data: bytes) -> dict[str, dict[int, list[tuple[int, int, int]]]]:
    """
    Scan every MTrk chunk and return
    ``{track_name: {note: [(tick, dur_ticks, channel), ...]}}``.

    Unlike core.midi_importer._parse this keeps *all* note numbers, so the
    verification can see notes the importer's 96–100 range hides.
    """
    if data[:4] != b'MThd':
        raise ValueError('not a MIDI file (missing MThd)')
    hdr_len = struct.unpack_from('>I', data, 4)[0]
    result: dict[str, dict[int, list[tuple[int, int, int]]]] = {}

    pos = 8 + hdr_len
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        chk_len = struct.unpack_from('>I', data, pos + 4)[0]
        end = pos + 8 + chk_len
        if tag != b'MTrk' or end > len(data):
            pos = end
            continue

        name = ''
        open_ons: dict[int, list[int]] = {}
        events: dict[int, list[tuple[int, int, int]]] = {}
        tick = 0
        status = 0

        p = pos + 8
        while p < end:
            delta, p = _vlq(data, p)
            if p >= end:
                break
            tick += delta
            b = data[p]

            if b == 0xFF:                      # meta
                p += 1
                if p >= end:
                    break
                mtype = data[p]; p += 1
                mlen, p = _vlq(data, p)
                mdata = data[p:p + mlen]; p += mlen
                status = 0
                if mtype == 0x03:
                    name = mdata.decode('latin-1', errors='replace').strip()
                elif mtype == 0x2F:
                    break
                continue

            if b in (0xF0, 0xF7):              # sysex
                p += 1
                slen, p = _vlq(data, p)
                p += slen
                status = 0
                continue

            if b & 0x80:                        # channel status byte
                status = b; p += 1
            ch = status & 0x0F
            msg = status & 0xF0

            if msg == 0x90:                    # note on
                if p + 1 >= end:
                    break
                note = data[p]; vel = data[p + 1]; p += 2
                if vel > 0:
                    open_ons.setdefault(note, []).append(tick)
                else:                          # velocity 0 = note off
                    starts = open_ons.get(note)
                    if starts:
                        start = starts.pop()
                        events.setdefault(note, []).append(
                            (start, tick - start, ch))
            elif msg == 0x80:                  # note off
                if p + 1 >= end:
                    break
                note = data[p]; p += 2
                starts = open_ons.get(note)
                if starts:
                    start = starts.pop()
                    events.setdefault(note, []).append(
                        (start, tick - start, ch))
            elif msg in (0xA0, 0xB0, 0xE0):    # aftertouch / CC / pitchbend
                p += 2
            elif msg in (0xC0, 0xD0):          # program / channel pressure
                p += 1
            else:
                p += 1

        for note, starts in open_ons.items():  # unterminated note-ons
            for start in starts:
                events.setdefault(note, []).append((start, 0, 0))

        result[name] = events
        pos = end

    return result


def scan_chart(text: str) -> dict[str, dict[int, list[tuple[int, int, int]]]]:
    """{section: {lane: [(tick, dur, 0), ...]}} for every ``N <lane> <dur>``."""
    result: dict[str, dict[int, list[tuple[int, int, int]]]] = {}
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
        if section is None:
            continue
        m = re.match(r'^(\d+)\s*=\s*N\s+(\d+)\s+(\d+)$', line)
        if m:
            tick, lane, dur = int(m.group(1)), int(m.group(2)), int(m.group(3))
            result.setdefault(section, {}).setdefault(lane, []).append(
                (tick, dur, 0))
    return result


# ── Classification helpers ───────────────────────────────────────────────────

def classify_midi_note(note: int, is_drum: bool) -> str:
    """Human-readable name for a MIDI note number."""
    if note in _MID_DRUM_NOTES:
        return _MID_DRUM_NOTES[note]
    if note < 96:
        return f'lower-difficulty / other ({note})'
    if note > 124:
        return f'unknown ({note})'
    return f'unknown ({note})'


def _parse_leds(spec: str) -> set[int]:
    """'17,26' / '7-8' / '*' → set of 1-based LED indices."""
    if spec.strip() == '*':
        return set(range(1, 29))
    leds: set[int] = set()
    for part in spec.split(','):
        part = part.strip()
        rng = re.match(r'^(\d+)-(\d+)$', part)
        sng = re.match(r'^(\d+)$', part)
        if rng:
            leds.update(range(int(rng.group(1)), int(rng.group(2)) + 1))
        elif sng:
            leds.add(int(sng.group(1)))
    return leds


def _sustained(
    events: dict[int, list[tuple[int, int, int]]],
    note: int,
    sustain_ticks: int,
) -> bool:
    """True when *note* has at least one hold longer than a short tap.

    Clone Hero exports every hit with a small note length (one 1/16 beat at
    480 tpq = 30 ticks), so a duration > tpq/8 is a real drum sustain.
    """
    return any(dur > sustain_ticks for _t, dur, _c in events.get(note, []))


# ── Import simulation ────────────────────────────────────────────────────────

def simulate_import(
    path: str,
    track_name: str,
    fret_leds: dict[int, str],
    cue_leds: str = '',
) -> dict:
    """
    Run the real Echoes importer for *track_name* and return a report dict:
    ``body``, ``commands``, ``errors``, ``per_fret``, ``per_cue``,
    ``cue_times`` (times of cue commands, for alignment checks).
    """
    cfg = TrackConfig(track_name=track_name, fret_leds=dict(fret_leds),
                      cue_leds=cue_leds, cue_color=(0, 255, 0))
    if path.lower().endswith('.chart'):
        with open(path, encoding='utf-8-sig', errors='replace') as fh:
            text = fh.read()
        bodies, _audio = convert_tracks_per_track(text, [cfg])
    else:
        with open(path, 'rb') as fh:
            data = fh.read()
        bodies, _audio = convert_midi_tracks_per_track(data, [cfg])

    if not bodies:
        return {'body': '', 'commands': [], 'errors': [], 'per_fret': {},
                'per_cue': 0, 'cue_times': set()}

    body = bodies[0][1]
    engine = ScriptEngine()
    engine.parse(body)

    cue_set = _parse_leds(cue_leds) if cue_leds.strip() else set()

    # Map each command to the fret lane / cue whose LED set it targets.
    per_fret: dict[int, int] = {f: 0 for f in range(5)}
    per_cue = 0
    cue_times: set[int] = set()
    for cmd in engine.commands:
        cmd_leds = set(cmd.leds)
        if cue_set and cmd_leds == cue_set:
            per_cue += 1
            cue_times.add(cmd.time_ms)
            continue
        for fret, spec in fret_leds.items():
            if cmd_leds == _parse_leds(spec):
                per_fret[fret] = per_fret.get(fret, 0) + 1
                break

    return {
        'body':      body,
        'commands':  engine.commands,
        'errors':    engine.errors,
        'per_fret':  per_fret,
        'per_cue':   per_cue,
        'cue_times': cue_times,
    }


# ── Reporting ───────────────────────────────────────────────────────────────

def report_midi(
    path: str,
    default_track: str,
    fret_leds: dict[int, str],
    out,
) -> int:
    with open(path, 'rb') as fh:
        data = fh.read()
    tracks = scan_midi(data)
    meta, track_list, bpm, ts_num, ts_den = get_midi_info(data)
    tpq = int(meta.get('Resolution', '480') or 480)
    sustain_ticks = max(1, tpq // 8)     # anything longer than 1/8 beat is a hold

    print(f'file      : {path}', file=out)
    print(f'format    : MIDI  Resolution {tpq} tpq  '
          f'initial BPM {bpm:.3f}  time-signature {ts_num}/{ts_den}',
          file=out)

    # song.ini drum flags, if the chart sits next to one
    ini = os.path.join(os.path.dirname(os.path.abspath(path)), 'song.ini')
    pro_drums = five_lane = None
    if os.path.isfile(ini):
        flags = {}
        with open(ini, encoding='utf-8', errors='replace') as fh:
            for raw in fh:
                m = re.match(r'^(\w+)\s*=\s*(.+)$', raw.strip())
                if m:
                    flags[m.group(1).lower()] = m.group(2).strip().strip('"')
        pro_drums = (flags.get('pro_drums', '').lower() in ('true', '1'))
        five_lane = (flags.get('five_lane_drums', '').lower() in ('true', '1'))
        print(f'song.ini  : pro_drums={pro_drums}  '
              f'five_lane_drums={five_lane}  '
              f'multiplier_note={flags.get("multiplier_note", "-")}', file=out)

    # which tracks exist (only tracks that carry events)
    print('tracks    :', ', '.join(
        f'{n or "(unnamed)"} ({sum(len(v) for v in e.values())} notes)'
        f'{"" if n != default_track else "  <-"}'
        for n, e in tracks.items() if e), file=out)

    if default_track not in tracks:
        print(f'ERROR: track "{default_track}" not found in this MIDI.', file=out)
        return 1
    events = tracks[default_track]
    is_drum_track = 'DRUM' in default_track.upper()
    fret_labels = _FRET_LABELS if is_drum_track else _GUITAR_FRET_LABELS
    print(file=out)
    print(f'== {default_track} ==', file=out)

    # expert notes: drums 97–101 are the five lanes (96 = pedal cue);
    # other instruments use 96–100
    if is_drum_track:
        lane_notes = set(range(97, 102))          # red … 5-lane green
        cue_note   = MIDI_PEDAL_NOTE               # 96 kick pedal
        fret_counts = {f: len(events.get(97 + f, [])) for f in range(5)}
    else:
        lane_notes = set(range(96, 101))
        cue_note   = None
        fret_counts = {f: len(events.get(96 + f, [])) for f in range(5)}
    total_fret = sum(fret_counts.values())
    extras = {n: ev for n, ev in events.items()
              if n not in lane_notes and n != cue_note}
    expert_extras = {n: ev for n, ev in extras.items() if n >= 95}
    low_extras = {n: ev for n, ev in extras.items() if n < 95}

    if total_fret:
        print(f'Expert notes : {total_fret}', file=out)
        base_note = 97 if is_drum_track else 96
        for f in range(5):
            dur_kind = ('holds' if _sustained(events, base_note + f, sustain_ticks)
                        else 'taps')
            print(f'  note {base_note + f:3d}  {fret_labels[f]:8s}  '
                  f'{fret_counts[f]:4d}  ({dur_kind})', file=out)
    else:
        print('Expert notes : none', file=out)

    if cue_note is not None:
        cue_hits = len(events.get(cue_note, []))
        if cue_hits:
            print(f'Pedal cue    : {cue_hits}  '
                  f'(note {cue_note} — imported onto the Cue LEDs)', file=out)
        else:
            print(f'Pedal cue    : none  (note {cue_note} not present)', file=out)

    if expert_extras:
        print('Extra note events (markers, not imported):', file=out)
        for note in sorted(expert_extras):
            count = len(expert_extras[note])
            label = classify_midi_note(note, is_drum=is_drum_track)
            kind = ('phrase (sustained)'
                    if _sustained(events, note, sustain_ticks) else 'hit')
            print(f'  note {note:3d}  {label:34s} {count:4d}  [{kind}]',
                  file=out)
    else:
        print('Extra note events : none', file=out)

    if low_extras:
        n_lanes = len(low_extras)
        n_events = sum(len(ev) for ev in low_extras.values())
        print(f'lower-difficulty lanes (< 96): {n_events} events across '
              f'{n_lanes} lanes (Easy/Medium/Hard -- never imported)', file=out)

    # ── Drum chart type ────────────────────────────────────────────────────
    if is_drum_track and ((pro_drums is not None) or any(
            n in events for n in (110, 111, 112)) or 101 in events):
        has_101 = 101 in events
        has_toms = any(n in events for n in (110, 111, 112))
        has_sustains = any(_sustained(events, n, sustain_ticks)
                           for n in (96, 97, 98, 99))
        if five_lane:
            drum_type = '5-lane (song.ini five_lane_drums=true)'
        elif pro_drums:
            drum_type = '4-lane Pro (song.ini pro_drums=true)'
        elif has_101:
            drum_type = '5-lane (green 5th-lane note 101 present)'
        elif has_toms:
            drum_type = '4-lane Pro (tom markers present)'
        elif has_sustains:
            drum_type = '5-lane (sustained notes present)'
        else:
            drum_type = 'standard 4-lane'
        print(f'drum type : {drum_type}', file=out)

        # cymbal/tom split per the Pro Drums convention
        if has_toms:
            print('cymbal/tom split (4-lane Pro convention, unmarked = cymbal):',
                  file=out)
            for note, marker, label in (
                    (98, 110, 'yellow'), (99, 111, 'blue'), (100, 112, 'green')):
                total = fret_counts.get(note - 97, 0)
                toms = len(events.get(marker, []))
                print(f'  {label:6s}: {total - toms:4d} cymbal, {toms:4d} tom',
                      file=out)
            # integrity: every tom marker must sit on a note of its lane
            for note, marker, label in (
                    (98, 110, 'yellow'), (99, 111, 'blue'), (100, 112, 'green')):
                note_ticks = {t for t, _d, _c in events.get(note, [])}
                marker_ticks = {t for t, _d, _c in events.get(marker, [])}
                if marker_ticks:
                    aligned = marker_ticks <= note_ticks
                    print(f'  marker {marker} on {label} notes: '
                          f'{"aligned" if aligned else "MISALIGNED"}', file=out)
        if has_101:
            print('  note 101 present (5-lane chart: lane 5 = fret 4)',
                  file=out)
        elif not has_toms:
            print('  no tom markers: every yellow/blue/green note is a cymbal',
                  file=out)

    # ── Import simulation ─────────────────────────────────────────────────
    print(file=out)
    print('== Import simulation (default Spikes preset) ==', file=out)
    sim = simulate_import(path, default_track, fret_leds)
    if not sim['commands'] and not sim['body'].strip():
        print('  ERROR: importer produced no output for this track.', file=out)
        return 1

    n_cmd = len(sim['commands'])
    n_err = len(sim['errors'])
    print(f'  script body       : {len(sim["body"].splitlines())} lines, '
          f'{n_cmd} commands', file=out)
    print(f'  parse errors      : {n_err}', file=out)
    for f in range(5):
        print(f'  fret {f} ({fret_labels[f]}): {sim["per_fret"].get(f, 0):4d} '
              f'commands  (from {fret_counts[f]:4d} notes)', file=out)

    # Every fret that has notes must produce at least one command.
    missing = [f for f in range(5)
               if fret_counts[f] > 0 and sim['per_fret'].get(f, 0) == 0]
    dropped = sum(len(ev) for ev in expert_extras.values())
    if dropped:
        print(f'  extra note events dropped: {dropped}  '
              f'(markers / phrases are not imported)', file=out)

    # ── 6th-note (pedal) cue import simulation ────────────────────────────
    cue_ok   = True
    cue_fail = ''
    mid = _parse(data) if is_drum_track else None
    cue_ticks = [t for t, _d, _c in events.get(MIDI_PEDAL_NOTE, [])] \
        if is_drum_track else []
    if cue_ticks:
        tempomap = _build_tempomap(mid.sync, mid.ticks)
        expected_times = {round(_tick_to_ms(tick, tempomap))
                          for tick in cue_ticks}
        print(file=out)
        print('== Pedal cue (6th note) import simulation (cue LEDs 27,28) ==',
              file=out)
        print(f'  pedal events      : {len(cue_ticks)}', file=out)
        sim2 = simulate_import(path, default_track, fret_leds, '27,28')
        print(f'  cue commands      : {sim2["per_cue"]}  '
              f'(parse errors: {len(sim2["errors"])})', file=out)
        unmatched = expected_times - sim2['cue_times']
        if unmatched or sim2['errors']:
            cue_ok   = False
            cue_fail = (f'cue import misaligned ({len(unmatched)} cue times '
                        f'missing, {len(sim2["errors"])} parse errors)')
            print(f'  timing aligned    : NO -- {len(unmatched)} cue times '
                  f'missing', file=out)
        else:
            print('  timing aligned    : yes', file=out)

    ok = n_err == 0 and not missing and cue_ok
    print(file=out)
    if ok:
        print('RESULT    : OK -- all playable notes mapped, script parses '
              + ('cleanly, pedal cue aligned.'
                 if cue_ticks else 'cleanly.'), file=out)
    else:
        if n_err:
            print(f'RESULT    : FAIL -- {n_err} script parse error(s).', file=out)
            for e in sim['errors'][:5]:
                print(f'    line {e.line_number}: {e.message}', file=out)
        if missing:
            print(f'RESULT    : FAIL -- frets with notes but no commands: '
                  f'{missing}', file=out)
        if not cue_ok:
            print(f'RESULT    : FAIL -- {cue_fail}', file=out)
    return 0 if ok else 1


def report_chart(
    path: str,
    default_track: str,
    fret_leds: dict[int, str],
    out,
) -> int:
    with open(path, encoding='utf-8-sig', errors='replace') as fh:
        text = fh.read()
    sections = scan_chart(text)
    meta, track_list, bpm, ts_num, ts_den = get_chart_info(text)

    print(f'file      : {path}', file=out)
    print(f'format    : .chart  BPM {bpm:.3f}  time-signature {ts_num}/{ts_den}',
          file=out)
    print('tracks    :', ', '.join(f'{n} ({sum(len(v) for v in t.values())} notes)'
                                   for n, t in sections.items() if t), file=out)

    if default_track not in sections:
        print(f'ERROR: section "{default_track}" not found.', file=out)
        return 1
    events = sections[default_track]
    is_drum_section = 'drums' in default_track.lower()
    print(file=out)
    print(f'== {default_track} ==', file=out)

    if is_drum_section:
        # pedal = note 0 (6th-note cue); lanes = notes 1–5
        lane_notes = set(range(1, 6))
        fret_counts = {f: len(events.get(1 + f, [])) for f in range(5)}
        cue_notes = [0]
    else:
        lane_notes = set(range(5))
        fret_counts = {f: len(events.get(f, [])) for f in range(5)}
        cue_notes = []
    extra = {n: ev for n, ev in events.items()
             if n not in lane_notes and n not in cue_notes}
    total = sum(fret_counts.values())
    print(f'5-fret notes : {total}', file=out)
    for f in range(5):
        print(f'  lane {f}  {fret_counts[f]:4d}', file=out)
    if cue_notes and 0 in events:
        print(f'Pedal cue    : {len(events[0])}  (note 0)', file=out)
    if extra:
        print('extra lanes/notes (markers, not imported):', file=out)
        for note in sorted(extra):
            print(f'  note {note}: {len(extra[note])}', file=out)

    print(file=out)
    print('== Import simulation (default Spikes preset) ==', file=out)
    sim = simulate_import(path, default_track, fret_leds)
    n_cmd = len(sim['commands'])
    n_err = len(sim['errors'])
    print(f'  script body       : {len(sim["body"].splitlines())} lines, '
          f'{n_cmd} commands', file=out)
    print(f'  parse errors      : {n_err}', file=out)
    missing = [f for f in range(5)
               if fret_counts[f] > 0 and sim['per_fret'].get(f, 0) == 0]

    # ── 6th-note cue import simulation ────────────────────────────────────
    cue_ok   = True
    cue_fail = ''
    song_meta2, sync_entries = _parse_song_and_sync(text)
    resolution = int(song_meta2.get('Resolution', '192') or 192)
    tempomap = _build_tempomap(sync_entries, resolution)
    cue_events = _parse_cue_notes_for_section(text, default_track)
    if cue_events:
        expected_times = {round(_tick_to_ms(tick, tempomap))
                          for tick, _note, _dur in cue_events}
        print(file=out)
        print('== Cue (6th note) import simulation (cue LEDs 27,28) ==',
              file=out)
        print(f'  cue events        : {len(cue_events)}', file=out)
        sim2 = simulate_import(path, default_track, fret_leds, '27,28')
        print(f'  cue commands      : {sim2["per_cue"]}  '
              f'(parse errors: {len(sim2["errors"])})', file=out)
        unmatched = expected_times - sim2['cue_times']
        if unmatched or sim2['errors']:
            cue_ok   = False
            cue_fail = (f'cue import misaligned ({len(unmatched)} cue times '
                        f'missing, {len(sim2["errors"])} parse errors)')
            print(f'  timing aligned    : NO -- {len(unmatched)} cue times '
                  f'missing', file=out)
        else:
            print('  timing aligned    : yes', file=out)

    ok = n_err == 0 and not missing and cue_ok
    print(file=out)
    print('RESULT    : ' + ('OK' if ok else 'FAIL'), file=out)
    if not ok and cue_fail:
        print(f'            {cue_fail}', file=out)
    return 0 if ok else 1


# ── Entry point ─────────────────────────────────────────────────────────────

DEFAULT_SONG = os.path.join(
    _ROOT, 'projects', 'Tsukasa Masuko - Song of Storms (Ghostbyob)', 'notes.mid')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description='Verify chart note data and the Echoes 5-fret import.')
    ap.add_argument('path', nargs='?', default=DEFAULT_SONG,
                    help='.mid / .chart file (default: Song of Storms test song)')
    ap.add_argument('--track', default='PART DRUMS',
                    help='track/section to analyse (default: PART DRUMS)')
    ap.add_argument('--all', action='store_true',
                    help='report every track, not just --track')
    ap.add_argument('--quiet', action='store_true',
                    help='only print the RESULT line')
    args = ap.parse_args(argv)

    path = args.path
    if not os.path.isfile(path):
        print(f'ERROR: file not found: {path}')
        return 2

    fret_leds = dict(LED_PRESETS['Spikes'])
    is_mid = not path.lower().endswith('.chart')

    stream = None
    if args.quiet:
        import io
        stream = io.StringIO()
    out = stream or sys.stdout

    if args.all:
        # Full track-by-track inventory first, then the selected track.
        if is_mid:
            with open(path, 'rb') as fh:
                data = fh.read()
            tracks = scan_midi(data)
        else:
            with open(path, encoding='utf-8-sig', errors='replace') as fh:
                tracks = scan_chart(fh.read())
        print(f'== track inventory: {path} ==', file=out)
        for name, ev in tracks.items():
            if not ev:
                continue
            total = sum(len(v) for v in ev.values())
            kinds = ', '.join(
                f'{n}:{len(v)}' for n, v in sorted(ev.items()))
            print(f'  {name or "(unnamed)":22s} {total:5d} notes  [{kinds}]',
                  file=out)
        print(file=out)

    if is_mid:
        rc = report_midi(path, args.track, fret_leds, out)
    else:
        rc = report_chart(path, args.track, fret_leds, out)

    if args.quiet:
        text = stream.getvalue()
        result = [ln for ln in text.splitlines() if ln.startswith('RESULT')]
        print(result[0] if result else 'RESULT    : FAIL (no result line)')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

