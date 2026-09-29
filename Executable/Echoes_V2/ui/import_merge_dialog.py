"""
Import-into-project merge dialog.

Shows a fixed grid of MAX_TRACKS slot rows.  For each slot the user picks
which incoming track (if any) should fill it.  The same source track can be
assigned to multiple slots, so a user can map the same chart data into
different hardware-preset slots without re-importing.
"""
from __future__ import annotations

from PySide6.QtCore    import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFrame, QGridLayout, QLabel, QVBoxLayout,
)

from ui.script_editor import MAX_TRACKS

_SKIP_TEXT      = "— skip —"
_UNCHANGED_TEXT = "— unchanged —"


class ImportMergeDialog(QDialog):
    """
    Parameters
    ----------
    incoming  : ordered list of source track names from the chart file
    existing  : ordered list of current project slot names (may be empty)
    """

    def __init__(
        self,
        incoming: list[str],
        existing: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import into project")
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        # One combo per slot row (index 0 – MAX_TRACKS-1)
        self._combos: list[QComboBox] = []
        self._existing = list(existing)

        _combo_style = (
            "QComboBox { background:#3c3c3c; color:#cccccc; "
            "border:1px solid #555555; border-radius:2px; "
            "padding-left:4px; font:11px 'Segoe UI'; min-height:22px; }"
            "QComboBox::drop-down { border:0; width:16px; }"
            "QComboBox QAbstractItemView { background:#3c3c3c; color:#cccccc; "
            "selection-background-color:#0e639c; }"
        )

        vbox = QVBoxLayout(self)
        vbox.setSpacing(10)

        # ── Metadata checkbox ──────────────────────────────────────────────────
        self._meta_chk = QCheckBox(
            "Import song metadata  (title, BPM, time signature, album, year…)")
        self._meta_chk.setChecked(True)
        self._meta_chk.setStyleSheet("color:#d4d4d4; font:11px 'Segoe UI';")
        vbox.addWidget(self._meta_chk)

        vbox.addWidget(_hline())

        # ── Slot grid ─────────────────────────────────────────────────────
        hdr = QLabel("Slot assignment")
        hdr.setStyleSheet("color:#cccccc; font:bold 11px 'Segoe UI';")
        vbox.addWidget(hdr)

        sub = QLabel(
            "Choose which source track fills each slot.  "
            "The same track can be assigned to multiple slots.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#777777; font:10px 'Segoe UI';")
        vbox.addWidget(sub)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnMinimumWidth(0, 60)   # "Slot N"
        grid.setColumnStretch(1, 1)         # slot name
        grid.setColumnStretch(2, 2)         # source dropdown

        for i in range(MAX_TRACKS):
            slot_exists = i < len(existing)
            slot_name   = existing[i] if slot_exists else ''

            # Col 0: slot number label
            num_lbl = QLabel(f"Slot {i + 1}")
            num_lbl.setStyleSheet(
                "color:#888888; font:10px 'Segoe UI'; "
                "padding-right:4px;")
            grid.addWidget(num_lbl, i, 0)

            # Col 1: current slot name (or empty indicator)
            if slot_exists:
                name_lbl = QLabel(slot_name)
                name_lbl.setStyleSheet(
                    "color:#cccccc; font:11px 'Segoe UI';")
            else:
                name_lbl = QLabel("—")
                name_lbl.setStyleSheet(
                    "color:#444444; font:11px 'Segoe UI';")
            grid.addWidget(name_lbl, i, 1)

            # Col 2: source track dropdown
            combo = QComboBox()
            combo.setStyleSheet(_combo_style)

            null_label = _UNCHANGED_TEXT if slot_exists else _SKIP_TEXT
            combo.addItem(null_label, None)    # None → skip / unchanged
            for track_name in incoming:
                combo.addItem(track_name, track_name)

            # Pre-fill: assign incoming[i] to slot i
            if i < len(incoming):
                combo.setCurrentIndex(i + 1)   # +1 because index 0 is skip/unchanged

            grid.addWidget(combo, i, 2)
            self._combos.append(combo)

        vbox.addLayout(grid)

        note = QLabel(
            "Slots set to '— unchanged —' keep their current content.  "
            "New slots left on '— skip —' are not created.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#555555; font:10px 'Segoe UI';")
        vbox.addWidget(note)

        vbox.addWidget(_hline())

        # ── Buttons ─────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Ok,
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        vbox.addWidget(btns)

    # ── Public API ──────────────────────────────────────────────────

    def wants_metadata(self) -> bool:
        """True if the user wants to overwrite the project’s song metadata."""
        return self._meta_chk.isChecked()

    def get_slot_assignments(self) -> list[tuple[str, str | None]]:
        """
        Return a list of ``(slot_name, source_track_or_None)`` for every slot row.

        ``slot_name``  — existing name for occupied slots; empty string for new ones.
        ``source``     — ``None`` means skip/unchanged; a string is the name of the
                         incoming track to write into this slot.
        """
        result: list[tuple[str, str | None]] = []
        for i, combo in enumerate(self._combos):
            slot_name = self._existing[i] if i < len(self._existing) else ''
            source    = combo.currentData()   # None or track name str
            result.append((slot_name, source))
        return result


# ── Helper ─────────────────────────────────────────────────────────────────

def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#333333; margin:0px;")
    return f

    """
    Parameters
    ----------
    incoming  : track names being imported (from the source chart file)
    existing  : ordered list of current project slot names
    """

    def __init__(
        self,
        incoming: list[str],
        existing: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import into project")
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self._assignments: dict[str, QComboBox] = {}

        _combo_style = (
            "QComboBox { background:#3c3c3c; color:#cccccc; "
            "border:1px solid #555555; border-radius:2px; "
            "padding-left:4px; font:11px 'Segoe UI'; min-height:22px; }"
            "QComboBox::drop-down { border:0; width:16px; }"
            "QComboBox QAbstractItemView { background:#3c3c3c; color:#cccccc; "
            "selection-background-color:#0e639c; }"
        )

        vbox = QVBoxLayout(self)
        vbox.setSpacing(10)

        # ── Metadata checkbox ─────────────────────────────────────────────────
        self._meta_chk = QCheckBox(
            "Import song metadata  (title, BPM, time signature, album, year…)")
        self._meta_chk.setChecked(True)
        self._meta_chk.setStyleSheet("color:#d4d4d4; font:11px 'Segoe UI';")
        vbox.addWidget(self._meta_chk)

        vbox.addWidget(_hline())

        # ── Track assignment ──────────────────────────────────────────────────
        hdr = QLabel("Track assignment")
        hdr.setStyleSheet("color:#cccccc; font:bold 11px 'Segoe UI';")
        vbox.addWidget(hdr)

        sub = QLabel("Choose which project slot each imported track is written to.")
        sub.setStyleSheet("color:#777777; font:10px 'Segoe UI';")
        vbox.addWidget(sub)

        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 2)

        free_slots = MAX_TRACKS - len(existing)

        for row, name in enumerate(incoming):
            src = QLabel(name)
            src.setStyleSheet("color:#4ec9b0; font:11px 'Segoe UI Semibold';")
            grid.addWidget(src, row, 0)

            arr = QLabel("→")
            arr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arr.setStyleSheet("color:#555555; font:11px 'Segoe UI';")
            grid.addWidget(arr, row, 1)

            combo = QComboBox()
            combo.setStyleSheet(_combo_style)
            combo.addItem("— New slot —", None)
            for slot in existing:
                combo.addItem(f"Overwrite: {slot}", slot)

            # Pre-select overwrite when no room exists for a new slot
            if free_slots <= row and existing:
                combo.setCurrentIndex(1)

            grid.addWidget(combo, row, 2)
            self._assignments[name] = combo

        vbox.addLayout(grid)

        if free_slots < len(incoming):
            warn = QLabel(
                f"⚠  The project has {len(existing)} / {MAX_TRACKS} slots.  "
                "Tracks set to 'New slot' that exceed the limit will be skipped."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "color:#ce9178; font:10px 'Segoe UI'; padding-top:4px;")
            vbox.addWidget(warn)

        note = QLabel("'Overwrite' replaces the slot's current contents.")
        note.setStyleSheet("color:#555555; font:10px 'Segoe UI';")
        vbox.addWidget(note)

        vbox.addWidget(_hline())

        # ── Buttons ───────────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Ok,
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        vbox.addWidget(btns)

    # ── Public API ────────────────────────────────────────────────────────────

    def wants_metadata(self) -> bool:
        """True if the user wants to overwrite the project's song metadata."""
        return self._meta_chk.isChecked()

    def get_assignments(self) -> dict[str, str | None]:
        """
        Return ``{incoming_name: target_slot_or_None}``.

        ``None`` means "New slot" (append under the incoming name).
        A string is the name of an existing slot to overwrite.
        """
        return {name: combo.currentData()
                for name, combo in self._assignments.items()}


# ── Helper ────────────────────────────────────────────────────────────────────

def _hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color:#333333; margin:0px;")
    return f
