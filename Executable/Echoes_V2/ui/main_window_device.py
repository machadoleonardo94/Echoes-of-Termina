"""
Echoes — MainWindow device pairing / transport / upload logic (mixin).

Split out of main_window.py to isolate all Echoes-hardware interaction:
discovery pairing, the device transport toolbar (play/pause/progress), and
uploading a project's audio + script to the device's SD card.

Assumes it is mixed into MainWindow, which provides the toolbar widgets
(``self._device_dot``, ``self._device_btn`` …), audio/script state
(``self._audio``, ``self._script_path``, ``self._meta``) and
``self._apply_script_text`` / ``self._set_track_display``.
"""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore    import QObject, Qt
from PySide6.QtWidgets import (
    QLabel, QMessageBox, QVBoxLayout,
)

from core.device_discovery import DEFAULT_HOSTNAME, probe_device
from core.device_media import (
    DeviceStatusPoller, ProjectUploadWorker, device_url, ensure_dir, fetch_dir,
    fetch_text, pause_device, play_on_device,
)

from ui.device_browser_dialog import DeviceBrowserDialog

# ── Upload worker lifetime ────────────────────────────────────────────────────
# A project upload may outlive the window (Wi-Fi transfer). Keep a strong
# reference so the QThread is never destroyed while running, and reap it on
# the main thread once it finishes.

_upload_workers: list = []


class _UploadReaper(QObject):
    """Lives in the main thread; safely deleteLater()-s finished uploaders."""

    def reap(self, worker=None) -> None:
        worker = worker if worker is not None else self.sender()
        if worker in _upload_workers:
            _upload_workers.remove(worker)
        worker.deleteLater()


_upload_reaper = _UploadReaper()


class DeviceControllerMixin:
    """Device discovery / pairing / transport / upload behaviour for MainWindow."""

    # ── Device discovery / pairing ────────────────────────────────────────────

    def _on_device_state(self, info: Optional[dict]) -> None:
        """Monitor tick: *info* is the discovery result, or None when offline."""
        self._device_online = info is not None
        self._device_info   = info
        self._update_device_ui()

    def _toggle_device_pair(self) -> None:
        """Connect ⇄ Disconnect button — pair with / unpair from the device."""
        if self._device_paired:
            self._device_paired = False
            self.statusBar().showMessage("Device disconnected")
        else:
            if self._device_info is None:
                QMessageBox.warning(
                    self, "Device not found",
                    f"No Echoes hardware found on the network ({DEFAULT_HOSTNAME}).\n"
                    "Make sure the device is powered on and on the same Wi-Fi network.",
                )
                return
            # Live re-verify so a stale monitor result can't fake a pairing
            host = self._device_info.get("host") or self._device_info.get("ip")
            if probe_device(host) is None:
                QMessageBox.warning(
                    self, "Device offline",
                    f"{DEFAULT_HOSTNAME} stopped responding.\n"
                    "Check the device power and network, then try again.",
                )
                return
            self._device_paired = True
            self.statusBar().showMessage(
                f"Paired with Echoes hardware at "
                f"{self._device_info.get('ip', '')}  \u2014  "
                f"SD: {'yes' if self._device_info['status'].get('sd') else 'no'}")
        self._update_device_ui()
        if self._device_paired:
            self._open_device_browser()

    def _device_host(self) -> str:
        """Best host string for device HTTP calls (IP preferred over mDNS)."""
        if self._device_info is not None:
            return (self._device_info.get("ip")
                    or self._device_info.get("host") or DEFAULT_HOSTNAME)
        return DEFAULT_HOSTNAME

    def _open_device_browser(self) -> None:
        """Open (or refresh) the device media browser dialog."""
        if not self._device_paired:
            QMessageBox.information(
                self, "Not connected",
                "Pair with the device first (Connect button), then browse its media.")
            return
        if self._device_browser is None:
            self._device_browser = DeviceBrowserDialog(self._device_host(), self)
            self._device_browser.load_project_requested.connect(
                self._device_load_project)
            self._device_browser.play_requested.connect(self._device_play_path)
            self._device_browser.destroyed.connect(
                lambda: setattr(self, "_device_browser", None))
        else:
            self._device_browser.set_host(self._device_host())
            self._device_browser.refresh()
        self._device_browser.show()
        self._device_browser.raise_()
        self._device_browser.activateWindow()

    def _device_play_path(self, file_path: str) -> None:
        """Play a stored audio file on the device (browser Play action). A
        Lightshows project also starts its synced .echoes lightshow."""
        ok, msg = play_on_device(self._device_host(), file_path)
        if ok:
            if "/Echoes/Lightshows/" in file_path.replace("\\", "/"):
                msg = f"{msg}  \u2014  lightshow attached"
            self.statusBar().showMessage(f"Device play: {msg}")
        else:
            self.statusBar().showMessage(f"Device play failed: {msg}")
        if ok:
            self._set_track_display(
                f"  \u25b6  {os.path.basename(file_path)}  (device)")

    def _device_load_project(self, project: dict) -> None:
        """Load a stored project (echoes script + audio) from the device."""
        host = self._device_host()
        # Device paths always use forward slashes
        script_name = project["script"][0]
        script_path = project["path"] + "/" + script_name
        text = fetch_text(host, script_path)
        if text is None:
            QMessageBox.warning(
                self, "Load failed",
                f"Could not download {script_path} from the device.")
            return
        self._apply_script_text(text, title=f"{project['name']}  (device)")
        self.statusBar().showMessage(
            f"Loaded project \u201c{project['name']}\u201d from device \u2014 "
            f"audio: {project['audio'][0]}")

        audio_path = project["path"] + "/" + project["audio"][0]
        self._audio.load_url(device_url(host, audio_path))
        self._set_track_display(
            f"  \u266b  {project['audio'][0]}  \u2014  {host}")

    def _update_device_ui(self) -> None:
        """Refresh the dot, label and Connect button from current state."""
        info   = self._device_info
        online = self._device_online
        paired = self._device_paired

        if info is not None:
            st = info.get("status") or {}
            via = {"mDNS": "mDNS (echoes.local)",
                   "ip-cache": "cached IP",
                   "subnet": "subnet scan"}.get(info.get("via"), info.get("via", ""))
            heap = st.get('heap')
            heap_txt = f"{heap // 1024} KB" if isinstance(heap, (int, float)) else '—'
            self._device_label.setText(f"{DEFAULT_HOSTNAME}  \u2022  {info.get('ip', '')}")
            self._device_label.setToolTip(
                f"Found via {via}\n"
                f"SD card: {'present' if st.get('sd') else 'missing'}\n"
                f"Playing: {'yes' if st.get('playing') else 'no'}\n"
                f"Lightshow: {'attached' if st.get('show') else 'off'}\n"
                f"Free heap: {heap_txt}\n"
                f"Track:   {st.get('track') or '—'}\n"
                f"Volume:  {st.get('volume', '—')}")
        else:
            self._device_label.setText(f"{DEFAULT_HOSTNAME} \u2014 not found")
            self._device_label.setToolTip(
                "Device not reachable on the network\n"
                "(powers on, same Wi-Fi network, mDNS/subnet scan active)")

        if paired:
            self._device_btn.setText("Disconnect")
            self._device_btn.setEnabled(True)
            color   = "#4caf50" if online else "#d33"
            dot_tip = "Paired \u2014 device online" if online else "Paired \u2014 device offline"
        else:
            self._device_btn.setText("Connect")
            self._device_btn.setEnabled(online)
            color   = "#d33"
            dot_tip = ("Device found \u2014 click Connect to pair"
                       if online else "Device not found")

        self._device_dot.setStyleSheet(f"background:{color}; border-radius:6px;")
        self._device_dot.setToolTip(dot_tip)
        self._device_media_btn.setEnabled(paired)

        # ── Device transport toolbar + status poller ─────────────────────────
        self._device_toolbar.setVisible(paired)
        if paired and self._status_poller is None:
            self._status_poller = DeviceStatusPoller(
                self._device_host(), parent=self)
            self._status_poller.status_changed.connect(self._on_device_status)
            self._status_poller.start()
        elif not paired and self._status_poller is not None:
            self._stop_status_poller()
        self._update_device_buttons()

    # ── Device transport control ──────────────────────────────────────────────

    def _stop_status_poller(self) -> None:
        poller = self._status_poller
        self._status_poller = None
        self._device_status = None
        if poller is not None:
            poller.stop()
            poller.wait(4000)          # probe timeout is 2 s — give it room
            poller.deleteLater()

    def _on_device_status(self, status: Optional[dict]) -> None:
        """1s poll tick from the device — refresh transport controls."""
        self._device_status = status
        self._update_device_buttons()

    def _update_device_buttons(self) -> None:
        st = self._device_status or {}
        playing = bool(st.get("playing"))
        track   = (st.get("track") or "").strip()
        dur = int(st.get("dur") or 0)
        pos = int(st.get("pos") or 0)

        self._device_pause_btn.setEnabled(playing)
        self._device_play_btn.setEnabled(
            not playing and
            (bool(track) or self._device_project_audio_path() is not None))
        if dur > 0:
            self._device_progress.setRange(0, dur)
            self._device_progress.setValue(max(0, min(pos, dur)))
        else:
            self._device_progress.setRange(0, 0)
            self._device_progress.setValue(0)

    def _device_project_audio_path(self) -> Optional[str]:
        """Device path of this project's audio (Lightshows/<proj>/<audio>), if known."""
        if not self._script_path:
            return None
        audio = self._meta.get('audio', '').strip()
        if not audio:
            src = self._audio.source_path
            if src and os.path.isfile(src):
                audio = os.path.basename(src)
            else:
                return None
        folder = self._device_project_folder
        if not folder:
            stem = os.path.splitext(os.path.basename(self._script_path))[0]
            folder = f"/Echoes/Lightshows/{stem}"
        return f"{folder}/{audio}"

    def _device_play(self) -> None:
        """Resume the current track, or start the project's audio on the device."""
        host = self._device_host()
        st = self._device_status or {}
        if st.get("playing"):
            return
        track = (st.get("track") or "").strip()
        if track:
            ok, msg = pause_device(host, "resume")
            if not ok:
                self.statusBar().showMessage(f"Device resume failed: {msg}")
                return
            # resume may not restart a finished song — fall back to replaying it
            st2 = probe_device(host)
            if st2 and not st2.get("playing") and (st2.get("track") or "").strip():
                play_on_device(host, st2["track"])
                self.statusBar().showMessage(f"Playing: {st2['track']}")
            else:
                self.statusBar().showMessage("Device playback resumed")
            return
        path = self._device_project_audio_path()
        if path:
            ok, msg = play_on_device(host, path)
            self.statusBar().showMessage(
                f"Device play: {msg}" if ok else f"Device play failed: {msg}")
            return
        self.statusBar().showMessage(
            "No track on the device \u2014 upload a project first")

    def _device_pause(self) -> None:
        ok, msg = pause_device(self._device_host(), "pause")
        self.statusBar().showMessage(
            "Device paused" if ok else f"Device pause failed: {msg}")

    # ── Upload project to the device ─────────────────────────────────────────

    def _ask_upload_folder_name(self, host: str, default: str) -> Optional[str]:
        """Ask for the device folder name, autocompleted from existing folders."""
        names: list = []
        data = fetch_dir(host, "/Echoes/Lightshows")
        if data:
            names = sorted(e["name"] for e in data.get("entries", [])
                           if e.get("isDir"))

        from PySide6.QtWidgets import (
            QCompleter, QDialog, QDialogButtonBox, QLineEdit,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Upload Project to Device")
        dlg.setMinimumWidth(420)
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(8)
        vbox.addWidget(QLabel(
            "Target folder on the device  (/Echoes/Lightshows/<name>):"))
        edit = QLineEdit(default)
        if names:
            edit.setCompleter(QCompleter(names, dlg))
            edit.setToolTip("Autocomplete from folders already on the device")
        vbox.addWidget(edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        vbox.addWidget(buttons)

        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            name = edit.text().strip()
            if not name:
                QMessageBox.warning(self, "Missing name", "Enter a folder name.")
                continue
            if "/" in name or "\\" in name or name in (".", ".."):
                QMessageBox.warning(
                    self, "Invalid name",
                    "The folder name must not contain '/' or '\\'.")
                continue
            return name

    def _device_upload_project(self) -> None:
        """
        Upload this project (.mp3 + .echoes) to /Echoes/Lightshows/<name>,
        asking for the folder name (autocompleted) and overwrite permission.
        """
        try:
            self._device_upload_project_impl()
        except Exception as exc:   # noqa: BLE001 — never fail silently
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self, "Upload error",
                f"Unexpected error while preparing the upload:\n\n{exc}")

    def _device_upload_project_impl(self) -> None:
        if not self._device_paired:
            QMessageBox.information(
                self, "Not connected", "Pair with the device first.")
            return
        if self._upload_worker is not None:
            return
        if not self._script_path:
            QMessageBox.information(
                self, "Save first",
                "Save the project before uploading it to the device.")
            return
        audio = self._audio.source_path
        if not audio or not os.path.isfile(audio):
            QMessageBox.warning(
                self, "No audio file",
                "The project has no local audio file to upload.\n\n"
                "Use \u201cConvert to .MP3\u201d, Open Audio or "
                "Load Audio Tracks first.")
            return

        host = self._device_host()

        # Default folder name: script stem (or its parent folder for generic
        # names like script.echoes)
        default = os.path.splitext(os.path.basename(self._script_path))[0]
        parent  = os.path.basename(
            os.path.dirname(os.path.abspath(self._script_path)))
        if default.lower() in ("script", "show", "main") and parent \
                and parent.lower() not in ("projects", "echoes", "tools"):
            default = parent

        name = self._ask_upload_folder_name(host, default)
        if not name:
            return
        dest = f"/Echoes/Lightshows/{name}"

        # Overwrite confirmation when the folder already exists
        if fetch_dir(host, dest) is not None:
            answer = QMessageBox.question(
                self, "Folder exists",
                f"'{name}' already exists on the device.\n"
                "Overwrite its contents?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        ok, msg = ensure_dir(host, dest)
        if not ok:
            QMessageBox.warning(self, "Cannot create folder", msg)
            return

        # Fast reachability check so a dead device fails immediately instead
        # of hanging the upload for minutes.
        if probe_device(host) is None:
            QMessageBox.warning(
                self, "Device not reachable",
                f"{host} stopped responding.\n\n"
                "Check the device power and that it is still on the same "
                "Wi-Fi network, then try again.")
            return

        self._device_project_folder = dest
        self._device_upload_btn.setEnabled(False)
        worker = ProjectUploadWorker(host, dest, [audio, self._script_path])
        worker.progress.connect(self._on_upload_progress)
        worker.succeeded.connect(self._on_upload_succeeded)
        worker.failed.connect(self._on_upload_failed)
        worker.finished.connect(self._on_upload_finished)
        worker.finished.connect(_upload_reaper.reap)
        self._upload_worker = worker
        _upload_workers.append(worker)

        # ── Loading screen ────────────────────────────────────────────────────
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Preparing upload \u2026", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Uploading to Device")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.canceled.connect(worker.cancel)
        dlg.show()
        self._upload_dialog = dlg

        worker.start()

    def _on_upload_progress(self, message: str) -> None:
        self.statusBar().showMessage(message)
        if self._upload_dialog is not None:
            self._upload_dialog.setLabelText(message)

    def _on_upload_succeeded(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self._close_upload_dialog()
        QMessageBox.information(self, "Upload complete", message)
        if self._device_browser is not None and self._device_browser.isVisible():
            self._device_browser.refresh()

    def _on_upload_failed(self, message: str) -> None:
        self.statusBar().showMessage("Upload failed")
        self._close_upload_dialog()
        QMessageBox.warning(self, "Upload failed", message)

    def _on_upload_finished(self) -> None:
        self._upload_worker = None
        self._device_upload_btn.setEnabled(True)
        self._close_upload_dialog()

    def _close_upload_dialog(self) -> None:
        dlg = self._upload_dialog
        self._upload_dialog = None
        if dlg is not None:
            dlg.close()
            dlg.deleteLater()
