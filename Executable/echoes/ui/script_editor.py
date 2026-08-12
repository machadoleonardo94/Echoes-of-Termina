"""
Script editor widget: a QPlainTextEdit with:
  • Echoes syntax highlighting (timestamp / command / RGB / LED-spec / comments)
  • Line-number gutter with active-command indicator
  • Full-width extra selections for the active playhead line (green) and parse
    errors (red), updated without interrupting the user's cursor.
  • Debounced `script_changed` signal (400 ms after last keystroke).
"""
from __future__ import annotations

import re

from PySide6.QtCore  import Qt, QRect, QSize, QTimer, Signal
from PySide6.QtGui   import (
    QColor, QFont, QPainter, QTextCharFormat, QTextCursor,
    QTextDocument, QTextFormat, QSyntaxHighlighter,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget


# ── Syntax highlighter ──────────────────────────────────────────────────────


class _Highlighter(QSyntaxHighlighter):
    _C_TS   = QColor("#4ec9b0")   # teal    — timestamp / duration
    _C_CMD  = QColor("#dcdcaa")   # yellow  — command keyword
    _C_RGB  = QColor("#c586c0")   # purple  — RGB values (start / single)
    _C_RGB2 = QColor("#b57edd")   # violet  — RAMP end RGB
    _C_DUR  = QColor("#ce9178")   # rust    — duration / percent parameter
    _C_LED  = QColor("#4fc1ff")   # blue    — LED spec
    _C_CMT  = QColor("#6a9955")   # green   — comments
    _C_ERR  = QColor("#f44747")   # red     — malformed tokens

    def __init__(self, doc: QTextDocument) -> None:
        super().__init__(doc)

    @staticmethod
    def _fmt(color: QColor, bold: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(color)
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        return f

    def highlightBlock(self, text: str) -> None:
        # ── comments ─────────────────────────────────────────────────────────
        cmt = re.search(r'#.*$', text)
        if cmt:
            self.setFormat(cmt.start(), len(text) - cmt.start(),
                           self._fmt(self._C_CMT))
            active = text[:cmt.start()]
        else:
            active = text

        # ── tokenise with absolute positions ─────────────────────────────────
        tokens = [(m.group(), m.start()) for m in re.finditer(r'\S+', active)]
        if not tokens:
            return

        idx = 0

        # Token 0 — timestamp
        tok, pos = tokens[idx]
        if re.match(r'^\d+:[0-5]\d(?:\.\d{1,3})?$', tok):
            self.setFormat(pos, len(tok), self._fmt(self._C_TS))
            idx += 1
        else:
            self.setFormat(pos, len(tok), self._fmt(self._C_ERR, bold=True))
            return   # can't colour the rest without a valid timestamp

        if idx >= len(tokens):
            return

        # Token 1 — command keyword
        tok, pos = tokens[idx]
        cmd = tok.upper()
        if cmd in ('SET', 'FADE', 'RAMP', 'BLINK', 'OFF', 'ON'):
            self.setFormat(pos, len(tok), self._fmt(self._C_CMD))
            idx += 1
        else:
            self.setFormat(pos, len(tok), self._fmt(self._C_ERR, bold=True))
            return

        def _rgb(start_idx: int, color: QColor) -> None:
            for i in range(3):
                j = start_idx + i
                if j < len(tokens):
                    t, p = tokens[j]
                    ok = re.match(r'^\d+$', t) and 0 <= int(t) <= 255
                    self.setFormat(p, len(t),
                                   self._fmt(color if ok else self._C_ERR))

        def _scalar(token_idx: int, color: QColor,
                    valid_fn=None) -> None:
            if token_idx < len(tokens):
                t, p = tokens[token_idx]
                ok = valid_fn(t) if valid_fn else True
                self.setFormat(p, len(t),
                               self._fmt(color if ok else self._C_ERR))

        def _led(token_idx: int) -> None:
            if token_idx < len(tokens):
                t, p = tokens[token_idx]
                self.setFormat(p, len(t), self._fmt(self._C_LED))

        if cmd == 'SET':
            # SET  R G B  LED-SPEC
            _rgb(idx, self._C_RGB)
            _led(idx + 3)

        elif cmd == 'RAMP':
            # RAMP  DURATION  R0 G0 B0  R1 G1 B1  LED-SPEC
            _scalar(idx, self._C_DUR,
                    lambda t: re.match(r'^\d+:[0-5]\d$', t))   # duration
            _rgb(idx + 1, self._C_RGB)                          # start colour
            _rgb(idx + 4, self._C_RGB2)                         # end colour
            _led(idx + 7)

        elif cmd == 'FADE':
            # FADE  R G B  PERCENT  LED-SPEC
            _rgb(idx, self._C_RGB)
            _scalar(idx + 3, self._C_DUR,
                    lambda t: re.match(r'^\d+$', t) and 1 <= int(t) <= 99)
            _led(idx + 4)


# ── Line-number gutter ───────────────────────────────────────────────────────


class _LineNumberArea(QWidget):
    def __init__(self, editor: ScriptEditor) -> None:
        super().__init__(editor)
        self._ed = editor

    def sizeHint(self) -> QSize:
        return QSize(self._ed._gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self._ed._paint_gutter(event)


# ── Main editor ──────────────────────────────────────────────────────────────


class ScriptEditor(QPlainTextEdit):
    """Syntax-highlighted plain-text editor for Echoes script files."""

    script_changed = Signal(str)   # emitted 400 ms after the last keystroke

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._gutter      = _LineNumberArea(self)
        self._active_line = -1          # 1-based; −1 = none
        self._error_lines: set[int] = set()

        # Monospace font
        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(28)

        _Highlighter(self.document())

        # Debounce — wait 400 ms of silence before emitting
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(
            lambda: self.script_changed.emit(self.toPlainText()))
        self.textChanged.connect(self._debounce.start)

        # Gutter plumbing
        self.blockCountChanged.connect(lambda _: self._sync_gutter_width())
        self.updateRequest.connect(self._update_gutter)
        self._sync_gutter_width()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_active_line(self, line: int) -> None:
        """Highlight *line* (1-based) as the current playhead command."""
        if self._active_line != line:
            self._active_line = line
            self._gutter.update()
            self._refresh_extras()

    def set_error_lines(self, lines: set) -> None:
        """Highlight the given 1-based line numbers as parse errors."""
        if self._error_lines != lines:
            self._error_lines = lines
            self._refresh_extras()

    def insert_command_line(self, line: str) -> None:
        """
        Insert *line* into the document in ascending timestamp order.

        Scans existing blocks for their leading M:SS timestamp; inserts
        before the first block whose timestamp is strictly greater than
        the new line's timestamp, or appends at the end.
        """
        from core.script_engine import _parse_timestamp

        tokens = line.split()
        new_ms = _parse_timestamp(tokens[0]) if tokens else None

        doc = self.document()
        n   = doc.blockCount()

        insert_before = n   # default: append
        if new_ms is not None:
            for i in range(n):
                blk = doc.findBlockByNumber(i)
                txt = blk.text().strip()
                if not txt or txt.startswith('#'):
                    continue
                toks = txt.split()
                ts = _parse_timestamp(toks[0]) if toks else None
                if ts is not None and ts > new_ms:
                    insert_before = i
                    break

        cursor = QTextCursor(doc)
        if insert_before < n:
            blk = doc.findBlockByNumber(insert_before)
            cursor.setPosition(blk.position())
            cursor.insertText(line + '\n')
        else:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            last_text = doc.findBlockByNumber(n - 1).text()
            if last_text.strip():
                cursor.insertText('\n')
            cursor.insertText(line)

        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    # ── Extra selections ─────────────────────────────────────────────────────

    def _refresh_extras(self) -> None:
        sels = []
        doc  = self.document()

        def _make(block_num_0: int,
                  bg: QColor,
                  fg: QColor | None = None) -> QTextEdit.ExtraSelection:
            block = doc.findBlockByNumber(block_num_0)
            if not block.isValid():
                return None
            s          = QTextEdit.ExtraSelection()
            s.cursor   = QTextCursor(block)
            s.cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            s.format   = QTextCharFormat()
            s.format.setBackground(bg)
            if fg is not None:
                s.format.setForeground(fg)
            s.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            return s

        # ── Active playhead line  —  bright, easy to follow while playing
        if self._active_line > 0:
            sel = _make(
                self._active_line - 1,
                bg = QColor("#1a3a5c"),    # steel blue  — stands out clearly
                fg = QColor("#e8e8e8"),    # near-white text for readability
            )
            if sel:
                sels.append(sel)

        # ── Error lines  —  subtle red, only for non-active lines
        for ln in self._error_lines:
            if ln != self._active_line:
                sel = _make(ln - 1, bg=QColor("#4a1515"))
                if sel:
                    sels.append(sel)

        self.setExtraSelections(sels)

    # ── Gutter internals ─────────────────────────────────────────────────────

    def _gutter_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance('9') * digits

    def _sync_gutter_width(self) -> None:
        self.setViewportMargins(self._gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(
                0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._sync_gutter_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(
            QRect(cr.left(), cr.top(), self._gutter_width(), cr.height()))

    def _paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor("#252526"))

        block = self.firstVisibleBlock()
        num   = block.blockNumber()
        top   = round(
            self.blockBoundingGeometry(block)
                .translated(self.contentOffset()).top())
        bot   = top + round(self.blockBoundingRect(block).height())
        lh    = self.fontMetrics().height()
        w     = self._gutter.width() - 4

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bot >= event.rect().top():
                is_active = (num + 1 == self._active_line)
                painter.setPen(QColor("#5aafef" if is_active else "#585858"))
                if is_active:
                    # Bold weight for the active line number
                    f = painter.font()
                    f.setBold(True)
                    painter.setFont(f)
                painter.drawText(
                    0, top, w, lh, Qt.AlignmentFlag.AlignRight, str(num + 1))
                if is_active:
                    f = painter.font()
                    f.setBold(False)
                    painter.setFont(f)
            block = block.next()
            top   = bot
            bot   = top + round(self.blockBoundingRect(block).height())
            num  += 1

        painter.end()
