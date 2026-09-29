"""
DXF zone loader for the Echoes LED canvas.

Reads areas_clean.dxf and returns 12 DxfZone objects:
  Zone  1 — left  eye  (CIRCLE entity, smallest X centroid)
  Zone  2 — right eye  (CIRCLE entity, largest  X centroid)
  Zones 3-12 — curved triangular spikes (closed LWPOLYLINE entities),
               numbered clockwise starting from the top-right spike.

Paths are built in DXF coordinate space (Y-up).
The canvas applies its own QTransform (Y-flip + scale) at paint time.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui  import QPainterPath


# ── Zone data type ────────────────────────────────────────────────────────────

@dataclass
class DxfZone:
    zone_id : int            # 1-based (1..12)
    path    : QPainterPath   # geometry in DXF coordinate space (Y-up)
    cx      : float          # centroid X (DXF units)
    cy      : float          # centroid Y (DXF units)
    xmin    : float
    xmax    : float
    ymin    : float
    ymax    : float


# ── DXF pair reader ───────────────────────────────────────────────────────────

def _read_pairs(path: str) -> list:
    with open(path, 'r', errors='replace') as fh:
        raw = fh.read().replace('\r\n', '\n').replace('\r', '\n')
    lines = raw.split('\n')
    out   = []
    i = 0
    while i + 1 < len(lines):
        try:
            out.append((int(lines[i].strip()), lines[i + 1].strip()))
        except ValueError:
            pass
        i += 2
    return out


def _parse_entities(pairs: list) -> list:
    """Split pair stream into entity dicts for CIRCLE and LWPOLYLINE."""
    _WANTED = {'CIRCLE', 'LWPOLYLINE'}
    ents = []
    cur  = None
    for code, val in pairs:
        if code == 0:
            if cur:
                ents.append(cur)
            cur = {
                'type'  : val,
                'handle': '',
                'flags' : 0,
                'xs'    : [],
                'ys'    : [],
                'r'     : None,       # CIRCLE radius  (code 40)
                'bulges': [],         # LWPOLYLINE arcs (code 42)
            }
        if not cur:
            continue
        if code == 5:  cur['handle'] = val
        if code == 70: cur['flags']  = int(val)
        if code == 10: cur['xs'].append(float(val))
        if code == 20: cur['ys'].append(float(val))
        if code == 40: cur['r'] = float(val)          # only meaningful for CIRCLE
        if code == 42: cur['bulges'].append(float(val))
    if cur:
        ents.append(cur)
    return [e for e in ents if e['type'] in _WANTED]


# ── Path builders ─────────────────────────────────────────────────────────────

def _circle_path(cx: float, cy: float, r: float) -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QPointF(cx, cy), r, r)
    return p


def _polyline_path(xs: list, ys: list, bulges: list, closed: bool) -> QPainterPath:
    """
    Build a QPainterPath from LWPOLYLINE vertices.
    Bulge values (code 42) curve the segment that follows each vertex.
    Bulge = tan(included_angle / 4); 0 = straight line.
    """
    n = len(xs)
    if n == 0:
        return QPainterPath()

    p = QPainterPath()
    p.moveTo(xs[0], ys[0])

    for i in range(n):
        j = (i + 1) % n
        if j == 0 and not closed:
            break
        x0, y0 = xs[i],   ys[i]
        x1, y1 = xs[j],   ys[j]
        b       = bulges[i] if i < len(bulges) else 0.0

        if abs(b) < 1e-6:
            p.lineTo(x1, y1)
        else:
            # Convert DXF bulge to an arc using QPainterPath.arcTo
            d     = math.hypot(x1 - x0, y1 - y0)
            if d < 1e-9:
                p.lineTo(x1, y1)
                continue
            r_    = d * (1.0 + b * b) / (4.0 * abs(b))
            # arc centre: perpendicular to midpoint
            mx, my   = (x0 + x1) / 2, (y0 + y1) / 2
            perp_x   = -(y1 - y0) / d
            perp_y   =  (x1 - x0) / d
            sagitta  = r_ - math.sqrt(max(0.0, r_ * r_ - (d / 2) ** 2))
            sign     = 1 if b > 0 else -1
            arc_cx   = mx + sign * (r_ - sagitta) * perp_x
            arc_cy   = my + sign * (r_ - sagitta) * perp_y
            # Qt arc parameters (angles in degrees, CCW positive)
            start_a  = math.degrees(math.atan2(y0 - arc_cy, x0 - arc_cx))
            sweep    = math.degrees(4.0 * math.atan(abs(b))) * sign
            rect     = QRectF(arc_cx - r_, arc_cy - r_, 2 * r_, 2 * r_)
            p.arcTo(rect, start_a, sweep)

    if closed:
        p.closeSubpath()
    return p


# ── Clockwise sort key ────────────────────────────────────────────────────────

def _cw_angle(cx: float, cy: float) -> float:
    """
    Clockwise angle (0°..360°) from screen-north (top of canvas).
    Assumes DXF Y-DOWN convention (positive cy = screen down):
      north  (cx= 0, cy=-1) → 0°
      east   (cx= 1, cy= 0) → 90°
      south  (cx= 0, cy= 1) → 180°
      west   (cx=-1, cy= 0) → 270°
    """
    return math.degrees(math.atan2(cx, -cy)) % 360.0


# ── Public API ────────────────────────────────────────────────────────────────

def load_zones(dxf_path: str) -> list:
    """
    Parse *dxf_path* and return a list of 12 DxfZone objects ordered 1..12.

    Returns an empty list if the file cannot be read or contains no usable
    geometry, so callers can fall back to the placeholder canvas gracefully.
    """
    try:
        pairs = _read_pairs(dxf_path)
        ents  = _parse_entities(pairs)
    except OSError:
        return []

    circles   = [e for e in ents if e['type'] == 'CIRCLE'     and e['xs']]
    polylines = [e for e in ents if e['type'] == 'LWPOLYLINE' and len(e['xs']) >= 2]

    if not circles and not polylines:
        return []

    zones: list[DxfZone] = []

    # ── Zones 1 & 2 — eye circles ─────────────────────────────────────────────
    # Left eye (negative X) → zone 1,  right eye (positive X) → zone 2
    for zone_id, c in enumerate(
            sorted(circles, key=lambda c: c['xs'][0]), start=1):
        cx, cy = c['xs'][0], c['ys'][0]
        r      = c['r'] if c['r'] is not None else 10.0
        zones.append(DxfZone(
            zone_id = zone_id,
            path    = _circle_path(cx, cy, r),
            cx=cx, cy=cy,
            xmin=cx - r, xmax=cx + r,
            ymin=cy - r, ymax=cy + r,
        ))

    # ── Zones 3-12 — triangular spikes ────────────────────────────────────────
    # Sort clockwise starting from the top-right spike
    polylines_cw = sorted(
        polylines,
        key=lambda p: _cw_angle(
            sum(p['xs']) / len(p['xs']),
            sum(p['ys']) / len(p['ys']),
        ),
    )
    for i, p in enumerate(polylines_cw):
        xs, ys = p['xs'], p['ys']
        cx     = sum(xs) / len(xs)
        cy     = sum(ys) / len(ys)
        closed = bool(p['flags'] & 1)
        zones.append(DxfZone(
            zone_id = i + 3,
            path    = _polyline_path(xs, ys, p['bulges'], closed),
            cx=cx, cy=cy,
            xmin=min(xs), xmax=max(xs),
            ymin=min(ys), ymax=max(ys),
        ))

    return sorted(zones, key=lambda z: z.zone_id)
