"""
LED canvas — SVG overlay + DXF zone glow.

Layer order (bottom → top):
  1. majoras.png     — background raster image
  2. echoes_outline.svg — dim outline overlay (QSvgRenderer, Y-flip corrected)
  3. Glow fills      — only for zones currently lit by the script engine
  4. Zone labels     — numbered badges at every centroid
"""
from __future__ import annotations

import math
import os
from typing import Optional

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui  import (
    QBrush, QColor, QFont, QPainter, QPen, QPixmap, QTransform,
)
from PySide6.QtWidgets import QWidget

_ASSETS   = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets')
_DXF      = os.path.join(_ASSETS, 'echoes_outline.dxf')
_BG_PATH  = os.path.join(_ASSETS, 'majoras.png')

# Eye anchor points ─ used to align the background image with DXF zones
# Image coords: centre of each green pupil in majoras.png (detected via PIL)
_IMG_EYE_L = (321.8, 372.6)   # left  eye pixel
_IMG_EYE_R = (555.8, 372.1)   # right eye pixel
# DXF coords of the same eyes (handle '100' and '127')
_DXF_EYE_L = (0.0,    0.0 )
_DXF_EYE_R = (146.33, 0.04)

_BG        = QColor("#1e1e1e")
_LABEL_DIM = QColor(90, 110, 130)
_LABEL_LIT = QColor(230, 230, 230)


class LEDCanvas(QWidget):
    """28-zone Majora’s Mask canvas: PNG background + DXF zone rendering."""

    zone_right_clicked = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._led_states: dict = {}
        self._zones:      list = []
        self._last_transform: QTransform = QTransform()
        self.setMinimumSize(320, 240)

        px = QPixmap(_BG_PATH)
        self._pixmap: Optional[QPixmap] = px if (px and not px.isNull()) else None
        self._bg_visible: bool = True
        self._labels_visible: bool = True

        self._try_load_dxf()

    # ── Public API ────────────────────────────────────────────────────────────

    def update_leds(self, states: dict) -> None:
        """Receive evaluated LED colour map {zone_id: (r,g,b)} and repaint."""
        self._led_states = states
        self.update()

    def set_bg_visible(self, visible: bool) -> None:
        """Show or hide the background raster image."""
        self._bg_visible = visible
        self.update()

    def set_labels_visible(self, visible: bool) -> None:
        """Show or hide zone number labels."""
        self._labels_visible = visible
        self.update()

    def set_hw_version(self, v2: bool) -> None:
        """Reload zones for V1 (eyes 27-28) or V2 (eyes 1-2)."""
        from core.majoras_loader import load_zones
        self._zones = load_zones(_DXF, hw_v2=v2)
        self._led_states = {}
        self.update()

    # ── Qt overrides ─────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, _BG)

        if self._zones:
            # Background + zones drawn together so they share the same transform
            self._draw_zones(painter, w, h)
        elif self._pixmap and self._bg_visible:
            _draw_pixmap(painter, self._pixmap, w, h)   # fallback (no DXF)
        else:
            _draw_placeholder(painter, w, h)

        painter.end()

    # ── Private ───────────────────────────────────────────────────────────────

    def _try_load_dxf(self) -> None:
        if not os.path.isfile(_DXF):
            return
        try:
            from core.majoras_loader import load_zones
            self._zones = load_zones(_DXF)
        except Exception as exc:
            print(f'[LEDCanvas] DXF load failed: {exc}')

    def contextMenuEvent(self, event) -> None:
        """Right-click on a zone: emit zone_right_clicked with its ID."""
        if not self._zones:
            event.ignore()
            return
        inv, ok = self._last_transform.inverted()
        if not ok:
            event.ignore()
            return
        dxf_pt = inv.map(QPointF(event.pos()))
        for zone in self._zones:
            if zone.path.contains(dxf_pt):
                self.zone_right_clicked.emit(zone.zone_id)
                event.accept()
                return
        event.ignore()

    def _build_transform(self, w: int, h: int) -> QTransform:
        """
        Compute a QTransform from DXF coordinate space to screen space
        that centres all zones and fills ~88% of the canvas.
        """
        all_x = [z.xmin for z in self._zones] + [z.xmax for z in self._zones]
        all_y = [z.ymin for z in self._zones] + [z.ymax for z in self._zones]
        dxf_cx = (min(all_x) + max(all_x)) / 2
        dxf_cy = (min(all_y) + max(all_y)) / 2
        dxf_w  = max(all_x) - min(all_x) or 1
        dxf_h  = max(all_y) - min(all_y) or 1
        scale  = min(w / dxf_w, h / dxf_h) * 0.88

        t = QTransform()
        t.translate(w / 2, h / 2)       # move origin to canvas centre
        t.scale(scale, scale)           # scale only — DXF uses Y-down (screen convention)
        t.translate(-dxf_cx, -dxf_cy)  # centre DXF world
        self._last_transform = t
        return t

    def _draw_zones(self, painter: QPainter, w: int, h: int) -> None:
        t = self._build_transform(w, h)
        # ── Background image — aligned via eye anchors ────────────────────────
        # Compute a similarity transform (uniform scale + translate) that maps
        # the two pupil pixels in majoras.png to the DXF eye zone centres.
        if self._pixmap and self._bg_visible:
            sc_l = t.map(QPointF(*_DXF_EYE_L))  # left  eye on screen
            sc_r = t.map(QPointF(*_DXF_EYE_R))  # right eye on screen
            screen_sep = math.hypot(sc_r.x() - sc_l.x(), sc_r.y() - sc_l.y())
            img_sep    = math.hypot(_IMG_EYE_R[0] - _IMG_EYE_L[0],
                                    _IMG_EYE_R[1] - _IMG_EYE_L[1])
            if img_sep > 0:
                s  = screen_sep / img_sep          # uniform scale
                tx = sc_l.x() - _IMG_EYE_L[0] * s
                ty = sc_l.y() - _IMG_EYE_L[1] * s
                target = QRectF(tx, ty,
                                self._pixmap.width()  * s,
                                self._pixmap.height() * s)
                painter.setTransform(QTransform())
                painter.drawPixmap(target.toAlignedRect(), self._pixmap)
        # ── Zone rendering ────────────────────────────────────────────────────
        # A zone is "circular" when its bounding box is square (circles from
        # DXF CIRCLE entities).  Spikes (LWPOLYLINE) have elongated bboxes.
        # We draw circle outlines directly from DXF data — the SVG Y-flip
        # transform distorts Qt arc rendering, so we skip the SVG entirely.

        _CIRCLE_TOL = 1.0   # DXF units: bbox width vs height must be this close
        _MIN_R_PX   = 5     # minimum rendered radius in screen pixels

        circle_pen = QPen(QColor(100, 130, 160,  70), 0.8)
        circle_pen.setCosmetic(True)
        spike_pen  = QPen(QColor(100, 130, 160,  70), 0.8)
        spike_pen.setCosmetic(True)

        font = QFont('Consolas', 8)
        font.setBold(True)
        painter.setFont(font)

        for zone in self._zones:
            rgb = self._led_states.get(zone.zone_id)

            sc  = t.map(QPointF(zone.cx, zone.cy))
            se  = t.map(QPointF(zone.xmax, zone.cy))
            scr = max(abs(se.x() - sc.x()), _MIN_R_PX)   # screen radius (clamped)

            dxf_bw = zone.xmax - zone.xmin
            dxf_bh = zone.ymax - zone.ymin
            is_circle = abs(dxf_bw - dxf_bh) < _CIRCLE_TOL

            painter.setTransform(QTransform())

            if is_circle:
                # ── Circle zone: outline (unlit) or filled glow (lit) ─────────
                dot = QRectF(sc.x() - scr, sc.y() - scr, scr * 2, scr * 2)
                if rgb:
                    r, g, b = rgb
                    painter.setPen(QPen(QColor(r, g, b).lighter(180), 1.5))
                    painter.setBrush(QBrush(QColor(r, g, b, 200)))
                else:
                    painter.setPen(circle_pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(dot)

            else:
                # ── Spike zone: always draw dim outline; glow when lit ────────
                painter.setTransform(t)
                if rgb:
                    r, g, b = rgb
                    gp = QPen(QColor(r, g, b, 160), 3.0)
                    gp.setCosmetic(True)
                    painter.setPen(gp)
                    painter.setBrush(QBrush(QColor(r, g, b, 50)))
                    painter.drawPath(zone.path)
                    lp = QPen(QColor(r, g, b).lighter(180), 1.2)
                    lp.setCosmetic(True)
                    painter.setPen(lp)
                    painter.setBrush(QBrush(QColor(r, g, b, 160)))
                    painter.drawPath(zone.path)
                else:
                    painter.setPen(spike_pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(zone.path)

            # ── Label badge ───────────────────────────────────────────────────
            if self._labels_visible:
                painter.setTransform(QTransform())
                lcolor = _LABEL_LIT if rgb else _LABEL_DIM
                bg_r   = QRectF(sc.x() - 10, sc.y() - 7, 20, 14)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(0, 0, 0, 150)))
                painter.drawRoundedRect(bg_r, 3, 3)
                painter.setPen(QPen(lcolor))
                painter.drawText(bg_r, Qt.AlignmentFlag.AlignCenter, str(zone.zone_id))


# ── Module-level helpers ──────────────────────────────────────────────────────

def _draw_pixmap(painter: QPainter, px: QPixmap, w: int, h: int) -> None:
    scaled = px.scaled(w, h,
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    painter.drawPixmap((w - scaled.width()) // 2,
                       (h - scaled.height()) // 2, scaled)


def _draw_placeholder(painter: QPainter, w: int, h: int) -> None:
    painter.setPen(QColor('#3a3a3a'))
    painter.drawRect(2, 2, w - 4, h - 4)
    painter.setPen(QColor('#555555'))
    painter.drawText(0, 0, w, h, Qt.AlignmentFlag.AlignCenter,
                     'LED canvas\n\nPlace assets/echoes_outline.dxf')

