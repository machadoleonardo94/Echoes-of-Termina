"""
Thin wrapper around QMediaPlayer + QAudioOutput that exposes a simplified
signal-based API for the Echoes transport layer.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class AudioPlayer(QObject):
    position_changed = Signal(int)   # current position in milliseconds
    duration_changed = Signal(int)   # total duration in milliseconds
    playing_changed  = Signal(bool)  # True = playing, False = paused/stopped
    error_occurred   = Signal(str)   # human-readable error string

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)
        self._output.setVolume(0.8)

        # qlonglong → Signal(int) requires an explicit intermediary in PySide6 6.x
        self._player.positionChanged.connect(
            lambda ms: self.position_changed.emit(int(ms)))
        self._player.durationChanged.connect(
            lambda ms: self.duration_changed.emit(int(ms)))
        self._player.playbackStateChanged.connect(self._relay_state)
        self._player.errorOccurred.connect(
            lambda _code, msg: self.error_occurred.emit(str(msg))
        )

    # ── Playback control ──────────────────────────────────────────────────────

    def load(self, path: str) -> None:
        self._player.setSource(QUrl.fromLocalFile(path))

    def play(self)  -> None: self._player.play()
    def pause(self) -> None: self._player.pause()
    def stop(self)  -> None: self._player.stop()

    def seek(self, position_ms: int) -> None:
        self._player.setPosition(position_ms)

    def set_volume(self, v: float) -> None:
        """Set volume; *v* must be in [0.0, 1.0]."""
        self._output.setVolume(max(0.0, min(1.0, v)))

    # ── State properties ──────────────────────────────────────────────────────

    @property
    def position(self) -> int:
        return self._player.position()

    @property
    def duration(self) -> int:
        return self._player.duration()

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def has_source(self) -> bool:
        return self._player.source().isValid()

    # ── Private ───────────────────────────────────────────────────────────────

    def _relay_state(self, state: QMediaPlayer.PlaybackState) -> None:
        self.playing_changed.emit(state == QMediaPlayer.PlaybackState.PlayingState)
