"""
FFT spectrum analysis for the Echoes editor.

Builds a pre-computed STFT ("spectrogram") of a whole audio file so the GUI can
render a spectrum analyser that follows the transport.

Why offline analysis instead of a live audio tap
───────────────────────────────────────────────
Playback goes through QMediaPlayer (core/audio_player.py), which decodes inside
Qt Multimedia and never hands PCM samples to Python — and Qt 6 dropped
QAudioProbe, so there is no supported way to tap the stream.  The editor
therefore follows the same approach as the envelope viewer: decode the file
once with ffmpeg in a worker thread, analyse it there, and let the GUI drive
the rendering from the player's position signal.  The result is a spectrum
that is sample-accurate, seek-safe and free of audio-dropout risk, at the cost
of a one-off analysis pass (see PERFORMANCE below).

PERFORMANCE
───────────
Pure Python, no numpy dependency.  One 1024-point radix-2 FFT with
pre-computed bit-reversal + twiddle tables costs ~0.9 ms on a typical desktop,
so a 3-minute file (~3 900 hops at the defaults below) analyses in ~5 s in a
background thread.  The frame rate (hop) is chosen for smooth rendering rather
than for analysis speed: 22 050 / 512 = 43 frames per second, which matches the
repaint rate of the analyser view.

Frames are stored as one byte per (frame, band) — dBFS quantised over
[-100, 0] dB (0.4 dB steps), 128 log-spaced bands per frame — so a 3-minute
file needs ~0.5 MB and a repaint never touches float data.
"""
from __future__ import annotations

import math
import os
import subprocess
from array import array

from PySide6.QtCore import QThread, Signal

from core.audio_converter import ffmpeg_available

# ── Analysis defaults ───────────────────────────────────────────────────────

DECODE_RATE   = 22050    # analysis sample rate (Hz) — mono, via ffmpeg
FFT_SIZE      = 1024     # → 512 usable bins, 21.5 Hz resolution at DECODE_RATE
HOP           = 512      # → 43.07 frames/s (~50 % overlap)
DEFAULT_BANDS = 128      # log-spaced display bands per frame

BAND_LOW_HZ   = 40.0     # lowest displayed frequency
BAND_HIGH_HZ  = 16000.0  # highest (clamped to Nyquist: 11 025 Hz here)

DB_FLOOR      = -100.0   # dBFS stored as byte 0
DB_CEIL       = 0.0      # dBFS stored as byte 255

_MAX_CHUNK = 1 << 20     # ffmpeg read size (bytes)


# ── FFT ─────────────────────────────────────────────────────────────────────

_TABLE_CACHE: dict = {}


def _fft_tables(n: int):
    """
    Bit-reversal permutation and per-stage complex twiddles for size *n*.

    Pre-computing both (and caching per size) is what keeps the Python FFT
    near 1 ms: the inner loop then does table lookups only, with no
    per-butterfly trigonometry.
    """
    hit = _TABLE_CACHE.get(n)
    if hit is not None:
        return hit

    rev = [0] * n
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        rev[i] = j

    twiddles = []
    length = 2
    while length <= n:
        half = length >> 1
        ang = -2.0 * math.pi / length
        twiddles.append([complex(math.cos(ang * k), math.sin(ang * k))
                         for k in range(half)])
        length <<= 1

    _TABLE_CACHE[n] = (rev, twiddles)
    return rev, twiddles


def _fft(buf: list, rev: list, twiddles: list) -> None:
    """In-place iterative radix-2 FFT of the complex list *buf*."""
    n = len(buf)
    for i in range(1, n):
        j = rev[i]
        if i < j:
            buf[i], buf[j] = buf[j], buf[i]
    for tw in twiddles:
        half = len(tw)
        step = half << 1
        for base in range(0, n, step):
            for k in range(half):
                a = base + k
                b = a + half
                t = buf[b] * tw[k]
                u = buf[a]
                buf[a] = u + t
                buf[b] = u - t


# ── Band plan ───────────────────────────────────────────────────────────────

def _band_plan(rate: int, n_bands: int, fft_size: int,
               lo_hz: float = BAND_LOW_HZ,
               hi_hz: float = BAND_HIGH_HZ) -> list:
    """
    Return ``[(f0, f1, bin0, bin1), …]`` — log-spaced display bands mapped to
    FFT bin ranges.

    Each band spans at least one bin: below ~1 kHz a band is narrower than the
    21.5 Hz bin spacing, and letting such bands collapse onto zero bins would
    leave holes in the low end of the display.
    """
    bins = fft_size // 2          # Nyquist bin index
    df = rate / fft_size          # bin width in Hz
    hi = min(hi_hz, rate / 2.0)
    if lo_hz <= 0 or hi <= lo_hz or n_bands < 1:
        return []
    ratio = (hi / lo_hz) ** (1.0 / n_bands)

    plan = []
    f = lo_hz
    for _ in range(n_bands):
        f0, f1 = f, f * ratio
        f = f1
        b0 = int(math.floor(f0 / df))
        b1 = int(math.ceil(f1 / df))
        b0 = max(1, min(b0, bins - 1))          # skip DC
        b1 = max(b0 + 1, min(b1, bins))
        plan.append((f0, f1, b0, b1))
    return plan


def _bin_band_map(plan: list, bins: int) -> list:
    """Bin index → display band index (-1 for bins below the lowest band)."""
    mapping = [-1] * bins
    for band, (_f0, _f1, b0, b1) in enumerate(plan):
        for k in range(b0, min(b1, bins)):
            if mapping[k] < 0:
                mapping[k] = band
    return mapping


# ── dB ↔ byte ───────────────────────────────────────────────────────────────

_DB_SPAN = DB_CEIL - DB_FLOOR          # 100 dB over the byte range
_DB_STEP = _DB_SPAN / 255.0


def byte_to_db(b: int) -> float:
    """Expand a stored byte back to dBFS."""
    return DB_FLOOR + b * _DB_STEP


# ── Decoding ────────────────────────────────────────────────────────────────

def _probe_duration(path: str) -> float:
    """Duration in seconds from ffprobe, or 0.0 when unavailable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        ).stdout.decode("ascii", "ignore").strip()
        return float(out)
    except Exception:
        return 0.0


def _decode(path: str, rate: int, progress=None) -> tuple:
    """
    Decode *path* to mono float32 at *rate* with ffmpeg.

    Returns ``(samples, duration_s)`` where *samples* is an ``array('f')`` in
    [-1, 1]; ``([], 0.0)`` when ffmpeg is missing or the file cannot be read.
    Reads stdout in chunks so the decode phase can report progress instead of
    looking frozen on long files.
    """
    total_s = _probe_duration(path)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
           "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return array('f'), 0.0

    samples = array('f')
    tail = b""
    read = 0
    try:
        while True:
            chunk = proc.stdout.read(_MAX_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if tail:
                chunk = tail + chunk
            keep = len(chunk) % 4
            if keep:
                tail = chunk[-keep:]
                chunk = chunk[:-keep]
            else:
                tail = b""
            samples.frombytes(chunk)
            if progress is not None and total_s > 0:
                done = read / 4.0 / rate
                progress(min(0.98, done / total_s))
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()

    if tail:                                  # trailing partial float
        samples.frombytes(tail[:len(tail) - (len(tail) % 4)])

    duration_s = len(samples) / float(rate) if rate else 0.0
    return samples, duration_s


# ── Spectrogram ─────────────────────────────────────────────────────────────

class Spectrogram:
    """
    A pre-computed spectrum: *frames* columns of log-spaced bands in dBFS.

    Immutable and cheap to read — the GUI renders straight from the byte
    matrix, converting to dB only for the values it actually paints.
    """

    __slots__ = ("path", "_data", "frames", "bands", "plan", "rate",
                 "fft_size", "hop", "_peaks", "duration_ms", "bin_hz")

    def __init__(self, path: str, data: bytearray, frames: int, bands: int,
                 plan: list, rate: int, fft_size: int, hop: int,
                 peaks: array, duration_ms: int) -> None:
        self.path = path
        self._data = data
        self.frames = frames
        self.bands = bands
        self.plan = plan
        self.rate = rate
        self.fft_size = fft_size
        self.hop = hop
        self._peaks = peaks          # peak bin index per frame
        self.duration_ms = duration_ms
        self.bin_hz = rate / float(fft_size)

    # ── Time ────────────────────────────────────────────────────────────────

    @property
    def frame_ms(self) -> float:
        """Duration of one analysis hop in milliseconds."""
        return self.hop * 1000.0 / self.rate

    def frame_at(self, ms: float) -> int:
        """Frame index covering position *ms* (clamped)."""
        if self.frames <= 0:
            return 0
        f = int(ms / self.frame_ms)
        return 0 if f < 0 else (self.frames - 1 if f >= self.frames else f)

    # ── Values ──────────────────────────────────────────────────────────────

    def raw_column(self, frame: int) -> bytes:
        """Stored bytes of one frame (band 0 = lowest frequency)."""
        off = frame * self.bands
        return bytes(self._data[off:off + self.bands])

    def column_db(self, frame: int) -> list:
        """One frame as a list of dBFS values, band 0 = lowest frequency."""
        off = frame * self.bands
        return [DB_FLOOR + b * _DB_STEP
                for b in self._data[off:off + self.bands]]

    def band_db(self, frame: int, band: int) -> float:
        return byte_to_db(self._data[frame * self.bands + band])

    def band_hz(self, band: int) -> tuple:
        """``(f_low, f_high)`` of a display band in Hz."""
        if not (0 <= band < self.bands):
            return 0.0, 0.0
        return self.plan[band][0], self.plan[band][1]

    def peak_hz(self, frame: int) -> float:
        """Frequency of the strongest bin in *frame* (0.0 when silent)."""
        if not (0 <= frame < self.frames) or len(self._peaks) <= frame:
            return 0.0
        if self.peak_level_db(frame) <= DB_FLOOR + 1.0:
            return 0.0          # digital silence — no meaningful peak
        return self._peaks[frame] * self.bin_hz

    def peak_level_db(self, frame: int) -> float:
        """Level of the strongest band in *frame*."""
        if not (0 <= frame < self.frames):
            return DB_FLOOR
        off = frame * self.bands
        return byte_to_db(max(self._data[off:off + self.bands]))


# ── Analysis ────────────────────────────────────────────────────────────────

def analyze(path: str, n_bands: int = DEFAULT_BANDS, fft_size: int = FFT_SIZE,
            hop: int = HOP, rate: int = DECODE_RATE,
            progress=None, is_cancelled=None):
    """
    Analyse *path* into a :class:`Spectrogram` (blocking; run off the GUI
    thread).  Returns None when ffmpeg is unavailable, the file cannot be
    decoded, or *is_cancelled()* goes true.

    *progress* is called as ``progress(fraction, stage)`` with fraction in
    [0, 1]; *is_cancelled* is polled between analysis chunks so a superseded
    analysis stops within a few dozen frames.
    """
    if not path or not os.path.isfile(path) or not ffmpeg_available():
        return None

    def _say(frac: float, stage: str) -> None:
        if progress is not None:
            progress(frac, stage)

    _say(0.0, "decoding audio")
    samples, duration_s = _decode(path, rate, lambda f: _say(f * 0.5, "decoding audio"))
    n = len(samples)
    if n < fft_size:
        return None

    plan = _band_plan(rate, n_bands, fft_size)
    if not plan:
        return None
    n_bands = len(plan)
    bins = fft_size // 2
    bin_band = _bin_band_map(plan, bins)

    # Hann window, normalised so that a full-scale sine reads 0 dBFS:
    # for a windowed sine of amplitude A, |X[k]| ~= A * sum(win) / 2.
    window = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (fft_size - 1))
              for i in range(fft_size)]
    win_sum = sum(window)
    amp_scale = 2.0 / win_sum

    rev, twiddles = _fft_tables(fft_size)
    frames = 1 + (n - fft_size) // hop
    data = bytearray(frames * n_bands)
    peaks = array('H', bytes(2 * frames))
    acc = [0.0] * n_bands
    weight = [0.0] * n_bands
    for band, (_f0, _f1, b0, b1) in enumerate(plan):
        weight[band] = 1.0 / (b1 - b0)

    dbs = [_DB_STEP * v + DB_FLOOR for v in range(256)]   # byte → dB lookup

    for f in range(frames):
        if is_cancelled is not None and (f & 63) == 0 and is_cancelled():
            return None
        off = f * hop
        seg = samples[off:off + fft_size]
        # Window + copy into the complex work buffer in a single pass.
        buf = [complex(seg[i] * window[i], 0.0) for i in range(fft_size)]
        _fft(buf, rev, twiddles)

        for b in range(n_bands):
            acc[b] = 0.0
        peak_k = 1
        peak_p = -1.0
        for k in range(1, bins):
            z = buf[k]
            p = z.real * z.real + z.imag * z.imag
            if p > peak_p:
                peak_p = p
                peak_k = k
            band = bin_band[k]
            if band >= 0:
                acc[band] += p

        base = f * n_bands
        for b in range(n_bands):
            amp = amp_scale * math.sqrt(acc[b] * weight[b])
            db = 20.0 * math.log10(amp) if amp > 1e-7 else DB_FLOOR
            v = int((db - DB_FLOOR) / _DB_STEP + 0.5)
            data[base + b] = 0 if v < 0 else (255 if v > 255 else v)
        peaks[f] = peak_k

        if progress is not None and (f & 63) == 0:
            _say(0.5 + 0.5 * f / frames, "analysing")

    _say(1.0, "done")
    duration_ms = int(round(duration_s * 1000.0))
    return Spectrogram(path, data, frames, n_bands, plan, rate, fft_size, hop,
                       peaks, duration_ms)


# ── Worker thread ───────────────────────────────────────────────────────────

class SpectrumWorker(QThread):
    """
    Runs :func:`analyze` off the GUI thread.

    *gen* is echoed back with the result so a view can ignore the result of an
    analysis that a newer source change has already superseded.
    """

    progressed = Signal(int, str)        # percent, stage
    done       = Signal(int, object)     # generation, Spectrogram | None

    def __init__(self, gen: int, path: str, n_bands: int = DEFAULT_BANDS,
                 parent=None) -> None:
        super().__init__(parent)
        self._gen = gen
        self._path = path
        self._n_bands = n_bands
        self._cancelled = False

    def cancel(self) -> None:
        """Ask the running analysis to stop (polled every 64 frames)."""
        self._cancelled = True

    def run(self) -> None:
        try:
            spec = analyze(
                self._path, self._n_bands,
                progress=lambda frac, stage:
                    self.progressed.emit(int(frac * 100), stage),
                is_cancelled=lambda: self._cancelled,
            )
        except Exception:
            spec = None
        self.done.emit(self._gen, spec)
