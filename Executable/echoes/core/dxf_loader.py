"""
Shared DXF primitives for the Echoes LED canvas.

Provides the :class:`DxfZone` data type plus the low-level readers and path
builders used by :mod:`core.majoras_loader`, which owns the 28-zone Majora's
Mask layout:

  _read_pairs / _parse_entities   — raw DXF group-code parsing
  _circle_path / _polyline_path   — QPainterPath construction (bulge arcs
                                    included)

Paths are built in DXF coordinate space (Y-up).
The canvas applies its own QTransform (Y-flip + scale) at paint time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui  import QPainterPath


# ── Zone data type ────────────────────────────────────────────────────────────

@dataclass
class DxfZone:
    zone_id : int            # 1-based (1..28)
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
