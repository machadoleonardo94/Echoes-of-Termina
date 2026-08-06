"""
DXF zone loader for echoes_outline.dxf — 28-zone Majora's Mask LED layout.

Physical strip sequence (both hardware versions visit zones in this order):
  eye_L  eye_R  bt-R-spk  far-R-spk  R-spk  uR-spk  uR-spk  top-spk
  uL-spk  L-spk  far-L-spk  bt-L-spk  [16 small circles in column order]

Hardware versions differ only in where the strip starts:
  V1  (current) — central circles 1-16, spikes 17-26, eyes 27-28
  V2  (new)     — eyes at physical positions  1-2;  strip starts at eye_L
"""
from __future__ import annotations

from core.dxf_loader import DxfZone, _read_pairs, _parse_entities  # noqa: F401

# ── Physical strip sequence ───────────────────────────────────────────────────
# Each entry: (dxf_handle, description)
# Order matches the physical LED strip as it routes around the mask.
_STRIP_ORDER: list[tuple[str, str]] = [
    # eyes first (positions 1-2 in V2, 27-28 in V1)
    ('100', 'left eye'),
    ('127', 'right eye'),
    # spikes, starting bottom-right and going counter-clockwise
    ('124', 'bottom-right spike'),
    ('123', 'far-right spike'),
    ('122', 'right spike'),
    ('121', 'upper-right spike B'),
    ('120', 'upper-right spike A'),
    ('105', 'top spike'),
    ('104', 'upper-left spike'),
    ('103', 'left spike'),
    ('102', 'far-left spike'),
    ('101', 'bottom-left spike'),
    # 16 small circles
    ('106', 'lower-left  col top'),
    ('113', 'lower-right col top'),
    ('114', 'lower-right col mid'),
    ('107', 'lower-left  col mid'),
    ('108', 'lower-left  col bot'),
    ('115', 'lower-right col bot'),
    ('125', 'mid-left'),
    ('126', 'mid-right'),
    ('116', 'upper-right col bot'),
    ('109', 'upper-left  col bot'),
    ('110', 'upper-left  col mid-lo'),
    ('117', 'upper-right col mid-lo'),
    ('118', 'upper-right col mid-hi'),
    ('111', 'upper-left  col mid-hi'),
    ('112', 'upper-left  col top'),
    ('119', 'upper-right col top'),
]

# V2 (eyes first): zone id = position in strip (1-based)
_HANDLE_TO_ZONE_V2: dict[str, int] = {
    h: i + 1 for i, (h, _) in enumerate(_STRIP_ORDER)
}

# V1: central circles first (1-16), then left/top spikes (17-21),
#     then right/bottom spikes (22-26), then eyes (27-28)
_V1_STRIP = (_STRIP_ORDER[12:]          # 16 circles  → positions  1-16
           + _STRIP_ORDER[7:12]         # 5 left spikes → positions 17-21
           + _STRIP_ORDER[2:7]          # 5 right spikes → positions 22-26
           + _STRIP_ORDER[:2])          # eyes          → positions 27-28
_HANDLE_TO_ZONE_V1: dict[str, int] = {
    h: i + 1 for i, (h, _) in enumerate(_V1_STRIP)
}


def load_zones(dxf_path: str, hw_v2: bool = False) -> list[DxfZone]:
    """
    Parse *dxf_path* (echoes_outline.dxf) and return 28 DxfZone objects
    ordered 1..28.  *hw_v2=True* uses the V2 mapping (eyes at 1-2);
    default (False) uses V1 (eyes at 27-28).
    Returns [] if the file cannot be read or parsed.
    """
    mapping = _HANDLE_TO_ZONE_V2 if hw_v2 else _HANDLE_TO_ZONE_V1

    try:
        pairs = _read_pairs(dxf_path)
        ents  = _parse_entities(pairs)
    except OSError:
        return []

    zones: list[DxfZone] = []

    for e in ents:
        zone_id = mapping.get(e['handle'])
        if zone_id is None:
            continue

        xs, ys = e['xs'], e['ys']
        if not xs:
            continue

        if e['type'] == 'CIRCLE':
            cx, cy = xs[0], ys[0]
            r = e['r'] if e['r'] is not None else 5.0
            from core.dxf_loader import _circle_path
            zones.append(DxfZone(
                zone_id=zone_id,
                path=_circle_path(cx, cy, r),
                cx=cx, cy=cy,
                xmin=cx - r, xmax=cx + r,
                ymin=cy - r, ymax=cy + r,
            ))

        elif e['type'] == 'LWPOLYLINE':
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)
            closed = bool(e['flags'] & 1)
            from core.dxf_loader import _polyline_path
            zones.append(DxfZone(
                zone_id=zone_id,
                # Pass empty bulges so spike sides render as straight lines.
                # The DXF bulge values produce arc-curved sides that don't
                # match the physical spike shapes on the mask.
                path=_polyline_path(xs, ys, [], closed),
                cx=cx, cy=cy,
                xmin=min(xs), xmax=max(xs),
                ymin=min(ys), ymax=max(ys),
            ))

    return sorted(zones, key=lambda z: z.zone_id)
