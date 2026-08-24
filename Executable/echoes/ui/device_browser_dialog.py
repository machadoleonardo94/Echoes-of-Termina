"""
Device media browser — lists the music albums and lightshow projects stored
on the connected Echoes hardware.

Layout concept (see core/device_media.py):

    /Echoes/Music/…          sub-folders = albums (playlists), e.g. Playlist_A
    /Echoes/Lightshows/…     one folder per project (audio + .echoes script)

The scan runs on a background thread; the dialog only renders results.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)

from core.device_media import DeviceMediaScanner, delete_path


# ── Scanner lifetime ──────────────────────────────────────────────────────────
# A scan may outlive its dialog (the dialog can be closed while the scan is
# still probing the device). Keep a strong reference so the QThread is never
# destroyed while running, and reap it on the main thread when it finishes.

_scanner_refs: list = []


class _ScannerReaper(QObject):
    """Lives in the main thread; safely deleteLater()-s finished scanners."""

    def reap(self, scanner=None) -> None:
        if scanner is None:
            scanner = self.sender()   # the emitting QThread (queued → works)
        if scanner in _scanner_refs:
            _scanner_refs.remove(scanner)
        scanner.deleteLater()


_reaper = _ScannerReaper()


class DeviceBrowserDialog(QDialog):
    load_project_requested = Signal(dict)   # project dict
    play_requested        = Signal(str)     # device audio path

    def __init__(self, host: str, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._scanner: Optional[DeviceMediaScanner] = None
        self._last_result: Optional[dict] = None

        self.setWindowTitle(f"Echoes Hardware — {host}")
        self.resize(720, 460)

        vbox = QVBoxLayout(self)
        vbox.setSpacing(8)

        # ── Header ──────────────────────────────────────────────────────────
        self._status = QLabel(f"Scanning {host} \u2026")
        self._status.setStyleSheet("color:#aaaaaa; font:12px 'Segoe UI';")
        vbox.addWidget(self._status)

        # ── Two lists: albums | projects ────────────────────────────────────
        lists = QHBoxLayout()
        lists.setSpacing(12)

        left = QVBoxLayout()
        left.addWidget(QLabel("Music \u2014 albums (playlists)"))
        self._albums = QListWidget()
        self._albums.itemSelectionChanged.connect(self._on_albums_selection)
        self._albums.itemDoubleClicked.connect(self._on_album_double_clicked)
        left.addWidget(self._albums)
        lists.addLayout(left, stretch=1)

        right = QVBoxLayout()
        right.addWidget(QLabel("Lightshow \u2014 projects  (double-click plays)"))
        self._projects = QListWidget()
        self._projects.itemSelectionChanged.connect(self._on_projects_selection)
        self._projects.itemDoubleClicked.connect(self._on_project_double_clicked)
        right.addWidget(self._projects)
        lists.addLayout(right, stretch=1)

        vbox.addLayout(lists, stretch=1)

        # ── Buttons ─────────────────────────────────────────────────────────
        btns = QHBoxLayout()

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)

        self._play_btn = QPushButton("Play on Device")
        self._play_btn.setEnabled(False)
        self._play_btn.setToolTip(
            "Start playback on the device — album: its first track;\n"
            "project: its audio + lightshow (double-click also plays)")
        self._play_btn.clicked.connect(self._play_selected)

        self._load_btn = QPushButton("Load Project")
        self._load_btn.setEnabled(False)
        self._load_btn.setToolTip("Load the selected project into the editor")
        self._load_btn.clicked.connect(self._load_selected)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setEnabled(False)
        self._delete_btn.setToolTip(
            "Delete the selected album or project from the device")
        self._delete_btn.clicked.connect(self._delete_selected)

        self._portal_btn = QPushButton("Open Portal")
        self._portal_btn.setToolTip("Open the device web portal in your browser")
        self._portal_btn.clicked.connect(self._open_portal)

        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.close)

        btns.addWidget(self._refresh_btn)
        btns.addStretch(1)
        btns.addWidget(self._play_btn)
        btns.addWidget(self._load_btn)
        btns.addWidget(self._delete_btn)
        btns.addWidget(self._portal_btn)
        btns.addWidget(self._close_btn)
        vbox.addLayout(btns)

        self.refresh()

    # ── Scanning ─────────────────────────────────────────────────────────────

    def set_host(self, host: str) -> None:
        self._host = host
        self.setWindowTitle(f"Echoes Hardware — {host}")

    def refresh(self) -> None:
        """(Re)scan the device. Safe to call while a scan is already running."""
        if self._scanner is not None:
            return
        self._set_scanning(True)
        scanner = DeviceMediaScanner(self._host)
        scanner.finished_ok.connect(self._on_scan_ok)
        scanner.failed.connect(self._on_scan_failed)
        scanner.finished.connect(self._on_scanner_done)
        scanner.finished.connect(_reaper.reap)   # survives dialog close
        self._scanner = scanner
        _scanner_refs.append(scanner)
        scanner.start()

    # ── Scan results ─────────────────────────────────────────────────────────

    def _on_scan_ok(self, result: dict) -> None:
        self._last_result = result
        self._fill_albums(result.get("albums", []))
        self._fill_projects(result.get("projects", []))

        n_albums   = len(result.get("albums", []))
        n_projects = len(result.get("projects", []))
        root_hint  = (f"  (Lightshow root: {result['lightshow_root']})"
                      if result.get("lightshow_root") else "")
        self._status.setText(
            f"{n_albums} album{'s' if n_albums != 1 else ''}  \u00b7  "
            f"{n_projects} project{'s' if n_projects != 1 else ''}{root_hint}")

    def _on_scan_failed(self, message: str) -> None:
        self._albums.clear()
        self._projects.clear()
        self._status.setStyleSheet(
            "color:#e5534b; font:12px 'Segoe UI';")
        self._status.setText(f"Scan failed \u2014 {message}")

    def _on_scanner_done(self) -> None:
        self._scanner = None
        self._set_scanning(False)

    def _set_scanning(self, scanning: bool) -> None:
        self._refresh_btn.setEnabled(not scanning)
        if scanning:
            self._status.setStyleSheet("color:#aaaaaa; font:12px 'Segoe UI';")
            self._status.setText(f"Scanning {self._host} \u2026")
        self._update_buttons()

    # ── List population ──────────────────────────────────────────────────────

    @staticmethod
    def _add_placeholder(widget: QListWidget, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)   # not selectable
        widget.addItem(item)

    def _fill_albums(self, albums: list) -> None:
        self._albums.clear()
        if not albums:
            self._add_placeholder(self._albums, "No albums found in /Echoes/Music")
            return
        for album in albums:
            n = len(album["audio"])
            item = QListWidgetItem(f"{album['name']}  \u2014  {n} track{'s' if n != 1 else ''}")
            item.setData(Qt.ItemDataRole.UserRole, album)
            item.setToolTip(f"{album['path']}\n" + "\n".join(album["audio"]))
            self._albums.addItem(item)

    def _fill_projects(self, projects: list) -> None:
        self._projects.clear()
        if not projects:
            self._add_placeholder(self._projects,
                                  "No projects found in /Echoes/Lightshows")
            return
        for project in projects:
            n  = len(project["audio"])
            ns = len(project["script"])
            item = QListWidgetItem(
                f"{project['name']}  \u2014  {n} audio \u00b7 {ns} script"
                f"{'s' if ns != 1 else ''}")
            item.setData(Qt.ItemDataRole.UserRole, project)
            item.setToolTip(f"{project['path']}\n\n"
                            + "\n".join(project["audio"]) + "\n"
                            + "\n".join(project["script"]))
            self._projects.addItem(item)

    # ── Selection / actions ──────────────────────────────────────────────────

    def _on_albums_selection(self) -> None:
        """Selecting an album deselects any project (and vice versa), so the
        Play action is never ambiguous."""
        if self._albums.selectedItems():
            self._projects.clearSelection()
        self._update_buttons()

    def _on_projects_selection(self) -> None:
        if self._projects.selectedItems():
            self._albums.clearSelection()
        self._update_buttons()

    def _selected(self, widget: QListWidget) -> Optional[dict]:
        items = widget.selectedItems()
        if not items:
            return None
        data = items[0].data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _update_buttons(self) -> None:
        scanning = self._scanner is not None
        has_selection = (self._selected(self._albums) is not None or
                         self._selected(self._projects) is not None)
        self._play_btn.setEnabled(not scanning and has_selection)
        self._delete_btn.setEnabled(not scanning and has_selection)
        self._load_btn.setEnabled(not scanning and
                                  self._selected(self._projects) is not None)

    def _play_selected(self) -> None:
        """Play on the device: album → its first track; project → its audio
        (the firmware attaches the project's .echoes lightshow automatically)."""
        album = self._selected(self._albums)
        if album and album["audio"]:
            # Device paths always use forward slashes
            self.play_requested.emit(album["path"] + "/" + album["audio"][0])
            return
        project = self._selected(self._projects)
        if project and project["audio"]:
            self.play_requested.emit(project["path"] + "/" + project["audio"][0])

    def _load_selected(self) -> None:
        project = self._selected(self._projects)
        if project:
            self.load_project_requested.emit(project)

    def _delete_selected(self) -> None:
        """Delete the selected album or project from the device (confirmed)."""
        target = self._selected(self._albums) or self._selected(self._projects)
        if not target:
            return
        name = target.get("name", "selection")
        path = target.get("path", "")

        answer = QMessageBox.question(
            self, "Delete from device",
            f"Delete '{name}' from the device?\n\n{path}\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._delete_btn.setEnabled(False)
        self._status.setText(f"Deleting {name} \u2026")
        ok, msg = delete_path(self._host, path)
        if ok:
            self._status.setText(f"Deleted {name}")
            self.refresh()   # rescan the device
        else:
            self._status.setText("Delete failed")
            QMessageBox.warning(self, "Delete failed", msg)
        self._delete_btn.setEnabled(True)

    def _on_album_double_clicked(self, item: QListWidgetItem) -> None:
        self._albums.setCurrentItem(item)
        self._play_selected()

    def _on_project_double_clicked(self, item: QListWidgetItem) -> None:
        self._projects.setCurrentItem(item)
        self._play_selected()   # double-click plays (Light button loads into editor)

    def _open_portal(self) -> None:
        QDesktopServices.openUrl(QUrl(f"http://{self._host}/"))

    def closeEvent(self, event) -> None:
        if self._scanner is not None:
            self._scanner.requestInterruption()
            self._scanner = None
        super().closeEvent(event)
