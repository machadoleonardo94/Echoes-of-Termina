#if !defined(SERVICE_AUDIO_CUES)
#define SERVICE_AUDIO_CUES

//? ------------------------------------------------------------------------------------------
//*! Audio cues — short tones the board uses to say what it is doing.
//*!
//*!  The device has a speaker, two buttons and no screen, so sound is the only channel that
//*!  can report a state nobody is standing in front of. This file owns that channel and
//*!  nothing else; the vocabulary itself is in services/audio_cues_api.h (it has to be
//*!  visible to the early headers that request a cue).
//*!
//*!  ── Two sinks, because one is not enough ────────────────────────────────────────────
//*!
//*!  SINK 1 — the Bluetooth stream mixer (cue_mix, registered by cues_begin()).
//*  The A2DP sink calls its hooks from inside audio_data_callback()
//*  (BluetoothA2DPSink.cpp:1311-1349) in this order:
//*!
//*      raw_stream_reader(data, len)      ← before volume control  (the LED tap)
//*      volume_control()->update_audio_data((Frame *)data, len/4)
//*      stream_reader(data, len)          ← AFTER volume control   ← this file
//*      write_audio(data, len)            → I2S
//*!
//*  The buffer is mutable: the library itself casts it to Frame* and rewrites it in
//*  place for the channel swap and for the volume. So a cue generator can simply add
//*  into it. Post-volume is the deliberate choice: a cue mixed here has the same loudness
//*  at 5% and at 100%, which is what a confirmation tone needs.
//*!
//*  SINK 2 — the I2S wire (cue_wire_pump / cue_play_now).
//*  Sink 1 has a hole that used to make every state cue impossible: it only runs when the
//*  phone is SENDING AUDIO. "Waiting for a source", "paired", "hotspot up", "WiFi
//*  connected" and "WiFi failed" all happen with no stream in existence, so the mixer can
//*  never announce them. What saves this is the shape of the sink itself:
//*!
//*    • a2dp_sink.start() → init_i2s() → out->begin() → i2s_driver_install(I2S_NUM_0)
//*      (BluetoothA2DPSink.cpp:231-237, BluetoothA2DPOutput.cpp:74-104) with 44100 Hz /
//*      16-bit / stereo and tx_desc_auto_clear=true, i.e. the DAC is clocked and fed
//*      silence from boot onward;
//*    • the base BluetoothA2DPSink has NO i2s task — i2s_task_handler() and
//*      bt_i2s_task_start_up() are the empty dummies meant for BluetoothA2DPSinkQueued
//*      (BluetoothA2DPSink.h:603-605). PCM reaches I2S only from audio_data_callback().
//*!
//*  So until a source streams, the DMA has no writer and the wire is ours. We render the
//*  same CuePattern straight into i2s_write() and it comes out of the speaker. The gate is
//*  the AUDIO state, not the CONNECTION state: handle_connection_state() sets
//*  connection_state before invoking our callback, so "connected" is exactly the moment we
//*  must still be allowed to write.
//*!
//*  In WiFi mode nothing has installed I2S_NUM_0 yet — the decoder is created lazily
//*  (wifi_portal.h:_ensure_player) — so cue_play_now() installs the peripheral for the
//*  length of one cue and uninstalls it again. That is the same sequence the boot beep
//*  test proved on both boards (components/ESP32/features/i2s_output_test.h), and with the
//*  small DMA setup the bitmap costs 2 KB, not the decoder's 32 KB.
//*!
//*  NOT AVAILABLE IN LOCAL PLAYBACK. ESP32-audioI2S owns I2S_NUM_0 while it runs and has
//*  no mixing hook, so nothing can be played on top of a track from the SD card. In that
//*  state the strip is the feedback channel (services/ui_feedback.h) and cue_play() is a
//*  silent no-op outside Bluetooth mode.
//*!
//*  ── Who claims a request ─────────────────────────────────────────────────────────────
//*  cue_play() drops one request into a single slot (a sequence word plus a payload). Both
//*  sinks compare it against the same cue_seen_seq under a tiny critical section, so a
//*  request is rendered exactly once, by whichever sink is live. The wire pump checks that
//*  the wire is FREE before claiming, so a cue requested while music plays is left in the
//*  slot for the mixer instead of being consumed and lost.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"
#include "services/audio_cues_api.h"
#include <driver/i2s.h>

// Cue loudness, post-volume, as a fraction of full scale. Loud enough to be heard
// over music, quiet enough not to be a jolt. 18% ≈ -15 dBFS.
//
// The wire path bypasses the sink's volume control entirely (it writes the DMA itself),
// so this is also the ABSOLUTE loudness of a state cue: the same at 0% and at 100%. That
// is deliberate for "I am waiting for a phone"; the volume cue uses pitch instead.
#define CUE_AMPLITUDE 6000

// Rate used when we install I2S ourselves (WiFi mode). Matches the rate the A2DP sink
// installs with, so both sinks render at the same pitch.
#define CUE_WIRE_RATE 44100

// Frames per i2s_write on the wire path: exactly one DMA period of the A2DP sink's
// configuration (dma_buf_len = 64). Writing one period per call keeps the pump
// non-blocking in practice — it waits at most one period (~1.5 ms) for space.
#define CUE_WIRE_CHUNK 64

// Silent tail written before the peripheral is uninstalled in WiFi mode, so a cue does
// not end in a click. Eight DMA periods ≈ the depth of the transmit buffer.
#define CUE_WIRE_FLUSH_CHUNKS 9

// Extra periods the loop pump may push in one pass to refill a starved transmit buffer.
// One transmit buffer (8 periods) is the most it can ever need.
#define CUE_WIRE_CATCHUP 8

// Hard cap on a blocking cue. Nothing here should ever reach it; it exists so a bus or
// DMA fault can never hold up a mode transition or the boot.
#define CUE_WIRE_MAX_MS 2000

struct CuePattern
{
    const char *name;
    uint8_t count;
    CueTone tones[CUE_MAX_TONES];
};

// The table is indexed by CueKind, so its order IS the enum's order.
//
// The five state motifs are built on one rule, which is what makes them learnable after
// two or three encounters: RISING means "on / got it / connected", FALLING means "no", and
// a repeated note means "listen, I need something from you". They are also kept apart in
// pitch from the transport cues above them, so "paired" cannot be confused with "playing".
static const CuePattern cue_patterns[CUE_KIND_COUNT] = {
    {"play", 2, {{784, 45}, {1175, 60}, {0, 0}, {0, 0}}},
    {"pause", 2, {{1175, 45}, {784, 60}, {0, 0}, {0, 0}}},
    {"next", 1, {{988, 45}, {0, 0}, {0, 0}, {0, 0}}},
    {"confirm", 2, {{880, 40}, {1319, 55}, {0, 0}, {0, 0}}},
    {"deny", 2, {{440, 60}, {330, 90}, {0, 0}, {0, 0}}},
    {"mode", 3, {{523, 55}, {659, 55}, {784, 80}, {0, 0}}},
    // ── State cues ───────────────────────────────────────────────────────────
    // Two long, low notes rising slowly: "I am here, and I am waiting". Played once at
    // boot (the owner's decision) — the connection cue below is what reports the change.
    {"bt-wait", 2, {{587, 110}, {784, 150}, {0, 0}, {0, 0}}},
    // Three fast notes over an octave: "we are connected" — the brightest thing the
    // device says, because it is the answer to the only question the user has at that
    // moment. Distinct from confirm (two notes, lower).
    {"bt-paired", 3, {{880, 60}, {1175, 60}, {1568, 110}, {0, 0}}},
    // The same note twice, 60 ms apart: a beacon, not an answer. It repeats on purpose,
    // because a hotspot nobody notices is a device that looks broken.
    {"ap-up", 3, {{1047, 90}, {0, 60}, {1047, 90}, {0, 0}}},
    // Rising triad, ending high: WiFi came back.
    {"wifi-ok", 3, {{659, 70}, {880, 70}, {1319, 130}, {0, 0}}},
    // Falling, ending low and long: no network. The mirror image of wifi-ok.
    {"wifi-fail", 3, {{523, 80}, {415, 80}, {330, 200}, {0, 0}}},
    {"tick", 1, {{0, 25}, {0, 0}, {0, 0}, {0, 0}}}, // pitch computed per call
};

// ---------------------------------------------------------------------------
// One voice = one cue being rendered
// ---------------------------------------------------------------------------
//
// The two sinks render the SAME table but must own SEPARATE state: the mixer runs on the
// Bluetooth AV task while the pump runs on the loop task, and for the few milliseconds
// between "the phone started streaming" and "the pump noticed" both can be active. Two
// voices with one shared phase counter would garble both.

struct CueVoice
{
    uint8_t kind;        // pattern being played
    uint8_t tone_index;  // index into cue_patterns[kind].tones
    uint32_t rate;       // samples/s of the wire this voice renders into
    uint32_t tone_pos;   // samples rendered of the current tone
    uint32_t tone_total; // 0 == no tone in progress
    uint32_t gap_left;   // silence inside a pattern, in samples
    uint32_t phase;      // 32-bit phase accumulator
    uint32_t phase_step; // per-sample increment
};

static CueVoice cue_mix_voice = {CUE_CONFIRM, 0, 44100, 0, 0, 0, 0, 0};
static CueVoice cue_wire_voice = {CUE_BT_WAIT, 0, 44100, 0, 0, 0, 0, 0};

// 256-entry sine, generated once. A table rather than sinf() because the mixer renders
// this per sample on the audio callback task.
static int16_t cue_sine[256];
static bool cue_sine_ready = false;

static void cue_build_sine()
{
    if (cue_sine_ready)
        return;
    for (int i = 0; i < 256; i++)
        cue_sine[i] = (int16_t)(sinf((float)i * 2.0f * PI / 256.0f) * CUE_AMPLITUDE);
    cue_sine_ready = true;
}

// Attack/release gain, 0..256. A tone that starts at full amplitude clicks, and
// that click would be louder than the cue itself — every cue would sound like a
// fault. ~2 ms each way at 48 kHz.
static inline int32_t cue_gain(uint32_t pos, uint32_t total)
{
    const uint32_t ramp = 96;
    if (total <= 1)
        return 0;
    if (pos < ramp)
        return (int32_t)((pos * 256) / ramp);
    if (pos + ramp >= total)
    {
        int32_t k = (int32_t)(((int64_t)(total - pos) * 256) / ramp);
        return (k < 0) ? 0 : k;
    }
    return 256;
}

static void cue_tone_start(CueVoice &v, uint32_t hz, uint32_t ms)
{
    if (hz == 0 || ms == 0)
    {
        v.tone_total = 0;
        v.tone_pos = 0;
        v.gap_left = (ms * v.rate) / 1000UL;
        return;
    }
    v.phase = 0;
    v.phase_step = (uint32_t)(((uint64_t)hz << 32) / (uint64_t)v.rate);
    v.tone_total = (ms * v.rate) / 1000UL;
    if (v.tone_total == 0)
        v.tone_total = 1;
    v.tone_pos = 0;
    v.gap_left = 0;
}

// Begins a pattern on one voice. `param` is the pitch for CUE_VOLUME_TICK.
static void cue_voice_start(CueVoice &v, uint8_t kind, uint8_t param, uint32_t rate)
{
    cue_build_sine();

    if (kind >= CUE_KIND_COUNT)
        kind = CUE_CONFIRM;
    if (rate == 0)
        rate = CUE_WIRE_RATE;

    v.kind = kind;
    v.rate = rate;
    v.tone_index = 0;
    v.tone_pos = 0;
    v.tone_total = 0;
    v.gap_left = 0;
    v.phase = 0;
    v.phase_step = 0;

    const CuePattern &p = cue_patterns[kind];
    uint32_t hz = p.tones[0].hz;
    if (kind == CUE_VOLUME_TICK)
        hz = 400 + ((uint32_t)param * 1000) / 100; // 400 Hz .. 1400 Hz
    cue_tone_start(v, hz, p.tones[0].ms);
}

static inline bool cue_voice_idle(const CueVoice &v)
{
    return v.tone_total == 0 && v.gap_left == 0;
}

static inline void cue_voice_stop(CueVoice &v)
{
    v.tone_total = 0;
    v.gap_left = 0;
}

// Renders one frame (mono sample; both sinks duplicate it to every channel).
// Advances the voice's state machine, so it must be called exactly once per frame
// that actually reaches the wire.
static inline int16_t cue_voice_render(CueVoice &v)
{
    if (v.gap_left > 0)
    {
        v.gap_left--;
        return 0;
    }
    if (v.tone_total == 0)
        return 0;

    int32_t raw = cue_sine[(uint8_t)(v.phase >> 24)];
    int16_t s = (int16_t)((raw * cue_gain(v.tone_pos, v.tone_total)) >> 8);
    v.phase += v.phase_step;
    v.tone_pos++;

    if (v.tone_pos >= v.tone_total)
    {
        // Advance to the next tone of the pattern, or stop at the end.
        v.tone_total = 0;
        const CuePattern &p = cue_patterns[v.kind];
        uint8_t next = v.tone_index + 1;
        if (next < p.count)
        {
            v.tone_index = next;
            cue_tone_start(v, p.tones[next].hz, p.tones[next].ms);
        }
    }
    return s;
}

// ---------------------------------------------------------------------------
// Request slot — one cue at a time, claimed by whichever sink is live
// ---------------------------------------------------------------------------

static volatile uint32_t cue_seq = 0;      // bumped by cue_play(); 0 = nothing requested
static volatile uint8_t cue_req_kind = CUE_PLAY;
static volatile uint8_t cue_req_param = 0; // pitch parameter for CUE_VOLUME_TICK

static uint32_t cue_seen_seq = 0; // last request either sink acted on
static bool cue_registered = false;

// The claim is the only place the two sinks can collide, and it is taken once per block
// (mixer) or once per loop (pump) — never per sample — so a critical section is
// affordable here even on the audio path. The payload is read under the same lock, so a
// torn request is impossible.
static portMUX_TYPE cue_req_mux = portMUX_INITIALIZER_UNLOCKED;

static inline bool cue_request_pending() { return cue_seq != cue_seen_seq; }

static bool cue_claim(uint8_t *kind, uint8_t *param)
{
    bool found = false;
    portENTER_CRITICAL(&cue_req_mux);
    uint32_t seq = cue_seq;
    if (seq != cue_seen_seq)
    {
        cue_seen_seq = seq;
        *kind = cue_req_kind;
        *param = cue_req_param;
        found = true;
    }
    portEXIT_CRITICAL(&cue_req_mux);
    return found;
}

// Starts (or replaces) the pending cue. Safe to call from the loop task or from an event
// callback: it stores a payload, bumps one word and never blocks.
void cue_play(CueKind kind, uint8_t param)
{
    if (active_mode != MODE_BLUETOOTH)
    {
        // WiFi mode: state cues are played where they happen, by cue_play_now(), and a
        // gesture cue has no output path at all (the decoder owns I2S when it runs).
#if BUTTON_TRACE
        Serial.printf("[CUE] %s (no wire in this mode)\n",
                      (kind < CUE_KIND_COUNT) ? cue_patterns[kind].name : "?");
#endif
        return;
    }
    cue_req_kind = (uint8_t)kind;
    cue_req_param = param;
    cue_seq++; // a sink picks this up on its next block / loop
}

// Convenience for the volume ramp: one short tick whose pitch rises with the level,
// so the ramp reports itself out loud even when the level change is inaudible.
void cue_volume_tick(uint8_t percent)
{
    cue_play(CUE_VOLUME_TICK, percent);
}

// True when cues can be mixed into a live stream (Bluetooth mode, mixer registered).
bool cues_available() { return cue_registered; }

// ---------------------------------------------------------------------------
// Sink 1 — mixing into the live Bluetooth stream
// ---------------------------------------------------------------------------

// Called from the A2DP stream reader, AFTER volume control, before I2S.
//
// `data` is interleaved 16-bit PCM. The signature is const because the library's
// hook is declared that way, but the buffer is the very one it edits in place a few
// lines earlier for the channel swap and the volume scaling — casting the const
// away is what the library itself does at that call site.
void cue_mix(const uint8_t *data, uint32_t len)
{
    uint32_t rate = a2dp_sink.sample_rate();
    uint16_t channels = a2dp_sink.channels();
    if (rate == 0)
        rate = CUE_WIRE_RATE;
    if (channels == 0)
        channels = 2;
    if (channels > 2)
        channels = 2; // an A2DP sink never gets more; be defensive anyway

    const uint32_t frame_bytes = 2 * channels;
    uint32_t frames = len / frame_bytes;

    // Pick up a newly requested cue. A block that arrives while the request is already
    // playing (the volume tick, the paired chime) is exactly when this runs.
    if (cue_request_pending())
    {
        uint8_t kind = 0, param = 0;
        if (cue_claim(&kind, &param))
            cue_voice_start(cue_mix_voice, kind, param, rate);
    }

    if (cue_voice_idle(cue_mix_voice))
        return; // idle: one comparison per block, nothing else

    int16_t *pcm = (int16_t *)(void *)data;

    for (uint32_t f = 0; f < frames; f++)
    {
        int16_t s = cue_voice_render(cue_mix_voice);
        if (s == 0)
            continue;

        for (uint16_t c = 0; c < channels; c++)
        {
            int32_t mixed = (int32_t)pcm[f * channels + c] + (int32_t)s;
            if (mixed > 32767)
                mixed = 32767;
            if (mixed < -32768)
                mixed = -32768;
            pcm[f * channels + c] = (int16_t)mixed;
        }
    }
}

// ---------------------------------------------------------------------------
// Sink 2 — writing the same cues straight to the I2S wire
// ---------------------------------------------------------------------------

// True when no one else is feeding the DAC, i.e. when a cue may write to it.
//
// Bluetooth: the gate is the AUDIO state, not the connection state. A connected source
// that has not started sending leaves the DMA empty, and that window is the only chance
// the "paired" cue will ever get — handle_connection_state() has already published
// CONNECTED by the time our callback runs. Once the phone streams, audio_data_callback()
// owns the wire and a second writer would interleave garbage.
//
// WiFi: the decoder is the only other writer, and it is absent until the portal creates
// it.
static bool cue_wire_free()
{
    if (active_mode == MODE_BLUETOOTH)
        return a2dp_sink.get_audio_state() != ESP_A2D_AUDIO_STATE_STARTED;

    if (player != nullptr && player->isRunning())
        return false;
    return true;
}

// True only while WE installed I2S_NUM_0, i.e. only ever in WiFi mode. In Bluetooth mode
// the sink's driver is borrowed, never owned, and must not be uninstalled.
static bool cue_wire_owned = false;

// Returns the sample rate to render at, or 0 when there is no usable wire.
static uint32_t cue_wire_open()
{
    if (active_mode == MODE_BLUETOOTH)
    {
        // a2dp_sink.start() installed this from boot (44100/16/stereo); sample_rate()
        // follows the negotiated rate once a source applies its own. Nothing else is
        // writing, so borrowing it needs no driver work at all.
        uint32_t rate = a2dp_sink.sample_rate();
        return rate == 0 ? CUE_WIRE_RATE : rate;
    }

    // WiFi mode: the decoder installs I2S_NUM_0 in its constructor, which is lazy, so
    // before the first playback there is no peripheral at all. Install one for the
    // length of this cue. The configuration is the one the A2DP path and the boot beep
    // test both use on this hardware (i2s_output_test.h), and its DMA is 2 KB.
    if (cue_wire_owned)
        return CUE_WIRE_RATE;

    const i2s_config_t cfg = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = CUE_WIRE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = (i2s_comm_format_t)I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = 0,
        .dma_buf_count = 8,
        .dma_buf_len = CUE_WIRE_CHUNK,
        .use_apll = false,
        .tx_desc_auto_clear = true,
        .fixed_mclk = 0};

    const i2s_pin_config_t pins = {
        .mck_io_num = I2S_PIN_NO_CHANGE,
        .bck_io_num = I2C_BCLK,
        .ws_io_num = I2C_LRC,
        .data_out_num = I2C_DOUT,
        .data_in_num = I2S_PIN_NO_CHANGE};

    // Fails when the decoder (or anything else) already holds the port — a cue is never
    // worth fighting for the peripheral, so this is a skip, not an error.
    if (i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL) != ESP_OK)
        return 0;
    if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK)
    {
        i2s_driver_uninstall(I2S_NUM_0);
        return 0;
    }

    cue_wire_owned = true;
    return CUE_WIRE_RATE;
}

static void cue_wire_close(bool flush)
{
    if (!cue_wire_owned)
        return;

    if (flush)
    {
        // The tone that was just written is still in the DMA queue; uninstalling now
        // would cut it mid-cycle (an audible click). Push silence over it first.
        static int16_t tail[CUE_WIRE_CHUNK * 2];
        memset(tail, 0, sizeof(tail));
        for (int i = 0; i < CUE_WIRE_FLUSH_CHUNKS; i++)
        {
            size_t written = 0;
            i2s_write(I2S_NUM_0, tail, sizeof(tail), &written, portMAX_DELAY);
        }
    }

    i2s_zero_dma_buffer(I2S_NUM_0);
    i2s_driver_uninstall(I2S_NUM_0);
    cue_wire_owned = false;
}

// Renders and writes one DMA period, advancing the voice by exactly one period.
//
// The wait is the timeout handed to i2s_write: portMAX_DELAY always delivers the whole
// period, at the cost of blocking ~1.5 ms while the DMA drains; 0 is the pump's top-up
// call, which returns false when the transmit buffer is still full. A PARTIAL write
// (0 < written < size) drops the tail of that period — one period of discontinuity in a
// tone that has already been envelope-shaped, which is inaudible next to the alternative
// of buffering a second copy of the cue just to retry it.
static bool cue_wire_write_chunk(CueVoice &v, TickType_t wait)
{
    static int16_t buf[CUE_WIRE_CHUNK * 2];
    for (int i = 0; i < CUE_WIRE_CHUNK; i++)
    {
        int16_t s = cue_voice_render(v);
        buf[i * 2] = s;
        buf[i * 2 + 1] = s;
    }
    size_t written = 0;
    i2s_write(I2S_NUM_0, buf, sizeof(buf), &written, wait);
    return written == sizeof(buf);
}

// Plays a cue to completion, here and now.
//
// This is the boot / mode-transition entry point: it is called from setup() (no loop()
// running yet, so nothing else could pump the wire) and from the two gestures that go on
// to block or reboot. It returns with the wire released, which is what makes the ordering
// safe — a mode switch cannot install the A2DP sink's driver halfway through a cue that
// still thinks it owns the peripheral.
bool cue_play_now(CueKind kind)
{
    const char *name = (kind < CUE_KIND_COUNT) ? cue_patterns[kind].name : "?";

    if (!cue_wire_free())
    {
        Serial.printf("[CUE] %s skipped — the audio path is busy\n", name);
        return false;
    }

    uint32_t rate = cue_wire_open();
    if (rate == 0)
    {
        Serial.printf("[CUE] %s skipped — no I2S wire available\n", name);
        return false;
    }

    cue_voice_start(cue_wire_voice, (uint8_t)kind, 0, rate);

    uint32_t deadline = millis() + CUE_WIRE_MAX_MS;
    while (!cue_voice_idle(cue_wire_voice))
    {
        cue_wire_write_chunk(cue_wire_voice, portMAX_DELAY);
        if ((int32_t)(millis() - deadline) >= 0)
        {
            Serial.printf("[CUE] %s cut short (wire timeout)\n", name);
            cue_voice_stop(cue_wire_voice);
            break;
        }
    }

    cue_wire_close(true);
    Serial.printf("[CUE] %s (%u Hz on the wire)\n", name, (unsigned)rate);
    return true;
}

// Pumps the wire path from loop(). At most one transmit buffer of audio per call.
//
// Bluetooth mode only, by design: the states a WiFi-mode cue reports are boot states, and
// installing/uninstalling the peripheral on every volume-ramp tick (150 ms) to say nothing
// useful would be worse than silence.
void cue_wire_pump()
{
    if (active_mode != MODE_BLUETOOTH)
        return;

    if (cue_voice_idle(cue_wire_voice))
    {
        // Leave the request in the slot when the wire is busy: the mixer will claim it on
        // the next audio block, which is the one case where a cue can still be heard.
        if (!cue_wire_free())
            return;
        if (!cue_request_pending())
            return;

        uint8_t kind = 0, param = 0;
        if (!cue_claim(&kind, &param))
            return;

        uint32_t rate = cue_wire_open();
        if (rate == 0)
            return;
        cue_voice_start(cue_wire_voice, kind, param, rate);
    }
    else if (!cue_wire_free())
    {
        // The source started streaming under the cue. Drop the tail instead of fighting
        // the A2DP writer for the DMA: the stream is what the user actually pressed play
        // for, and half-mixed tone over interleaved writes would be noise.
        cue_voice_stop(cue_wire_voice);
        return;
    }

    // One period blocks for at most ~1.5 ms. The top-up loop then fills whatever the DMA
    // is still hungry for, without waiting: this is what keeps a cue running at real
    // speed when loop() itself is slow (a 144-LED show() blocks ~4 ms, i.e. three periods
    // per pass), instead of letting the transmit buffer run dry between passes and
    // stuttering the tone.
    if (!cue_wire_write_chunk(cue_wire_voice, pdMS_TO_TICKS(20)))
        return;
    for (int i = 0; i < CUE_WIRE_CATCHUP && !cue_voice_idle(cue_wire_voice); i++)
    {
        if (!cue_wire_write_chunk(cue_wire_voice, 0))
            return;
    }
}

// Bridge from the A2DP sink's connection events to a cue.
//
// Runs on the Bluetooth application task, so it only posts a request — it must not block
// (a tone takes ~300 ms, and this task is what completes the A2DP handshake). The pump in
// loop() plays it a few milliseconds later.
void cue_on_bt_connection_state(esp_a2d_connection_state_t state, void *obj)
{
    (void)obj;
    switch (state)
    {
    case ESP_A2D_CONNECTION_STATE_CONNECTED:
        Serial.println("[CUE] Bluetooth source connected");
        cue_play(CUE_BT_PAIRED);
        break;
    case ESP_A2D_CONNECTION_STATE_DISCONNECTED:
        // Deliberately silent: the owner asked for ONE "waiting" announcement at boot,
        // and auto-reconnect cycles through DISCONNECTED up to three times, which would
        // turn a status cue into a stutter.
        Serial.println("[CUE] Bluetooth source disconnected (no cue, by design)");
        break;
    default:
        break;
    }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

// Registers the stream mixer. Bluetooth mode only: in local playback there is no stream to
// mix into (and I2S_NUM_0 belongs to the decoder), so this deliberately does nothing
// there rather than pretending.
//
// Runs from controls_begin(), i.e. AFTER a mode's own setup — which is fine and intended:
// the boot state cue (CUE_BT_WAIT) is played by cue_play_now() from setup_bluetooth_mode(),
// and it needs only the driver the sink already installed.
void cues_begin()
{
    cue_build_sine();
    if (active_mode != MODE_BLUETOOTH)
    {
        Serial.println("[CUE] Local playback mode — stream mixer off (the strip answers instead)");
        return;
    }

    // is_output MUST be true: this overload doubles as the "am I the I2S writer"
    // switch, and false would silence the DAC.
    a2dp_sink.set_stream_reader(cue_mix, true);
    cue_registered = true;
    Serial.println("[CUE] Mixer registered post-volume (cues are volume-independent)");
}

#endif // SERVICE_AUDIO_CUES
