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
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)


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

    def set_plain_text_silent(self, text: str) -> None:
        """
        Set document text without triggering the script_changed debounce.
        Syntax highlighting and gutter still update normally.
        """
        self._debounce.stop()
        self.textChanged.disconnect(self._debounce.start)
        self.setPlainText(text)
        self.textChanged.connect(self._debounce.start)

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


# ── Text-level insert helper ─────────────────────────────────────────────────

def _insert_line_into_text(text: str, new_line: str) -> str:
    """
    Insert *new_line* into *text* in ascending timestamp order.
    Used when the target track is not open in a visible editor.
    """
    from core.script_engine import _parse_timestamp

    tokens = new_line.split()
    new_ms = _parse_timestamp(tokens[0]) if tokens else None

    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'

    if new_ms is None:
        return text.rstrip('\n') + '\n' + new_line + '\n'

    insert_before = len(lines)
    for i, raw in enumerate(lines):
        stripped = raw.split('#', 1)[0].strip()
        if not stripped:
            continue
        toks = stripped.split()
        ts = _parse_timestamp(toks[0]) if toks else None
        if ts is not None and ts > new_ms:
            insert_before = i
            break

    lines.insert(insert_before, new_line + '\n')
    return ''.join(lines)


# ── Multi-track editor panel ───────────────────────────────────────────

MAX_TRACKS = 6   # maximum number of track slots per project──────


class MultiTrackEditorWidget(QWidget):
    """
    Wraps one or two ScriptEditors with a track-selector dropdown.

    The *left* editor is the 'active' editor — its content is fed to the
    script engine for parsing and playhead highlighting.  A second editor
    can be toggled on via the Split button, letting the user read or edit a
    different track in parallel without mixing them up.

    Public API (mirrors the ScriptEditor subset used by MainWindow):
        set_tracks(dict[str, str])   — load all tracks, rebuild dropdowns
        get_tracks() -> dict         — return all tracks with live editor text
        set_active_line(int)         — forward to left editor
        set_error_lines(set)         — forward to left editor
        insert_command_line(str)     — insert into focused editor (default left)
        setPlaceholderText(str)      — forward to left editor

    Signal:
        active_script_changed(str)  — emitted when the active (left) track text
                                       changes, with the new text as argument.
    """

    active_script_changed    = Signal(str)        # left track text changed
    background_script_changed = Signal(str, str)   # (track_name, text) right editor
    tracks_changed            = Signal(list)        # list[str] track names changed

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._tracks: dict[str, str] = {}
        self._left_track:  str = ''
        self._right_track: str = ''

        # ── Header bar ────────────────────────────────────────────────────────
        _combo_style = (
            "QComboBox { background:#3c3c3c; color:#cccccc; "
            "border:1px solid #555555; border-radius:2px; "
            "padding-left:4px; font:11px 'Segoe UI'; min-height:20px; }"
            "QComboBox::drop-down { border:0; width:16px; }"
            "QComboBox QAbstractItemView { background:#3c3c3c; color:#cccccc; "
            "selection-background-color:#0e639c; }"
        )

        header = QWidget()
        header.setFixedHeight(30)
        header.setStyleSheet("background:#252526; border-bottom:1px solid #1e1e1e;")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(6, 3, 6, 3)
        hbox.setSpacing(4)

        lbl = QLabel("Track:")
        lbl.setStyleSheet("color:#888888; font:11px 'Segoe UI';")
        hbox.addWidget(lbl)

        self._left_combo = QComboBox()
        self._left_combo.setStyleSheet(_combo_style)
        hbox.addWidget(self._left_combo, 1)

        _btn_style = (
            "QPushButton { background:#3c3c3c; color:#cccccc; "
            "border:1px solid #555555; border-radius:2px; "
            "font:12px 'Segoe UI'; min-width:22px; min-height:20px; padding:0 4px; }"
            "QPushButton:hover:enabled { border-color:#888888; }"
            "QPushButton:disabled { color:#555555; border-color:#3a3a3a; }"
        )

        self._add_btn = QPushButton("+")
        self._add_btn.setFixedSize(24, 22)
        self._add_btn.setToolTip(f"Add a new empty track slot  (max {MAX_TRACKS})")
        self._add_btn.setStyleSheet(_btn_style)
        self._add_btn.clicked.connect(self._on_add_track)
        hbox.addWidget(self._add_btn)

        self._del_btn = QPushButton("−")
        self._del_btn.setFixedSize(24, 22)
        self._del_btn.setToolTip("Delete the current track slot")
        self._del_btn.setStyleSheet(_btn_style)
        self._del_btn.clicked.connect(self._on_del_track)
        hbox.addWidget(self._del_btn)

        self._split_btn = QPushButton("Split")
        self._split_btn.setCheckable(True)
        self._split_btn.setFixedWidth(48)
        self._split_btn.setStyleSheet(
            "QPushButton { background:#3c3c3c; color:#aaaaaa; "
            "border:1px solid #555555; border-radius:2px; "
            "padding:0 6px; font:11px 'Segoe UI'; min-height:20px; }"
            "QPushButton:checked { background:#0e639c; color:#ffffff; "
            "border-color:#1177bb; }"
            "QPushButton:hover:!checked { border-color:#888888; }"
        )
        hbox.addWidget(self._split_btn)

        # Separator between left and right header sections
        self._sep = QWidget()
        self._sep.setFixedWidth(2)
        self._sep.setStyleSheet("background:#1e1e1e;")
        self._sep.hide()
        hbox.addWidget(self._sep)

        self._right_lbl = QLabel("Track:")
        self._right_lbl.setStyleSheet("color:#888888; font:11px 'Segoe UI';")
        self._right_lbl.hide()
        hbox.addWidget(self._right_lbl)

        self._right_combo = QComboBox()
        self._right_combo.setStyleSheet(_combo_style)
        self._right_combo.hide()
        hbox.addWidget(self._right_combo, 1)

        # ── Editors ───────────────────────────────────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(2)

        self._left_editor  = ScriptEditor()
        self._right_editor = ScriptEditor()
        self._right_editor.hide()

        self._splitter.addWidget(self._left_editor)
        self._splitter.addWidget(self._right_editor)

        # ── Layout ────────────────────────────────────────────────────────────
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        vbox.addWidget(header)
        vbox.addWidget(self._splitter, 1)

        # ── Wiring ────────────────────────────────────────────────────────────
        self._left_combo.currentTextChanged.connect(self._on_left_combo)
        self._right_combo.currentTextChanged.connect(self._on_right_combo)
        self._split_btn.toggled.connect(self._on_split_toggled)
        self._left_editor.script_changed.connect(self._on_left_text_changed)
        self._right_editor.script_changed.connect(self._on_right_text_changed)
        self._refresh_slot_buttons()
    # ── Public API ────────────────────────────────────────────────────────────

    def set_tracks(self, tracks: dict[str, str]) -> None:
        """Replace all tracks and rebuild the dropdowns."""
        self._tracks = dict(tracks)
        names = list(tracks.keys())

        for combo in (self._left_combo, self._right_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            combo.blockSignals(False)

        self._left_track = names[0] if names else ''
        self._left_editor.set_plain_text_silent(
            tracks.get(self._left_track, ''))
        self.active_script_changed.emit(
            tracks.get(self._left_track, ''))

        # Pre-select a different track in the right editor when possible
        right_idx = 1 if len(names) > 1 else 0
        self._right_track = names[right_idx] if names else ''
        self._right_combo.blockSignals(True)
        self._right_combo.setCurrentIndex(right_idx)
        self._right_combo.blockSignals(False)
        self._right_editor.set_plain_text_silent(
            tracks.get(self._right_track, ''))

        self._refresh_slot_buttons()
        self.tracks_changed.emit(names)

    def get_tracks(self) -> dict[str, str]:
        """Return all tracks, syncing current editor contents first."""
        if self._left_track:
            self._tracks[self._left_track] = self._left_editor.toPlainText()
        if self._right_track and self._right_track != self._left_track:
            self._tracks[self._right_track] = self._right_editor.toPlainText()
        return dict(self._tracks)

    def active_track_name(self) -> str:
        """Return the name of the currently active (left) track."""
        return self._left_track

    def set_active_line(self, line: int) -> None:
        self._left_editor.set_active_line(line)

    def set_error_lines(self, lines: set) -> None:
        self._left_editor.set_error_lines(lines)

    def insert_command_line(self, line: str) -> None:
        """Insert into whichever editor has focus, defaulting to left."""
        if self._right_editor.isVisible() and self._right_editor.hasFocus():
            self._right_editor.insert_command_line(line)
        else:
            self._left_editor.insert_command_line(line)

    def insert_command_line_to(self, track_name: str, line: str) -> None:
        """
        Insert *line* into a specific track slot in timestamp order.

        Routes through the visible editor when the target is already displayed
        (gives the user cursor feedback).  For hidden tracks the insertion is
        applied directly to the stored text without switching editors.
        """
        if track_name == self._left_track:
            self._left_editor.insert_command_line(line)
        elif track_name == self._right_track and self._right_editor.isVisible():
            self._right_editor.insert_command_line(line)
        elif track_name in self._tracks:
            self._tracks[track_name] = _insert_line_into_text(
                self._tracks[track_name], line)

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802
        self._left_editor.setPlaceholderText(text)

    @property
    def left_editor(self) -> 'ScriptEditor':
        """Direct access to the active (left) ScriptEditor for advanced operations."""
        return self._left_editor

    # ── Slot management ───────────────────────────────────────────────────────

    def _refresh_slot_buttons(self) -> None:
        n = len(self._tracks)
        self._add_btn.setEnabled(n < MAX_TRACKS)
        self._add_btn.setToolTip(
            f"Add a new empty track slot  ({n}/{MAX_TRACKS})"
            if n >= MAX_TRACKS else
            f"Add a new empty track slot  (max {MAX_TRACKS})"
        )
        self._del_btn.setEnabled(n > 1)

    def _on_add_track(self) -> None:
        if len(self._tracks) >= MAX_TRACKS:
            return
        name, ok = QInputDialog.getText(
            self, "New track slot", "Slot name:",
            text=f"Track {len(self._tracks) + 1}",
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        # Ensure unique name
        base, n = name, 2
        while name in self._tracks:
            name = f"{base} ({n})"; n += 1
        self._tracks[name] = ''
        # Rebuild both combos
        for combo in (self._left_combo, self._right_combo):
            combo.blockSignals(True)
            combo.addItem(name)
            combo.blockSignals(False)
        self._refresh_slot_buttons()
        self.tracks_changed.emit(list(self._tracks.keys()))

    def _on_del_track(self) -> None:
        if len(self._tracks) <= 1 or not self._left_track:
            return
        ans = QMessageBox.question(
            self, "Delete slot",
            f"Delete track slot \"{self._left_track}\"?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        del self._tracks[self._left_track]
        names = list(self._tracks.keys())
        for combo in (self._left_combo, self._right_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(names)
            combo.blockSignals(False)
        self._left_track = names[0]
        self._left_combo.setCurrentIndex(0)
        self._left_editor.set_plain_text_silent(self._tracks[self._left_track])
        self.active_script_changed.emit(self._left_editor.toPlainText())
        right_idx = 1 if len(names) > 1 else 0
        self._right_track = names[right_idx]
        self._right_combo.blockSignals(True)
        self._right_combo.setCurrentIndex(right_idx)
        self._right_combo.blockSignals(False)
        self._right_editor.set_plain_text_silent(self._tracks[self._right_track])
        self._refresh_slot_buttons()
        self.tracks_changed.emit(list(self._tracks.keys()))

    def _on_left_combo(self, new_name: str) -> None:
        if not new_name or new_name == self._left_track:
            return
        if self._left_track:
            self._tracks[self._left_track] = self._left_editor.toPlainText()
        self._left_track = new_name
        self._left_editor.set_plain_text_silent(self._tracks.get(new_name, ''))
        self.active_script_changed.emit(self._left_editor.toPlainText())

    def _on_right_combo(self, new_name: str) -> None:
        if not new_name or new_name == self._right_track:
            return
        if self._right_track:
            self._tracks[self._right_track] = self._right_editor.toPlainText()
        self._right_track = new_name
        self._right_editor.set_plain_text_silent(self._tracks.get(new_name, ''))

    def _on_split_toggled(self, on: bool) -> None:
        self._right_editor.setVisible(on)
        self._right_combo.setVisible(on)
        self._right_lbl.setVisible(on)
        self._sep.setVisible(on)
        if on:
            w = self._splitter.width()
            self._splitter.setSizes([w // 2, w // 2])

    def _on_left_text_changed(self, text: str) -> None:
        if self._left_track:
            self._tracks[self._left_track] = text
        self.active_script_changed.emit(text)

    def _on_right_text_changed(self, text: str) -> None:
        if self._right_track:
            self._tracks[self._right_track] = text
            self.background_script_changed.emit(self._right_track, text)
