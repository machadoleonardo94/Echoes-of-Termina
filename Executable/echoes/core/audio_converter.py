"""
Audio → MP3 conversion for the Echoes editor.

Transcodes the project audio into .mp3 so the firmware's SD card player
(ESP32-audioI2S, which has no OPUS/OGG-Vorbis decoder) can play it. This
mirrors the reference CLI helper in
Firmware/echoes_of_termina/tools/opus_to_mp3.py, but writes the .mp3 next to
the source file and reports progress back to the GUI.

Projects may hold several audio stems (e.g. guitar/bass/drums/vocals …); these
can be mixed into a single .MP3 with mix_to_mp3 (ffmpeg amix) and discovered in
a folder with find_audio_files.

Requirements:
  - Python 3
  - ffmpeg on your PATH  (https://ffmpeg.org/download.html)
    Only needed when the source is not already an .mp3 (MP3 sources are copied).
"""
from __future__ import annotations

import os
import shutil
import subprocess

from PySide6.QtCore import QThread, Signal

# MP3 bitrate (kbps). 128k is a good size/quality balance when re-encoding an
# already-lossy Opus source (higher just grows the file without recovering quality).
MP3_BITRATE = "128k"

# Extensions the editor can open; all of them can be transcoded to MP3.
SOURCE_EXTS = {".mp3", ".wav", ".ogg", ".opus", ".flac", ".aac", ".m4a"}


def ffmpeg_available() -> bool:
    """True if the ffmpeg executable is discoverable on PATH."""
    return shutil.which("ffmpeg") is not None


def find_audio_files(folder: str, limit: int = 6, exclude=()) -> list:
    """
    Return up to *limit* audio files in *folder* (sorted by name), skipping
    files whose basename is in *exclude* (e.g. the project's combined audio).
    """
    excluded = {os.path.normcase(os.path.basename(e)) for e in exclude if e}
    found: list = []
    if not folder or not os.path.isdir(folder):
        return found
    for name in sorted(os.listdir(folder)):
        if len(found) >= limit:
            break
        if os.path.normcase(name) in excluded:
            continue
        if os.path.splitext(name)[1].lower() in SOURCE_EXTS:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                found.append(path)
    return found


def convert_to_mp3(source: str, destination: str, bitrate: str = MP3_BITRATE):
    """
    Convert *source* to an MP3 at *destination* (blocking).

    Returns (ok: bool, message: str). Already-MP3 sources are copied rather
    than re-encoded; ffmpeg is only required for the other formats.
    """
    source = os.path.abspath(source)
    destination = os.path.abspath(destination)

    if not os.path.isfile(source):
        return False, f"Not a file: {source}"

    ext = os.path.splitext(source)[1].lower()
    if ext not in SOURCE_EXTS:
        return False, f"Unsupported audio format: {ext or '(none)'}"

    # Same file already in place — nothing to do
    if os.path.normcase(source) == os.path.normcase(destination):
        return True, "Already an MP3 at the destination — nothing to do."

    os.makedirs(os.path.dirname(destination), exist_ok=True)

    # Plain copy — no ffmpeg needed
    if ext == ".mp3":
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            return False, f"Copy failed: {exc}"
        return True, f"Copied MP3 to {destination}"

    if not ffmpeg_available():
        return False, (
            "ffmpeg was not found on your PATH.\n\n"
            "1. Download it from https://ffmpeg.org/download.html\n"
            "2. Extract the archive and add its 'bin' folder to your PATH\n"
            "3. Restart Echoes and try again."
        )

    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", source, "-codec:a", "libmp3lame", "-b:a", bitrate, destination],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return False, f"ffmpeg failed:\n{exc}"
    return True, f"Converted to {destination}"


def mix_to_mp3(sources, destination: str, bitrate: str = MP3_BITRATE,
               normalize: bool = True):
    """
    Mix *sources* into a single MP3 at *destination* (blocking).

    A single source is transcoded (no mixing). With several sources the files
    are summed with ffmpeg's amix filter; *normalize* divides each input by the
    number of tracks to prevent clipping. Returns (ok, message).
    """
    sources = [os.path.abspath(s) for s in sources if os.path.isfile(s)]
    destination = os.path.abspath(destination)
    if not sources:
        return False, "No audio files to mix."
    if len(sources) == 1:
        return convert_to_mp3(sources[0], destination, bitrate)
    if not ffmpeg_available():
        return False, (
            "ffmpeg was not found on your PATH.\n\n"
            "1. Download it from https://ffmpeg.org/download.html\n"
            "2. Extract the archive and add its 'bin' folder to your PATH\n"
            "3. Restart Echoes and try again."
        )
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    n = len(sources)
    inputs: list = []
    for s in sources:
        inputs += ["-i", s]
    labels = "".join(f"[{i}:a]" for i in range(n))
    norm = 1 if normalize else 0
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           *inputs,
           "-filter_complex", f"{labels}amix=inputs={n}:normalize={norm}[a]",
           "-map", "[a]", "-codec:a", "libmp3lame", "-b:a", bitrate,
           destination]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        return False, f"ffmpeg failed:\n{exc}"
    return True, f"Mixed {n} tracks into {destination}"


class Mp3ConvertWorker(QThread):
    """
    Runs an audio → MP3 conversion off the GUI thread.

    With a single source the file is transcoded (or copied if already MP3);
    with several sources they are mixed into one MP3 (see mix_to_mp3).
    Emits `succeeded(destination_path)` or `failed(error_message)` when done.
    """

    succeeded = Signal(str)   # destination path
    failed    = Signal(str)   # human-readable error message

    def __init__(self, source: str, destination: str, parent=None,
                 extra_sources=(), normalize: bool = True) -> None:
        super().__init__(parent)
        self._sources = [source, *extra_sources]
        self._destination = destination
        self._normalize = normalize

    def run(self) -> None:
        if len(self._sources) == 1:
            ok, message = convert_to_mp3(self._sources[0], self._destination)
        else:
            ok, message = mix_to_mp3(self._sources, self._destination,
                                     normalize=self._normalize)
        (self.succeeded if ok else self.failed).emit(
            self._destination if ok else message)
