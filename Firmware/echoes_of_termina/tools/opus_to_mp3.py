#!/usr/bin/env python3
"""
opus_to_mp3.py - Convert .opus / .ogg audio files to .mp3 using ffmpeg.

Drag one or more .opus (or .ogg) files onto `opus_to_mp3.bat` (or this script).
The resulting .mp3 files are written into this same `tools/` folder, ready to be
uploaded to the SD card over FTP or the web UI.

Requirements:
  - Python 3
  - ffmpeg on your PATH  (https://ffmpeg.org/download.html)

Why .mp3: the ESP32-audioI2S decoder has no OPUS/OGG-Vorbis decoder, so .opus
files must be transcoded first. MP3 keeps file sizes small for the SD card.
"""

import os
import subprocess
import sys

# The directory containing this script (the project's tools/ folder).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Opus and Ogg-Vorbis both need transcoding.
SOURCE_EXTS = {".opus", ".ogg"}

# MP3 bitrate (kbps). 128k is a good size/quality balance when re-encoding an
# already-lossy Opus source (higher just grows the file without recovering quality).
MP3_BITRATE = "128k"


def convert(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        print(f"[skip] not a file: {path}")
        return

    ext = os.path.splitext(path)[1].lower()
    if ext not in SOURCE_EXTS:
        print(f"[skip] not an .opus/.ogg file: {os.path.basename(path)}")
        return

    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(SCRIPT_DIR, stem + ".mp3")

    print(f"Converting {os.path.basename(path)} -> {os.path.basename(out)} ...")
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", path, "-codec:a", "libmp3lame", "-b:a", MP3_BITRATE, out],
            check=True,
        )
        print(f"  OK -> {out}")
    except FileNotFoundError:
        print("  ERROR: ffmpeg not found. Install it and make sure it is on your PATH.")
    except subprocess.CalledProcessError as exc:
        print(f"  FAILED: {exc}")


def main():
    if len(sys.argv) < 2:
        print("Drag one or more .opus/.ogg files onto opus_to_mp3.bat to convert them.")
        print("The .mp3 files are written into the tools/ folder.")
        try:
            input("Press Enter to exit...")
        except (EOFError, OSError):
            pass
        return

    for arg in sys.argv[1:]:
        convert(arg)

    print("Done.")


if __name__ == "__main__":
    main()
