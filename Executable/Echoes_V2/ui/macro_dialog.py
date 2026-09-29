"""
Macro editor dialog — define the name and body of one macro button (1-9).

Body syntax (see core/macros.py):
  • Commands  — Echoes commands without a timestamp:
        FADE 255 255 127 99 22
    They are stamped with the insertion time when the macro fires.
  • Skip steps — timing-only, space the commands in time:
        SKIP 1/4          → advance x/y of a bar (bar = beats × 60 s/BPM)
  • Blank lines and '#' comments are ignored.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QVBoxLayout, QWidget,
)

from core import macros as macro_store


def _style(kind: str) -> str:
    """Small per-kind stylesheet fragments reused below."""
    return {
        'caption': (
            "color:#888888; font:11px 'Segoe UI';"
        ),
        'input': (
            "QLineEdit, QPlainTextEdit { background:#1e1e1e; color:#dcdcdc; "
            "border:1px solid #3f3f46; border-radius:3px; "
            "selection-background-color:#0e639c; }"
            "QLineEdit:focus, QPlainTextEdit:focus { border-color:#0e639c; }"
        ),
        'mono': "font-family:'Consolas'; font-size:11px;",
    }[kind]


class MacroEditorDialog(QDialog):
    """Edit one macro slot: a display name plus the command/skip body."""

    def __init__(self, index: int, name: str, content: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Macro {index}  \u2014  Echoes")
        self.setMinimumSize(520, 380)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Name row
        namerow = QHBoxLayout()
        namerow.setSpacing(6)
        lbl = QLabel(f"Macro {index}")
        lbl.setStyleSheet(
            "color:#e0b34e; font:bold 12px 'Segoe UI';")
        namerow.addWidget(lbl)
        self._name = QLineEdit(name)
        self._name.setPlaceholderText("Name \u2014 shown in the button tooltip")
        self._name.setStyleSheet(_style('input'))
        namerow.addWidget(self._name, 1)
        root.addLayout(namerow)

        # ── Body editor
        self._body = QPlainTextEdit(content)
        self._body.setStyleSheet(_style('input') + _style('mono'))
        self._body.setPlaceholderText(
            "FADE 255 255 127 99 22\n"
            "SKIP 1/4\n"
            "FADE 255 255 127 99 24\n"
            "SKIP 2/4\n"
            "FADE 255 255 127 99 22"
        )
        root.addWidget(self._body, 1)

        # ── Live summary of what the macro will do
        self._summary = QLabel("")
        self._summary.setStyleSheet(_style('caption'))
        root.addWidget(self._summary)

        # ── Help caption
        help_ = QLabel(
            "Each line is either a <b>command</b> (SET / RAMP / FADE \u2014 no "
            "timestamp) or a <b>skip step</b> <span style='color:#9cdcfe'>"
            "SKIP x/y</span>.  On trigger the commands are inserted in order "
            "starting at the current time; every skip step moves the next "
            "command forward by x/y of a bar, where one bar = (beats per bar) "
            "\u00d7 (60 s / BPM) from the transport bar.\n"
            "Example at 4/4 \u00b7 120 BPM (bar = 2 s):  "
            "FADE \u2192 T \u00b7 SKIP 1/4 \u2192 +0.5 s \u00b7 "
            "SKIP 2/4 \u2192 +1.0 s.\n"
            "Blank lines and '#' comments are ignored.")
        help_.setWordWrap(True)
        help_.setTextFormat(Qt.TextFormat.RichText)
        help_.setStyleSheet("color:#7a7a7a; font:10px 'Segoe UI';")
        root.addWidget(help_)

        # ── Buttons
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._body.textChanged.connect(self._refresh_summary)
        self._refresh_summary()

    # ── Slots / helpers ──────────────────────────────────────────────────────

    def _refresh_summary(self) -> None:
        body = self._body.toPlainText()
        n_cmd, n_skip = macro_store.stats(body)
        empty = not body.strip()
        if empty:
            text = "Empty \u2014 pressing this macro inserts nothing."
        else:
            text = (f"{n_cmd} command{'s' if n_cmd != 1 else ''}"
                    f" \u00b7 {n_skip} skip step{'s' if n_skip != 1 else ''}")
            if n_cmd == 0:
                text += "  \u26a0  no commands \u2014 nothing would be inserted"
        self._summary.setText(text)
        # OK stays enabled even for an empty body — that is how content is
        # cleared from a macro slot.

    def _on_accept(self) -> None:
        self.accept()

    def macro(self) -> tuple[str, str]:
        """Return the edited (name, content)."""
        return (self._name.text().strip(),
                self._body.toPlainText())
