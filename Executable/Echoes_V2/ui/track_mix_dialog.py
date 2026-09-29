"""
Track mix dialog — choose which found audio tracks go into a single .MP3.

Shown by the "Convert to .MP3" action when audio files are found in the
project folder (e.g. guitar/bass/drums/vocals stems). The user ticks the
tracks to process and picks the output file name.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QVBoxLayout,
)

_MAX_TRACKS = 6


class TrackMixDialog(QDialog):
    """Modal dialog: track checklist + output file name for the mix."""

    def __init__(self, tracks: list, default_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Convert Tracks to .MP3")
        self.setMinimumWidth(460)

        self._checks: list = []   # [(QCheckBox, path), …]

        vbox = QVBoxLayout(self)
        vbox.setSpacing(8)

        info = (f"Found {len(tracks)} audio track{'s' if len(tracks) != 1 else ''} "
                "in the project folder. Tick the ones to process:")
        vbox.addWidget(QLabel(info))

        for path in tracks[: _MAX_TRACKS]:
            cb = QCheckBox(os.path.basename(path))
            cb.setChecked(True)
            cb.setToolTip(path)
            self._checks.append((cb, path))
            vbox.addWidget(cb)

        form = QFormLayout()
        form.setSpacing(6)
        self._name = QLineEdit(default_name)
        self._name.selectAll()
        self._name.setToolTip("Output .mp3 file name (written into the project folder)")
        form.addRow("Output file name:", self._name)

        self._normalize = QCheckBox("Normalize volume (prevent clipping)")
        self._normalize.setChecked(True)
        self._normalize.setToolTip(
            "Divide each track by the number of inputs so the mix does not clip")
        form.addRow("", self._normalize)
        vbox.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

    # ── Validation / results ─────────────────────────────────────────────────

    def _on_ok(self) -> None:
        if not self.selected_tracks():
            QMessageBox.warning(
                self, "No tracks selected",
                "Select at least one track to convert.")
            return
        if not self._name.text().strip():
            QMessageBox.warning(
                self, "Missing name",
                "Enter an output file name.")
            return
        self.accept()

    def selected_tracks(self) -> list:
        """Paths of the checked tracks, in their original order."""
        return [path for cb, path in self._checks if cb.isChecked()]

    def output_filename(self) -> str:
        """The output name, guaranteed to end in .mp3."""
        name = self._name.text().strip()
        if name and not name.lower().endswith(".mp3"):
            name += ".mp3"
        return name

    def normalize(self) -> bool:
        return self._normalize.isChecked()
