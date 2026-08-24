"""
Metadata editor dialog for .echoes script files.

Editable fields:  title, author, bpm, timesig, comment
Read-only fields: duration (auto from audio), modified (auto on save)
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QLabel,
    QLineEdit, QTextEdit, QVBoxLayout,
)
from PySide6.QtCore import Qt


_FIELD_STYLE = (
    "QLineEdit, QTextEdit, QComboBox {"
    "  background: #2d2d2d; color: #d4d4d4;"
    "  border: 1px solid #555; border-radius: 3px;"
    "  padding: 2px 4px; font: 11px 'Segoe UI';"
    "}"
    "QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color: #007acc; }"
    "QLineEdit:read-only { color: #777777; background: #222222; }"
    "QComboBox::drop-down { border: none; width: 18px; }"
    "QComboBox QAbstractItemView {"
    "  background: #2d2d2d; color: #d4d4d4; selection-background-color: #005f9e;"
    "}"
)

_LABEL_STYLE = "color: #aaaaaa; font: 11px 'Segoe UI';"


# Common time signatures with denominators 2, 4, or 8
_TIME_SIGNATURES = [
    "2/2", "2/4", "2/8",
    "3/4", "3/8",
    "4/4", "4/8",
    "5/4", "5/8",
    "6/4", "6/8",
    "7/4", "7/8",
    "9/8", "12/8",
]


class MetadataDialog(QDialog):
    """Modal dialog for viewing and editing .echoes file metadata."""

    def __init__(self, meta: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Script Properties")
        self.setMinimumWidth(500)
        self.setStyleSheet("background: #1e1e1e;")
        # Keep unknown/auto-managed keys (e.g. 'audio') across edits
        self._orig = dict(meta)

        form = QFormLayout()
        form.setSpacing(8)
        form.setContentsMargins(12, 12, 12, 4)

        def _label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(_LABEL_STYLE)
            return lbl

        self._title  = QLineEdit(meta.get('title', ''))
        self._title.setPlaceholderText("Show title")

        self._author = QLineEdit(meta.get('author', ''))
        self._author.setPlaceholderText("Composer / arranger")

        self._charter = QLineEdit(meta.get('charter', ''))
        self._charter.setPlaceholderText("Charter / creator")

        self._album = QLineEdit(meta.get('album', ''))
        self._album.setPlaceholderText("Album or game title")

        self._year = QLineEdit(meta.get('year', ''))
        self._year.setPlaceholderText("Release year")

        self._bpm = QLineEdit(meta.get('bpm', '') or '120')
        self._bpm.setToolTip("Beats per minute — integer between 20 and 300")

        # Time signature drop-down (editable for unusual meters)
        self._timesig = QComboBox()
        self._timesig.setEditable(True)
        self._timesig.addItems(_TIME_SIGNATURES)
        current_ts = meta.get('timesig', '') or '4/4'
        idx = self._timesig.findText(current_ts)
        if idx >= 0:
            self._timesig.setCurrentIndex(idx)
        else:
            self._timesig.setCurrentText(current_ts)

        self._track1 = QLineEdit(meta.get('track1', ''))
        self._track1.setPlaceholderText("e.g. ExpertSingle")
        self._track1.setToolTip("Label for the first imported chart track")

        self._track2 = QLineEdit(meta.get('track2', ''))
        self._track2.setPlaceholderText("e.g. ExpertDoubleBass")
        self._track2.setToolTip("Label for the second imported chart track")

        self._track3 = QLineEdit(meta.get('track3', ''))
        self._track3.setPlaceholderText("optional")
        self._track3.setToolTip("Label for the third imported chart track")

        self._track4 = QLineEdit(meta.get('track4', ''))
        self._track4.setPlaceholderText("optional")
        self._track4.setToolTip("Label for the fourth imported chart track")

        self._duration = QLineEdit(meta.get('duration', ''))
        self._duration.setReadOnly(True)
        self._duration.setToolTip("Auto-populated when audio is loaded")

        self._modified = QLineEdit(meta.get('modified', ''))
        self._modified.setReadOnly(True)
        self._modified.setToolTip("Updated automatically on save")

        self._comment = QTextEdit()
        self._comment.setPlainText(meta.get('comment', ''))
        self._comment.setFixedHeight(88)
        self._comment.setToolTip("Free-form notes about the show or arrangement")

        for widget in (
            self._title, self._author, self._charter, self._album, self._year,
            self._bpm, self._timesig,
            self._track1, self._track2, self._track3, self._track4,
            self._duration, self._modified, self._comment,
        ):
            widget.setStyleSheet(_FIELD_STYLE)

        def _sep() -> QFrame:
            f = QFrame()
            f.setFrameShape(QFrame.Shape.HLine)
            f.setStyleSheet("color:#333;")
            return f

        form.addRow(_label("Title:"),            self._title)
        form.addRow(_label("Author:"),           self._author)
        form.addRow(_label("Charter:"),          self._charter)
        form.addRow(_label("Album:"),            self._album)
        form.addRow(_label("Year:"),             self._year)
        form.addRow(_sep())
        form.addRow(_label("Beats per minute:"), self._bpm)
        form.addRow(_label("Time signature:"),   self._timesig)
        form.addRow(_sep())
        form.addRow(_label("Track 1:"),          self._track1)
        form.addRow(_label("Track 2:"),          self._track2)
        form.addRow(_label("Track 3:"),          self._track3)
        form.addRow(_label("Track 4:"),          self._track4)
        form.addRow(_sep())
        form.addRow(_label("Duration:"),         self._duration)
        form.addRow(_label("Last modified:"),    self._modified)
        form.addRow(_label("Comments:"),         self._comment)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setStyleSheet(
            "QPushButton { background:#2d2d2d; color:#d4d4d4; "
            "border:1px solid #555; border-radius:3px; padding:4px 14px; }"
            "QPushButton:hover { border-color:#007acc; }"
            "QPushButton:pressed { background:#1a1a1a; }"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 8)
        vbox.setSpacing(8)
        vbox.addLayout(form)
        vbox.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignCenter)

    # ── Public ────────────────────────────────────────────────────────────────

    def get_meta(self) -> dict:
        """Return the (possibly edited) metadata as a plain dict of strings."""
        out = dict(self._orig)
        out.update({
            'title':    self._title.text().strip(),
            'author':   self._author.text().strip(),
            'charter':  self._charter.text().strip(),
            'album':    self._album.text().strip(),
            'year':     self._year.text().strip(),
            'bpm':      self._bpm.text().strip(),
            'timesig':  self._timesig.currentText().strip(),
            'track1':   self._track1.text().strip(),
            'track2':   self._track2.text().strip(),
            'track3':   self._track3.text().strip(),
            'track4':   self._track4.text().strip(),
            'duration': self._duration.text().strip(),
            'modified': self._modified.text().strip(),
            'comment':  self._comment.toPlainText().strip(),
        })
        return out
