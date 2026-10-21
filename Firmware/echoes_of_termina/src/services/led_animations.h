#if !defined(SERVICE_LED_ANIMATIONS)
#define SERVICE_LED_ANIMATIONS

//? ------------------------------------------------------------------------------------------
//*! LED animations generated from the audio itself.
//*!
//*  These are what plays when a song has NO .echoes lightshow attached. The
//*  priority order is:
//*    1. an authored .echoes show, when one is loaded  (lightshow owns the strip)
//*    2. the selected generated animation, when audio is flowing
//*    3. dark
//*  so an authored show always wins and this feature stays opt-in per song.
//*!
//*  THE PCM TAP
//*  Both playback paths push decoded PCM into one mono ring buffer, which makes
//*  the animations mode-agnostic:
//*
//*    SD / WiFi mode  ESP32-audioI2S weak hook audio_process_extern(), called once
//*                    per decoded block from inside the decoder (Audio.cpp:4157).
//*    Bluetooth mode  BluetoothA2DPSink::set_raw_stream_reader(), called from the
//*                    BT stack's work queue (BluetoothA2DPSink.cpp:1311).
//*
//*  Both run in the MAIN LOOP or the BT work queue — never an ISR — and both are
//*  on a path that feeds the I2S DMA, so the callback does nothing but a memcpy.
//*  Any analysis belongs in the frame pump, not in the tap.
//*!
//*  The alternative — reading the INMP441 microphone over I2S_NUM_1 — was
//*  rejected: it is 16 kHz (8 kHz of usable bandwidth), it hears the room and its
//*  own speaker, and installing that peripheral costs DMA-capable internal RAM,
//*  which is the scarcest resource on this board.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

// Selectable generated animation. The value is persisted in NVS, so append new
// modes rather than renumbering.
typedef enum
{
    LED_ANIM_NONE = 0,     // no generated animation (the strip stays dark for songs without a show)
    LED_ANIM_ENVELOPE = 1, // amplitude envelope, mirrored level bar
    LED_ANIM_FFT = 2,      // frequency spectrum, low frequencies at the centre spreading outward
} led_anim_mode_t;

#ifndef LED_ANIM_DEFAULT
#define LED_ANIM_DEFAULT LED_ANIM_NONE
#endif

#define LED_ANIM_NVS_KEY "anim"

// ---------------------------------------------------------------------------
// PCM ring buffer
// ---------------------------------------------------------------------------

// Mono, power of two. Sized for the analysis window plus slack, not for a whole
// song: only the newest window is ever read.
#define LED_PCM_RING 1024
#define LED_PCM_MASK (LED_PCM_RING - 1)

static int16_t led_pcm_ring[LED_PCM_RING];
static volatile uint16_t led_pcm_head = 0; // next write index
static volatile uint32_t led_pcm_total = 0; // cumulative mono samples, for activity detection

// Format of the blocks the active tap will hand over. The SD tap cannot see the
// decoder's channel count (its hook signature carries only the buffer and a frame
// count), so run_audio_lightshow() refreshes this from the player each iteration.
// Bluetooth A2DP is always 16-bit stereo, so the defaults are correct there.
static uint8_t led_pcm_channels = 2;
static uint8_t led_pcm_bits = 16;
// Sample rate of the incoming blocks, needed to turn FFT bins into frequencies.
// The SD side reports the decoder's real rate (44.1 or 48 kHz); A2DP is 44.1 kHz
// unless the source negotiates 48, so this is the default it starts from.
static uint32_t led_pcm_rate = 44100;

static void led_pcm_set_format(uint8_t channels, uint8_t bits, uint32_t rate)
{
    if (channels >= 1 && channels <= 2)
        led_pcm_channels = channels;
    if (bits == 8 || bits == 16)
        led_pcm_bits = bits;
    if (rate >= 8000 && rate <= 192000)
        led_pcm_rate = rate;
}

static led_anim_mode_t led_anim_mode = LED_ANIM_DEFAULT;

// True when an animation is selected, so the taps can skip the copy entirely.
// This is why the default is LED_ANIM_NONE: with it off there is no per-block
// cost at all on the playback path.
static bool led_anim_enabled()
{
    return led_anim_mode != LED_ANIM_NONE;
}

// Pushes 16-bit interleaved PCM into the mono ring, downmixing stereo by summing.
// Called from the tap, so it is deliberately a tight loop.
static void led_pcm_push_interleaved(const int16_t *pcm, uint32_t frames, uint8_t channels)
{
    uint16_t head = led_pcm_head;
    if (channels >= 2)
    {
        for (uint32_t i = 0; i < frames; i++)
        {
            // >>1 rather than /2: an arithmetic shift is fine here because a
            // half-LSB bias on odd negative sums is irrelevant to a level meter.
            led_pcm_ring[head] = (int16_t)(((int32_t)pcm[i * 2] + (int32_t)pcm[i * 2 + 1]) >> 1);
            head = (uint16_t)((head + 1) & LED_PCM_MASK);
        }
    }
    else
    {
        for (uint32_t i = 0; i < frames; i++)
        {
            led_pcm_ring[head] = pcm[i];
            head = (uint16_t)((head + 1) & LED_PCM_MASK);
        }
    }
    led_pcm_head = head;
    led_pcm_total += frames;
}

// ── SD / WiFi tap ──────────────────────────────────────────────────────────
// ESP32-audioI2S declares this weak and never defines it (Audio.h:87), so this
// definition is what resolves the symbol and makes the library call it.
//
// len is a FRAME count, not a sample count: the library sets
// m_validSamples = MP3GetOutputSamps() / getChannels() (Audio.cpp:4146) and
// playChunk() reads the buffer as interleaved m_outBuff[frame * 2 + ch]
// (Audio.cpp:2386-2391).
//
// *continueI2S MUST be set true. The library returns early and skips the I2S
// write when it comes back false (Audio.cpp:4160-4163), so forgetting this
// SILENTLY MUTES ALL PLAYBACK.
void audio_process_extern(int16_t *buff, uint16_t len, bool *continueI2S)
{
    *continueI2S = true;

    if (!led_anim_enabled() || buff == nullptr || len == 0)
        return;
    if (led_pcm_bits != 16)
        return; // 8-bit WAV packs two frames per word; see the note in the log below

    led_pcm_push_interleaved(buff, len, led_pcm_channels);
}

// ── Bluetooth tap ──────────────────────────────────────────────────────────
// Registered with BluetoothA2DPSink::set_raw_stream_reader() from
// setup_bluetooth_mode(). Measured on Echoes V1: 215 blocks per 5000 ms = 43
// blocks/s of 1024 frames each, 44032 frames/s — real 44.1 kHz stereo PCM.
//
// raw_stream_reader, not stream_reader: set_stream_reader() also assigns
// `is_output` (BluetoothA2DPSink.cpp:123-127), so a wrong second argument silences
// the DAC entirely, and it fires AFTER volume control. The raw reader cannot mute
// anything and fires before volume, so the level it sees is independent of the
// ESP32's own volume setting.
//
// Runs in the BT stack's work queue: it only copies, same rule as the SD tap.
void led_audio_bt_stream(const uint8_t *data, uint32_t len)
{
    if (!led_anim_enabled() || data == nullptr)
        return;
    // A2DP always hands over 16-bit interleaved stereo: len bytes = len/4 frames.
    uint32_t frames = len / 4;
    if (frames == 0)
        return;
    led_pcm_push_interleaved((const int16_t *)data, frames, 2);
}

// ---------------------------------------------------------------------------
// Analysis
// ---------------------------------------------------------------------------

// Rectified-mean and peak of the newest *window* samples. Mean-abs rather than RMS:
// it is one add and one compare per sample instead of a multiply, and it tracks
// perceived level just as well for a meter.
//
// Not atomic against the Bluetooth tap (which runs in another task), so a sample
// or two may straddle the snapshot. That is invisible on a level meter and not
// worth a critical section on the audio path.
static void led_pcm_analyze(uint16_t window, uint32_t *mean_abs_out, uint32_t *peak_out)
{
    if (window == 0 || window > LED_PCM_RING)
        window = LED_PCM_RING;

    uint16_t head = led_pcm_head;
    uint32_t sum = 0;
    uint32_t peak = 0;
    for (uint16_t i = 0; i < window; i++)
    {
        uint16_t idx = (uint16_t)((head - 1 - i) & LED_PCM_MASK); // newest first
        int32_t v = led_pcm_ring[idx];
        if (v < 0)
            v = -v;
        sum += (uint32_t)v;
        if ((uint32_t)v > peak)
            peak = (uint32_t)v;
    }
    *mean_abs_out = sum / window;
    *peak_out = peak;
}

// ---------------------------------------------------------------------------
// Envelope animation
// ---------------------------------------------------------------------------

#define LED_ENV_WINDOW 512 // ~12 ms at 44.1 kHz: responsive without chasing transients

// Activity window. Judged over a WINDOW, not per frame: A2DP delivers ~43 blocks/s
// (1024 frames each, measured) while the pump runs at ~66 fps, so most frames see no
// new block — that is normal, not silence. A real pause or stop stops blocks
// entirely, so this still reaches zero within the timeout.
#define LED_PCM_ACTIVE_TIMEOUT_MS 150

// Level mapping, in mean-abs terms over the window (fraction of full scale).
//
// CALIBRATION HISTORY — worth keeping, because the mistakes are instructive.
//
// Attempt 1 (FLOOR 0.006 / CEIL 0.300, aiming at -44..-10.5 dBFS): guessed, and
// left the bar crawling along the bottom fifth of the strip.
//
// Attempt 2 (FLOOR 0.004 / CEIL 0.100): calibrated against the 5 s average levels
// logged from the board (mean|s| 1028..2308 = -30..-23 dBFS). Better, but still
// reported as "lacking magnitude" on hardware. The reason is a mismatch of
// quantities: the log's 5 s mean is averaged over 5000 ms, while THIS detector
// reads a 512-sample (12 ms) window, and a short-window mean|s| has a skewed
// distribution whose typical value sits well below the long-window mean. Calibrating
// a fast detector from a slow average under-drives it, every time.
//
// Attempt 3 (these values): more gain and a gamma to lift the midrange, so the bar
// uses its whole length instead of the bottom third:
//     FLOOR 0.003  ~= -51 dBFS   (below this: dark)
//     CEIL  0.070  ~= -25 dBFS   (and above: full)
//     GAMMA 0.6                  (expands the low/mid range upward)
// LED_ENV_TELEMETRY now logs the actual short-window numbers once a second, so the
// next adjustment can be made from data instead of by eye.
//
// CAVEAT worth knowing: the tap is pre-ESP32-volume but NOT pre-source-volume — a
// phone applies its own volume before the A2DP stream is encoded — so a much louder
// source will sit higher and can pin the bar at full. LED_ENV_CEIL is the knob; the
// alternative is an auto-gain that tracks the source level.
#define LED_ENV_FLOOR 0.003f
#define LED_ENV_CEIL 0.070f
#define LED_ENV_GAMMA 0.6f

// Logs the raw short-window level and the resulting bar height once a second. This
// is what makes the next calibration a measurement rather than a guess. Set to 0
// once the envelope looks right.
#ifndef LED_ENV_TELEMETRY
#define LED_ENV_TELEMETRY 1
#endif

// Time constants, in SECONDS, so the animation behaves identically at 66 fps and
// at 200 fps. Fast attack, slow release — the classic meter feel.
#define LED_ENV_ATTACK_S 0.006f
#define LED_ENV_RELEASE_S 0.250f
#define LED_ENV_PEAK_S 1.200f

static float led_env_level = 0.0f; // smoothed bar level, 0..1
static float led_env_peak = 0.0f;  // decaying peak marker, 0..1

static void _anim_set(uint16_t idx, uint8_t r, uint8_t g, uint8_t b)
{
    if (idx < majora_led_count)
        majora.setPixelColor(idx, majora.Color(r, g, b));
}

// Bar colour: deep blue at the bottom rising to white at the top, so the level
// reads at a glance. Written at full scale deliberately — the brightness ceiling
// applied via majora.setBrightness() scales it down at output, so these values do
// not need to know about the strip length or the current budget.
static void _anim_level_colour(float t, uint8_t *r, uint8_t *g, uint8_t *b)
{
    if (t < 0.0f)
        t = 0.0f;
    if (t > 1.0f)
        t = 1.0f;
    *r = (uint8_t)(20.0f + 235.0f * t * t);
    *g = (uint8_t)(60.0f + 195.0f * t);
    *b = (uint8_t)(140.0f + 115.0f * t);
}

// Fills from the centre outward, mirrored, so the bar grows symmetrically about
// the middle of the strip — the same centre the FFT spread uses.
static void led_anim_render_envelope()
{
    uint16_t total = majora_led_count;
    if (total == 0)
        return;

    uint16_t half = total / 2; // LEDs on each side of the centre
    float lit_f = led_env_level * (float)half;

    for (uint16_t k = 0; k < half; k++)
    {
        // k = 0 is the innermost LED of the bar; frac is how much of it is lit,
        // which gives the bar a soft edge instead of a hard step.
        float frac = lit_f - (float)k;
        if (frac <= 0.0f)
            continue;
        if (frac > 1.0f)
            frac = 1.0f;

        // Position along the bar, for the colour ramp.
        float t = (float)k / (float)half;
        uint8_t r, g, b;
        _anim_level_colour(t, &r, &g, &b);

        uint16_t left = (uint16_t)(half - 1 - k);
        uint16_t right = (total & 1) ? (uint16_t)(half + 1 + k) : (uint16_t)(half + k);

        // Scale by frac, which is what softens the leading edge.
        _anim_set(left, (uint8_t)(r * frac), (uint8_t)(g * frac), (uint8_t)(b * frac));
        _anim_set(right, (uint8_t)(r * frac), (uint8_t)(g * frac), (uint8_t)(b * frac));
    }

    // Odd strips have one true centre LED, which belongs to the bar too.
    if (total & 1)
    {
        float frac = led_env_level > 0.0f ? led_env_level : 0.0f;
        if (frac > 1.0f)
            frac = 1.0f;
        uint8_t r, g, b;
        _anim_level_colour(0.0f, &r, &g, &b);
        _anim_set(half, (uint8_t)(r * frac), (uint8_t)(g * frac), (uint8_t)(b * frac));
    }

    // Peak marker: a warm dot at the highest level seen recently, on both sides.
    float peak_f = led_env_peak * (float)half;
    if (peak_f >= 1.0f && half > 0)
    {
        uint16_t k = (uint16_t)(peak_f - 1.0f);
        if (k >= half)
            k = (uint16_t)(half - 1);
        uint16_t left = (uint16_t)(half - 1 - k);
        uint16_t right = (total & 1) ? (uint16_t)(half + 1 + k) : (uint16_t)(half + k);
        _anim_set(left, 255, 140, 0);
        _anim_set(right, 255, 140, 0);
    }
}

// ---------------------------------------------------------------------------
// FFT spread
// ---------------------------------------------------------------------------
//
// GEOMETRY: one LED = one frequency band, so the only visual dimension is
// brightness and the strip reads as a spectrum. Band 0 (lowest frequency) sits at
// the centre and frequency rises toward both ends — "spread from centre, mirrored
// out". With bands = min(96, N/2) each band lands on exactly one LED per side at
// every supported strip length, which is also why there is no per-band peak-hold:
// there is no spatial room for a moving dot.
//
// SPECTRAL RESOLUTION, and why a 256-point FFT is enough
// A plain 256-point FFT at 44.1 kHz has 172 Hz bins, so every band below ~1 kHz
// would collapse onto the same bin and the bottom third of the strip would be one
// flat block. Decimating by 4 first — a boxcar average, which doubles as a crude
// anti-alias filter — gives 43 Hz bins at the same transform size.
//
// The window is 1024 source samples = 23 ms = exactly the PCM ring, so the
// transform covers the newest ring's worth of audio with no extra buffer. The
// tradeoff is the Nyquist ceiling: 5.5 kHz at 44.1 kHz. That is a deliberate
// choice rather than only a limitation — content above ~5 kHz is mostly cymbals
// and noise and makes a spectrum display jitter — but it is the number to raise if
// the top few bands ever look wrong. Doubling it means LED_FFT_SIZE 512 with
// LED_FFT_DECIM 2, which costs 2 KB more for the re/im buffers.
#define LED_FFT_SIZE 256
#define LED_FFT_LOG2 8
#define LED_FFT_DECIM 4
#define LED_FFT_BINS (LED_FFT_SIZE / 2) // usable bins (bin 0 = DC, skipped)
#define LED_FFT_MAX_BANDS 96

// The window must be exactly one ring: the transform reads the whole ring in place.
static_assert(LED_FFT_SIZE * LED_FFT_DECIM == LED_PCM_RING,
              "the FFT window must be exactly one PCM ring");

#define LED_FFT_BAND_LOW_HZ 40.0f // lowest displayed frequency (matches the editor)
#define LED_FFT_FLOOR_DB (-55.0f)
#define LED_FFT_CEIL_DB (-6.0f)
#define LED_FFT_ATTACK_S 0.030f
#define LED_FFT_RELEASE_S 0.180f

// A full-scale sine at a bin centre with a Hann window peaks at A*N/4 (verified
// numerically: ratio 0.9961), so this normalises a band magnitude to ~0..1 before
// the dB mapping. File-scope so the calibration telemetry below can use it too.
#define LED_FFT_MAG_REF (32767.0f * (float)LED_FFT_SIZE * 0.25f)

// Bring-up telemetry: logs raw per-band dB once a second so the dB window above can
// be calibrated from measurement. The FFT was confirmed good on hardware with these
// anchors, so this is off by default now; set to 1 to re-inspect (e.g. after
// changing LED_FFT_SIZE / LED_FFT_DECIM, which move the whole band plan).
#ifndef LED_FFT_TELEMETRY
#define LED_FFT_TELEMETRY 0
#endif

// ~5.2 KB, and only meaningful when LED_ANIM_FFT is selected. Static rather than
// lazy-allocated to keep the analysis path free of failure branches; the two big
// dynamic buffers in this firmware (the .echoes pixel buffer and Adafruit's RMT
// encoding buffer) are the ones that actually matter for the RAM budget.
static float fft_re[LED_FFT_SIZE];
static float fft_im[LED_FFT_SIZE];
static float fft_win[LED_FFT_SIZE];
static float fft_tw_re[LED_FFT_BINS];
static float fft_tw_im[LED_FFT_BINS];
static uint16_t fft_brev[LED_FFT_SIZE];
static uint8_t fft_bin0[LED_FFT_MAX_BANDS];
static uint8_t fft_bin1[LED_FFT_MAX_BANDS];
static uint8_t fft_band_count = 0;
static float led_fft_level[LED_FFT_MAX_BANDS];
static bool fft_tables_ready = false;
// The band plan depends on both the sample rate and the strip length, so it is
// rebuilt when either changes rather than assumed once.
static uint32_t fft_plan_rate = 0;
static uint16_t fft_plan_leds = 0;

// Bit-reversal table, twiddle factors and a Hann window, computed once. Hann
// rather than rectangular: leakage from a rectangular window smears strong low
// frequencies across the whole spectrum, which on a 1-LED-per-band display reads
// as the entire strip flickering with the bass.
static void _fft_init_tables()
{
    if (fft_tables_ready)
        return;

    for (uint16_t i = 0; i < LED_FFT_SIZE; i++)
    {
        uint16_t r = 0, x = i;
        for (uint8_t b = 0; b < LED_FFT_LOG2; b++)
        {
            r = (uint16_t)((r << 1) | (x & 1));
            x = (uint16_t)(x >> 1);
        }
        fft_brev[i] = r;
    }
    for (uint16_t i = 0; i < LED_FFT_BINS; i++)
    {
        float a = -2.0f * (float)PI * (float)i / (float)LED_FFT_SIZE;
        fft_tw_re[i] = cosf(a);
        fft_tw_im[i] = sinf(a);
    }
    for (uint16_t i = 0; i < LED_FFT_SIZE; i++)
        fft_win[i] = 0.5f * (1.0f - cosf(2.0f * (float)PI * (float)i / (float)(LED_FFT_SIZE - 1)));

    fft_tables_ready = true;
}

// Log-spaced band plan, following the Echoes editor's core/spectrum.py
// _band_plan(): each band spans at least one bin, and DC (bin 0) is skipped.
//
// One deliberate departure from the editor. It maps bands to bins purely by
// frequency, which below ~1 kHz collapses many bands onto the same bin — fine on a
// 2D spectrogram where they are a thin sliver, but on a strip where one band IS one
// LED it makes a block of LEDs show an identical value. Measured with a pure
// frequency mapping at 144 LEDs: 11 bands per side landing on the same bin, 14 at
// 200. The fix here is `min_b0`: every band must claim a bin no earlier band owns,
// so the low bands walk up the bins one at a time (a 43 Hz step at 44.1 kHz)
// instead of piling onto bin 1. High bands are unaffected, since there their log
// width already exceeds a bin. This uses the available resolution instead of
// discarding it, and is visually indistinguishable from pure log spacing.
static void _fft_build_band_plan()
{
    uint16_t bands = (uint16_t)(majora_led_count / 2);
    if (bands > LED_FFT_MAX_BANDS)
        bands = LED_FFT_MAX_BANDS;
    if (bands < 1)
    {
        fft_band_count = 0;
        fft_plan_rate = led_pcm_rate;
        fft_plan_leds = majora_led_count;
        return;
    }

    float eff_rate = (float)led_pcm_rate / (float)LED_FFT_DECIM;
    float bin_hz = eff_rate / (float)LED_FFT_SIZE;
    float lo = LED_FFT_BAND_LOW_HZ;
    float hi = eff_rate / 2.0f; // Nyquist of the decimated signal
    if (hi <= lo * 1.01f || bin_hz <= 0.0f)
    {
        fft_band_count = 0;
        fft_plan_rate = led_pcm_rate;
        fft_plan_leds = majora_led_count;
        Serial.printf("[LED] FFT plan unusable: rate %u gives %.0f Hz Nyquist\n",
                      (unsigned)led_pcm_rate, hi);
        return;
    }

    float ratio = powf(hi / lo, 1.0f / (float)bands);
    float f = lo;
    int min_b0 = 1; // next bin no earlier band has claimed
    for (uint16_t b = 0; b < bands; b++)
    {
        float f0 = f;
        float f1 = f * ratio;
        f = f1;

        int b0 = (int)floorf(f0 / bin_hz);
        if (b0 < min_b0)
            b0 = min_b0;
        if (b0 > LED_FFT_BINS - 1)
            b0 = LED_FFT_BINS - 1;

        int b1 = (int)ceilf(f1 / bin_hz);
        if (b1 < b0 + 1)
            b1 = b0 + 1; // never collapse onto zero bins
        if (b1 > LED_FFT_BINS)
            b1 = LED_FFT_BINS;

        fft_bin0[b] = (uint8_t)b0;
        fft_bin1[b] = (uint8_t)b1;
        min_b0 = b0 + 1;
    }
    fft_band_count = (uint8_t)bands;
    fft_plan_rate = led_pcm_rate;
    fft_plan_leds = majora_led_count;

    Serial.printf("[LED] FFT plan: %u bands, %.0f Hz..%.0f Hz, bin %.1f Hz (rate %u / %u decim)\n",
                  (unsigned)bands, lo, hi, bin_hz,
                  (unsigned)led_pcm_rate, (unsigned)LED_FFT_DECIM);
}

// In-place iterative radix-2 complex FFT. Real input goes in fft_re with fft_im
// zeroed, so the upper half of the output is the conjugate mirror and only bins
// 1..LED_FFT_BINS-1 are meaningful.
static void _fft_run()
{
    for (uint16_t i = 0; i < LED_FFT_SIZE; i++)
    {
        uint16_t j = fft_brev[i];
        if (j > i)
        {
            float t = fft_re[i];
            fft_re[i] = fft_re[j];
            fft_re[j] = t;
            t = fft_im[i];
            fft_im[i] = fft_im[j];
            fft_im[j] = t;
        }
    }

    for (uint16_t len = 2; len <= LED_FFT_SIZE; len = (uint16_t)(len << 1))
    {
        uint16_t half = (uint16_t)(len >> 1);
        uint16_t step = (uint16_t)(LED_FFT_SIZE / len);
        for (uint16_t i = 0; i < LED_FFT_SIZE; i = (uint16_t)(i + len))
        {
            for (uint16_t k = 0; k < half; k++)
            {
                uint16_t tw = (uint16_t)(k * step);
                float wr = fft_tw_re[tw];
                float wi = fft_tw_im[tw];
                uint16_t a = (uint16_t)(i + k);
                uint16_t b = (uint16_t)(a + half);

                float tr = fft_re[b] * wr - fft_im[b] * wi;
                float ti = fft_re[b] * wi + fft_im[b] * wr;

                fft_re[b] = fft_re[a] - tr;
                fft_im[b] = fft_im[a] - ti;
                fft_re[a] += tr;
                fft_im[a] += ti;
            }
        }
    }
}

// Decimates the newest ring into the transform input and applies the window.
// Reads straight out of the ring rather than copying first: a 1024-sample local
// would be 2 KB of stack on a task that has none to spare.
static void _fft_fill_input()
{
    uint16_t head = led_pcm_head;
    uint16_t base = (uint16_t)(head - LED_PCM_RING); // uint16 wraparound is the point

    for (uint16_t n = 0; n < LED_FFT_SIZE; n++)
    {
        int32_t acc = 0;
        for (uint8_t d = 0; d < LED_FFT_DECIM; d++)
        {
            uint16_t idx = (uint16_t)(base + (uint16_t)(n * LED_FFT_DECIM + d)) & LED_PCM_MASK;
            acc += led_pcm_ring[idx];
        }
        // Boxcar average: decimation plus a crude low-pass in one step.
        fft_re[n] = ((float)acc / (float)LED_FFT_DECIM) * fft_win[n];
        fft_im[n] = 0.0f;
    }
}

// Runs the transform, folds it into per-band levels in dB, and smooths each band.
// The attack/release coefficients are computed once per frame because every band
// shares the frame's dt.
static void led_anim_fft_analyze(float dt_s, bool audio_flowing)
{
    _fft_init_tables();
    if (fft_plan_rate != led_pcm_rate || fft_plan_leds != majora_led_count)
        _fft_build_band_plan();
    if (fft_band_count == 0)
        return;

    _fft_fill_input();
    _fft_run();

    float ka = 1.0f - expf(-dt_s / LED_FFT_ATTACK_S);
    float kr = 1.0f - expf(-dt_s / LED_FFT_RELEASE_S);

    // A full-scale sine at a bin centre with a Hann window peaks at A*N/4, so this
    // normalises the magnitude to roughly 0..1 before the dB mapping.
    const float mag_ref = LED_FFT_MAG_REF;

    for (uint16_t b = 0; b < fft_band_count; b++)
    {
        // Peak within the band, not the mean: a band should light for the
        // strongest component in it, otherwise narrow peaks get averaged away.
        float best = 0.0f;
        for (uint8_t k = fft_bin0[b]; k < fft_bin1[b]; k++)
        {
            float m = sqrtf(fft_re[k] * fft_re[k] + fft_im[k] * fft_im[k]);
            if (m > best)
                best = m;
        }

        float target = 0.0f;
        if (audio_flowing && best > 0.0f)
        {
            float db = 20.0f * log10f(best / mag_ref);
            target = (db - LED_FFT_FLOOR_DB) / (LED_FFT_CEIL_DB - LED_FFT_FLOOR_DB);
            if (target < 0.0f)
                target = 0.0f;
            if (target > 1.0f)
                target = 1.0f;
        }

        float &lvl = led_fft_level[b];
        if (target > lvl)
            lvl += (target - lvl) * ka;
        else
            lvl += (target - lvl) * kr;
    }
}

#if LED_FFT_TELEMETRY
// Logs raw per-band dB once a second. This is the measurement that calibrates
// LED_FFT_FLOOR_DB / LED_FFT_CEIL_DB: the anchors are guesses until real per-band
// numbers come back from the hardware. Recomputes the magnitudes from the transform
// still sitting in fft_re/fft_im (the analysis ran this frame), so it costs nothing
// and needs no extra storage.
static void _fft_telemetry(bool audio_flowing)
{
    static uint32_t last = 0;
    uint32_t now = millis();
    if (now - last < 1000)
        return;
    last = now;
    if (fft_band_count == 0)
        return;

    const uint16_t probes[] = {0, 4, 12, 24, 48, 72, 95};
    Serial.printf("[FFTBAND] %s bands=%u:", audio_flowing ? "audio " : "silent",
                  (unsigned)fft_band_count);
    for (uint8_t i = 0; i < sizeof(probes) / sizeof(probes[0]); i++)
    {
        uint16_t b = probes[i];
        if (b >= fft_band_count)
            continue;
        float best = 0.0f;
        for (uint8_t k = fft_bin0[b]; k < fft_bin1[b]; k++)
        {
            float m = sqrtf(fft_re[k] * fft_re[k] + fft_im[k] * fft_im[k]);
            if (m > best)
                best = m;
        }
        float db = best > 0.0f ? 20.0f * log10f(best / LED_FFT_MAG_REF) : -99.0f;
        Serial.printf(" b%u=%.0fdB/%.2f", (unsigned)b, db, led_fft_level[b]);
    }
    Serial.printf("  (floor %.0f ceil %.0f dB)\n", LED_FFT_FLOOR_DB, LED_FFT_CEIL_DB);
}
#endif

// Three-stop ramp, deliberately distinct from the envelope's blue-white so the two
// animations are tellable apart at a glance: orange-red (low) -> yellow-green
// (mid) -> cyan-blue (high).
static void _anim_fft_colour(float t, uint8_t *r, uint8_t *g, uint8_t *b)
{
    if (t < 0.0f)
        t = 0.0f;
    if (t > 1.0f)
        t = 1.0f;

    float r0, g0, b0, r1, g1, b1, u;
    if (t < 0.5f)
    {
        r0 = 255.0f, g0 = 30.0f, b0 = 0.0f;
        r1 = 180.0f, g1 = 255.0f, b1 = 0.0f;
        u = t * 2.0f;
    }
    else
    {
        r0 = 180.0f, g0 = 255.0f, b0 = 0.0f;
        r1 = 0.0f, g1 = 180.0f, b1 = 255.0f;
        u = (t - 0.5f) * 2.0f;
    }
    *r = (uint8_t)(r0 + (r1 - r0) * u);
    *g = (uint8_t)(g0 + (g1 - g0) * u);
    *b = (uint8_t)(b0 + (b1 - b0) * u);
}

// One band per LED position, band 0 at the centre. The position -> band mapping is
// nearest-neighbour, so every LED always gets a value even in the one case where
// there are fewer bands than positions (bands caps at 96 while a 200-LED strip has
// 100 positions per side).
static void led_anim_render_fft()
{
    uint16_t total = majora_led_count;
    if (total == 0 || fft_band_count == 0)
        return;

    uint16_t half = (uint16_t)(total / 2);
    if (half == 0)
        return;

    for (uint16_t k = 0; k < half; k++)
    {
        uint16_t b = (uint16_t)((uint32_t)k * fft_band_count / half);
        if (b >= fft_band_count)
            b = (uint16_t)(fft_band_count - 1);

        float t = (fft_band_count > 1) ? (float)b / (float)(fft_band_count - 1) : 0.0f;
        uint8_t r, g, bl;
        _anim_fft_colour(t, &r, &g, &bl);

        float lvl = led_fft_level[b];
        if (lvl < 0.0f)
            lvl = 0.0f;
        if (lvl > 1.0f)
            lvl = 1.0f;

        uint16_t left = (uint16_t)(half - 1 - k);
        uint16_t right = (total & 1) ? (uint16_t)(half + 1 + k) : (uint16_t)(half + k);
        _anim_set(left, (uint8_t)(r * lvl), (uint8_t)(g * lvl), (uint8_t)(bl * lvl));
        _anim_set(right, (uint8_t)(r * lvl), (uint8_t)(g * lvl), (uint8_t)(bl * lvl));
    }

    // An odd strip has one true centre LED, which belongs to band 0 with the pair
    // either side of it.
    if (total & 1)
    {
        float lvl = led_fft_level[0];
        if (lvl < 0.0f)
            lvl = 0.0f;
        if (lvl > 1.0f)
            lvl = 1.0f;
        uint8_t r, g, bl;
        _anim_fft_colour(0.0f, &r, &g, &bl);
        _anim_set(half, (uint8_t)(r * lvl), (uint8_t)(g * lvl), (uint8_t)(bl * lvl));
    }
}

// ---------------------------------------------------------------------------
// Pump
// ---------------------------------------------------------------------------

#if LED_ENV_TELEMETRY
// Logs the ACTUAL short-window level once a second, alongside the bar height it
// produced. This is the measurement that was missing: attempt 2 was calibrated
// against 5 s averages from the [BTTAP] line, but this detector reads a 12 ms
// window, and the two distributions are not the same shape. With these numbers the
// FLOOR/CEIL/GAMMA triple can be set from data rather than by eye.
static void _env_telemetry(uint32_t mean_abs, float level, bool audio_flowing)
{
    static uint32_t last = 0;
    uint32_t now = millis();
    if (now - last < 1000)
        return;
    last = now;

    float raw = (float)mean_abs / 32768.0f;
    float db = raw > 0.0f ? 20.0f * log10f(raw) : -99.0f;
    Serial.printf("[ENV] %s mean|s|=%u (%.1f dBFS) level=%.3f peak=%.3f bar=%.1f/%u%s\n",
                  audio_flowing ? "audio " : "silent",
                  (unsigned)mean_abs, db, level, led_env_peak,
                  level * (float)(majora_led_count / 2), (unsigned)(majora_led_count / 2),
                  level >= 0.999f ? "  <-- pinned at full" : "");
}
#endif

// Clears the strip and the animation's state when it stops driving, so the strip
// does not stay lit after a stop or a switch to an authored show.
static void _anim_reset()
{
    led_env_level = 0.0f;
    led_env_peak = 0.0f;
    for (uint16_t b = 0; b < LED_FFT_MAX_BANDS; b++)
        led_fft_level[b] = 0.0f;

    for (uint16_t i = 0; i < majora_led_count; i++)
        majora.setPixelColor(i, 0);
    majora.show();
}

// Frame pump — call once per loop() iteration, after run_lightshow().
void run_led_animation()
{
    static bool was_on = false;
    static uint32_t last_frame_ms = 0;
    static uint32_t last_pcm_total = 0;
    // Timestamp of the last observed rise in led_pcm_total — see the activity note
    // below. Starts at 0, so before any audio arrives "now - 0" is uptime and the
    // display correctly starts silent.
    static uint32_t last_pcm_change_ms = 0;

    // An authored show always wins; so does "no animation selected" — and so does
    // the UI overlay (services/ui_feedback.h), which owns the strip while it is
    // reporting a gesture. Both would otherwise draw the same frame twice: each
    // renderer ends in show(), which blocks for the whole RMT transfer.
    bool overlay = ui_feedback_active();
    bool on = led_anim_enabled() && !is_lightshow_playing() && !overlay;
    if (!on)
    {
        if (was_on)
        {
            was_on = false;
            // Do NOT clear the strip when a show took over: run_lightshow() has
            // already drawn this frame, and blanking it here would throw that frame
            // away — a visible flicker on every handover. lightshow_stop() clears
            // the strip itself when the show ends. The overlay likewise draws its own
            // frame, so it must not be blanked here either.
            if (!is_lightshow_playing() && !overlay)
                _anim_reset();
        }
        // Keep the clock anchored so re-enabling starts with a sane dt.
        last_frame_ms = millis();
        return;
    }
    if (!was_on)
    {
        was_on = true;
        last_frame_ms = millis();
        last_pcm_total = led_pcm_total;
    }

    // Same frame budget as the lightshow engine: show() blocks for the whole RMT
    // transfer, so the period has to scale with the strip length.
    uint16_t period = lightshow_frame_period_ms();
    uint32_t now = millis();
    uint32_t since = now - last_frame_ms;
    if (since < period)
        return;
    last_frame_ms = now;

    float dt_s = (float)since / 1000.0f;
    if (dt_s > LIGHTSHOW_MAX_FRAME_DT_S)
        dt_s = LIGHTSHOW_MAX_FRAME_DT_S;

    // Audio activity, detected from the tap itself rather than from the player
    // object: this keeps the pump identical in SD and Bluetooth mode, where the
    // decoder does not even exist.
    //
    // Measured on the Echoes V1 board: A2DP delivers 215 blocks per 5000 ms = 43
    // blocks/s, so a block arrives every ~23 ms while this pump runs at ~66 fps
    // (15 ms at 200 LEDs). Judging activity per frame — "did a block arrive since
    // the last frame?" — is therefore false on roughly a third of frames, and the
    // old code read that as silence: the bar sagged and the FFT bands dipped, once
    // per frame, at the frame rate. Activity is a WINDOW question, not a frame one.
    uint32_t total = led_pcm_total;
    if (total != last_pcm_total)
    {
        last_pcm_total = total;
        last_pcm_change_ms = now;
    }
    bool audio_flowing = (now - last_pcm_change_ms) < LED_PCM_ACTIVE_TIMEOUT_MS;

    if (led_anim_mode == LED_ANIM_ENVELOPE)
    {
        uint32_t mean_abs = 0, peak = 0;
        led_pcm_analyze(LED_ENV_WINDOW, &mean_abs, &peak);

        float raw = (float)mean_abs / 32768.0f;
        float norm = (raw - LED_ENV_FLOOR) / (LED_ENV_CEIL - LED_ENV_FLOOR);
        if (norm < 0.0f)
            norm = 0.0f;
        if (norm > 1.0f)
            norm = 1.0f;

        // Gamma expansion AFTER the linear map, so the low and middle of the range
        // are stretched upward while the top still reaches full. This is what gives
        // the bar magnitude: the linear map alone spent most of the strip's length
        // on levels that music rarely reaches.
        norm = powf(norm, LED_ENV_GAMMA);

        // Nothing arriving (paused, stopped, disconnected) reads as silence, so
        // the bar falls to zero rather than freezing lit.
        if (!audio_flowing)
            norm = 0.0f;

        // Frame-rate independent smoothing: the per-frame step is derived from the
        // real elapsed time, so 66 fps and 200 fps behave the same.
        if (norm > led_env_level)
            led_env_level += (norm - led_env_level) * (1.0f - expf(-dt_s / LED_ENV_ATTACK_S));
        else
            led_env_level += (norm - led_env_level) * (1.0f - expf(-dt_s / LED_ENV_RELEASE_S));

        // Peak marker falls slowly, and snaps up instantly.
        led_env_peak *= expf(-dt_s / LED_ENV_PEAK_S);
        if (led_env_level > led_env_peak)
            led_env_peak = led_env_level;

        led_anim_render_envelope();
#if LED_ENV_TELEMETRY
        _env_telemetry(mean_abs, norm, audio_flowing);
#endif
    }
    else if (led_anim_mode == LED_ANIM_FFT)
    {
        led_anim_fft_analyze(dt_s, audio_flowing);
        led_anim_render_fft();
#if LED_FFT_TELEMETRY
        _fft_telemetry(audio_flowing); // reads the transform from this frame
#endif
    }

    majora.show();
}

// ---------------------------------------------------------------------------
// Settings (NVS) — same pattern as the LED count in led_config.h
// ---------------------------------------------------------------------------

static bool _led_anim_mode_valid(uint8_t m)
{
    return m == LED_ANIM_NONE || m == LED_ANIM_ENVELOPE || m == LED_ANIM_FFT;
}

static const char *_led_anim_mode_name(uint8_t m)
{
    switch (m)
    {
    case LED_ANIM_ENVELOPE:
        return "envelope";
    case LED_ANIM_FFT:
        return "fft";
    default:
        return "none";
    }
}

led_anim_mode_t load_led_anim_mode()
{
    // Read-WRITE even though this only reads: on a device that has never saved a
    // setting the namespace does not exist yet, and a read-only open logs
    // "[E][Preferences.cpp:50] begin(): nvs_open failed: NOT_FOUND" — alarming red
    // noise on every boot for a completely normal first run. A read-write open
    // creates the (empty) namespace instead. Same reasoning in led_config.h.
    internal_storage.begin(LED_NVS_NAMESPACE, false);
    uint8_t m = internal_storage.getUChar(LED_ANIM_NVS_KEY, (uint8_t)LED_ANIM_DEFAULT);
    internal_storage.end();

    if (!_led_anim_mode_valid(m))
        m = LED_ANIM_NONE;
    led_anim_mode = (led_anim_mode_t)m;
    return led_anim_mode;
}

void save_led_anim_mode(uint8_t m)
{
    if (!_led_anim_mode_valid(m))
        m = LED_ANIM_NONE;
    internal_storage.begin(LED_NVS_NAMESPACE, false);
    internal_storage.putUChar(LED_ANIM_NVS_KEY, m);
    internal_storage.end();
    Serial.printf("[LED] Saved animation: %u\n", (unsigned)m);
}

// Runtime setter for the web UI: persist, then apply. The FFT band plan is built
// on the first analysis frame, so nothing else has to be primed here.
void set_led_anim_mode(uint8_t m)
{
    if (!_led_anim_mode_valid(m))
        m = LED_ANIM_NONE;
    if ((uint8_t)led_anim_mode == m)
        return;
    save_led_anim_mode(m);
    led_anim_mode = (led_anim_mode_t)m;
    Serial.printf("[LED] Animation -> %s\n", _led_anim_mode_name(m));
}

// Read-only accessors for the web UI / status JSON.
uint8_t led_anim_mode_get() { return (uint8_t)led_anim_mode; }
const char *led_anim_mode_name() { return _led_anim_mode_name((uint8_t)led_anim_mode); }

// Boot path: read the persisted mode. Reports the tap state, and warns about the
// one source format the tap cannot read.
void apply_led_anim_config()
{
    load_led_anim_mode();
    Serial.printf("[LED] Animation: %s (default %s)\n",
                  _led_anim_mode_name((uint8_t)led_anim_mode),
                  _led_anim_mode_name((uint8_t)LED_ANIM_DEFAULT));
    if (led_anim_mode != LED_ANIM_NONE)
        Serial.println("[LED] Note: 8-bit WAV sources have no PCM tap (the library packs two frames per word); MP3/AAC/FLAC are 16-bit and work");
}

#endif // SERVICE_LED_ANIMATIONS
