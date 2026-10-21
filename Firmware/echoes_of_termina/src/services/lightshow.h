#if !defined(SERVICE_LIGHTSHOW)
#define SERVICE_LIGHTSHOW

//? ------------------------------------------------------------------------------------------
//*! Echoes lightshow engine — plays .echoes files synced to the audio playhead.
//*
//*  File format (one command per line), matching the Echoes editor
//*  (Executable/echoes/core/script_engine.py):
//*    M:SS[.mmm]  SET  R G B                  LED-SPEC
//*    M:SS[.mmm]  RAMP DURATION R0 G0 B0 R1 G1 B1  LED-SPEC
//*    M:SS[.mmm]  FADE R G B PERCENT           LED-SPEC
//*
//*  LED-SPEC: single "5", range "1-5", list "1,3,5", or wildcard "*".
//*
//*  TIME ORDER IS PART OF THE FORMAT: timestamps must be globally
//*  non-decreasing over the whole file. The editor merges its tracks on export,
//*  so a multi-track project arrives here already interleaved. Track headers
//*  ([Name]) and metadata (# @key value) lines are skipped, and only survive as
//*  comments for readability.
//*
//*  Why that matters: the parser streams a single cursor through the file and
//*  fires every command whose timestamp has passed. A legacy per-track file
//*  stores one whole track per block, so once the first block is exhausted
//*  every remaining command looks "due" and thousands of them fire at once.
//*  load_lightshow() validates the file up front and refuses an unsorted one.
//*
//*  Memory: commands are STREAMED from the SD card as the playhead advances
//*  (the ESP32 has too little RAM to hold a full show alongside the audio
//*  decoder + WiFi). Only one command lives in RAM at a time.
//*
//*  Timing: the engine follows an EXTERNAL playhead fed by the audio player
//*  (lightshow_set_playhead_ms) so the show stays locked to the song — pause
//*  freezes it, stop clears it, seeks re-sync it.
//*
//*  Frame rate: derived from the strip length, not fixed. Driving the strip costs
//*  ~29 us per LED (Adafruit's ESP32 path blocks for the whole RMT transfer), so a
//*  single frame period cannot suit both a 28-LED mask and a 200-LED strip. See
//*  lightshow_frame_period_ms(). FADEs decay by REAL elapsed time, so how fast a
//*  fade fades does not depend on that period.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Frame period. A WS2812 frame costs 24 bits x 1.2 us = 28.8 us per LED plus
// ~0.34 ms of fixed overhead (reset/latch and the RMT refill handshake). That
// reproduces the figure the engine was originally tuned around: 28 LEDs ->
// 28*0.0288 + 0.34 = 1.15 ms. The 2.5x margin keeps show() under ~40% duty so the
// rest of the loop still has time for the decoder.
//
// Yields exactly the old 5 ms at 28 LEDs (so existing mask shows are unchanged),
// and stretches only as the strip grows: 8 ms at 96, 11 ms at 144, 15 ms at 200
// LEDs — still ~66 fps at the top of the range.
#define LIGHTSHOW_LED_US 28.8f
#define LIGHTSHOW_SHOW_OVERHEAD_MS 0.34f
#define LIGHTSHOW_MIN_TIMESTEP_MS 5
#define LIGHTSHOW_DUTY_MARGIN 2.5f

// Not static: services/ui_feedback.h is included above this file (the renderers
// yield to the overlay, so the overlay has to be declared first) and paces its own
// frames with this. Same translation unit, so this is the only caller-facing change.
uint16_t lightshow_frame_period_ms()
{
    float show_ms = LIGHTSHOW_SHOW_OVERHEAD_MS +
                    LIGHTSHOW_LED_US * (float)majora_led_count / 1000.0f;
    uint16_t period = (uint16_t)(show_ms * LIGHTSHOW_DUTY_MARGIN + 0.5f);
    return period < LIGHTSHOW_MIN_TIMESTEP_MS ? LIGHTSHOW_MIN_TIMESTEP_MS : period;
}

// A stall longer than this is not a frame; decaying by the full gap would nuke
// every fade at once. Seeks are handled separately by the playhead re-sync.
#define LIGHTSHOW_MAX_FRAME_DT_S 0.5f

#define MAX_ADDR_PER_INSTR 8     // explicit addresses per line (wildcard uses the all flag)
#define LIGHTSHOW_LINE_LEN 160   // max parsed line length (long lines are truncated)
// How far a timestamp may go backwards before the file counts as unsorted.
// The export is an exact merge, so this only absorbs hand-edited jitter; the
// legacy per-track layout breaks order by whole tracks (seconds), not by ms.
#define LIGHTSHOW_BACKSTEP_TOLERANCE_MS 1000

#define INSTR_SET 0
#define INSTR_FADE 1
#define INSTR_RAMP 2

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

typedef struct
{
    uint32_t time_ms;                 // absolute playback time in ms
    uint8_t type;                     // INSTR_SET / INSTR_FADE / INSTR_RAMP
    uint8_t r, g, b;                  // SET / FADE target colour
    uint8_t fade;                     // FADE decay % per second
    uint8_t r0, g0, b0, r1, g1, b1;   // RAMP start / end colours
    uint32_t dur_ms;                  // RAMP duration
    bool all;                         // wildcard: apply to every LED
    uint8_t addr[MAX_ADDR_PER_INSTR]; // 0-indexed LED addresses
    uint8_t addr_count;
} lightshow_instr_t;

typedef struct
{
    float red = 0.0f, green = 0.0f, blue = 0.0f;
    // Per-SECOND colour retention (1.0 = no decay, used by SET and RAMP). The
    // per-frame decay factor is derived from this and the real elapsed time in
    // run_lightshow(). Storing a per-frame factor here instead — as this used to —
    // ties every FADE's speed to the frame period, which is now strip-dependent.
    float retention = 1.0f;
    bool  ramp_active = false;
    float ramp_r0 = 0.0f, ramp_g0 = 0.0f, ramp_b0 = 0.0f;
    float ramp_r1 = 0.0f, ramp_g1 = 0.0f, ramp_b1 = 0.0f;
    uint32_t ramp_start_ms = 0, ramp_end_ms = 0;
} wellerman_pixel_t;

// ---------------------------------------------------------------------------
// State (file-scoped)
// ---------------------------------------------------------------------------

static File show_file;                     // open .echoes file (streamed)
static lightshow_instr_t show_next;        // the next not-yet-fired command
static bool show_has_next = false;
static uint32_t show_fired_count = 0;      // commands fired so far (diagnostics)
static uint16_t show_current_index = 0;    // placeholder kept for API symmetry
static uint32_t show_playhead_ms = 0;
static uint32_t show_last_pos_ms = 0;
static bool show_playing = false;
// Timestamp of the last rendered frame. File-scope (not function-static) so that
// lightshow_play() can anchor it: otherwise the first frame of a new show would
// measure its dt from the PREVIOUS show's last frame — clamped to the 0.5 s
// ceiling, which would over-decay a FADE fired on that very first frame.
static uint32_t show_last_frame_ms = 0;
// Per-LED working buffer, allocated LAZILY by lightshow_play() and released by
// lightshow_stop().
//
// Sized for MAJORA_MAX_LEDS it is ~10.4 KB, which is why it is not a static
// array: a .echoes show can only ever run when the SD player is up, so in
// Bluetooth mode — where the reactive envelope/FFT animations need the RAM — a
// never-used show buffer must not be resident. Measured cost of the static
// version: +8.9 KB of .bss at boot, in every mode.
static wellerman_pixel_t *w_pixels = nullptr;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

// Reads one text line (no trailing CR/LF) into *buf* without heap allocation.
// Returns true if a line (possibly empty) was read.
static bool _lightshow_read_line(char *buf, size_t buflen)
{
    size_t i = 0;
    while (show_file.available() && i < buflen - 1)
    {
        int c = show_file.read();
        if (c == '\n')
            break;
        if (c != '\r')
            buf[i++] = (char)c;
    }
    buf[i] = '\0';
    return i > 0 || show_file.available();
}

// Parses "M:SS[.mmm]" into milliseconds
static uint32_t _lightshow_parse_time_ms(const char *s)
{
    char *end;
    uint32_t min = strtoul(s, &end, 10);
    if (*end != ':')
        return 0;
    uint32_t sec = strtoul(end + 1, &end, 10);
    uint32_t ms = 0;
    if (*end == '.')
    {
        const char *ms_start = end + 1;
        ms = strtoul(ms_start, &end, 10);
        // Normalise to exactly 3 digits (milliseconds)
        size_t digits = (size_t)(end - ms_start);
        while (digits < 3)
        {
            ms *= 10;
            digits++;
        }
        while (digits > 3)
        {
            ms /= 10;
            digits--;
        }
    }
    return min * 60000UL + sec * 1000UL + ms;
}

// Parses an LED-SPEC token into 0-indexed addresses; returns the count.
// Supports comma-separated values ("3,12"), inclusive dash ranges ("7-8"),
// mixed forms ("3,7-9,12") and the wildcard "*" (sets out->all).
//
// Addresses beyond the configured strip length are dropped rather than clamped: a
// show authored for a 200-LED strip played on a 28-LED one simply does not reach
// the missing LEDs. The wildcard is the case that must NOT be filtered here — it
// is expanded against majora_led_count at apply time, so "*" always covers exactly
// the strip that is attached.
static uint8_t _lightshow_parse_addrs(const char *addr_str, lightshow_instr_t *out)
{
    if (strcmp(addr_str, "*") == 0)
    {
        out->all = true;
        out->addr_count = 0;
        return 0;
    }
    out->all = false;

    uint8_t count = 0;
    char buf[64];
    strncpy(buf, addr_str, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char *tok = strtok(buf, ",");
    while (tok && count < MAX_ADDR_PER_INSTR)
    {
        char *dash = strchr(tok, '-');
        if (dash)
        {
            // Range notation: "7-8" -> physical LEDs 7 through 8 (1-indexed)
            int first = atoi(tok);
            int last = atoi(dash + 1);
            if (first > last)
            {
                int tmp = first;
                first = last;
                last = tmp;
            }
            for (int i = first; i <= last && count < MAX_ADDR_PER_INSTR; i++)
            {
                int idx = i - 1; // 1-indexed -> 0-indexed
                if (idx >= 0 && idx < (int)majora_led_count)
                    out->addr[count++] = (uint8_t)idx;
            }
        }
        else
        {
            int addr = atoi(tok) - 1; // 1-indexed file -> 0-indexed array
            if (addr >= 0 && addr < (int)majora_led_count)
                out->addr[count++] = (uint8_t)addr;
        }
        tok = strtok(NULL, ",");
    }
    out->addr_count = count;
    return count;
}

// Parses a single text line into a lightshow_instr_t.
// Returns true if the line is a valid command.
static bool _lightshow_parse_line(const char *line, lightshow_instr_t *out)
{
    char buf[LIGHTSHOW_LINE_LEN];
    strncpy(buf, line, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    // Skip blank lines, comments and track-section headers
    const char *p = buf;
    while (*p == ' ' || *p == '\t')
        p++;
    if (*p == '\0' || *p == '#' || *p == '/' || *p == '-' || *p == '<' ||
        *p == '*' || *p == '[')
        return false;

    char work[LIGHTSHOW_LINE_LEN];
    strncpy(work, p, sizeof(work) - 1);
    work[sizeof(work) - 1] = '\0';

    char *time_tok = strtok(work, " \t");
    if (!time_tok)
        return false;
    char *type_tok = strtok(NULL, " \t");
    if (!type_tok)
        return false;

    if (strcmp(type_tok, "SET") == 0)
        out->type = INSTR_SET;
    else if (strcmp(type_tok, "FADE") == 0)
        out->type = INSTR_FADE;
    else if (strcmp(type_tok, "RAMP") == 0)
        out->type = INSTR_RAMP;
    else
        return false;

    if (out->type == INSTR_RAMP)
    {
        // RAMP DURATION R0 G0 B0 R1 G1 B1 LED-SPEC
        char *dur_tok = strtok(NULL, " \t");
        char *r0 = strtok(NULL, " \t");
        char *g0 = strtok(NULL, " \t");
        char *b0 = strtok(NULL, " \t");
        char *r1 = strtok(NULL, " \t");
        char *g1 = strtok(NULL, " \t");
        char *b1 = strtok(NULL, " \t");
        char *addr_tok = strtok(NULL, " \t");
        if (!dur_tok || !r0 || !g0 || !b0 || !r1 || !g1 || !b1 || !addr_tok)
            return false;
        out->dur_ms = _lightshow_parse_time_ms(dur_tok);
        out->r0 = (uint8_t)atoi(r0);
        out->g0 = (uint8_t)atoi(g0);
        out->b0 = (uint8_t)atoi(b0);
        out->r1 = (uint8_t)atoi(r1);
        out->g1 = (uint8_t)atoi(g1);
        out->b1 = (uint8_t)atoi(b1);
        out->r = out->r0;
        out->g = out->g0;
        out->b = out->b0;
        _lightshow_parse_addrs(addr_tok, out);
    }
    else
    {
        // SET R G B LED-SPEC   /   FADE R G B PERCENT LED-SPEC
        char *r_tok = strtok(NULL, " \t");
        char *g_tok = strtok(NULL, " \t");
        char *b_tok = strtok(NULL, " \t");
        if (!r_tok || !g_tok || !b_tok)
            return false;
        out->r = (uint8_t)atoi(r_tok);
        out->g = (uint8_t)atoi(g_tok);
        out->b = (uint8_t)atoi(b_tok);

        char *next_tok = strtok(NULL, " \t");
        if (!next_tok)
            return false;
        if (out->type == INSTR_FADE)
        {
            out->fade = (uint8_t)atoi(next_tok);
            next_tok = strtok(NULL, " \t");
            if (!next_tok)
                return false;
        }
        else
        {
            out->fade = 0;
        }
        _lightshow_parse_addrs(next_tok, out);
    }

    out->time_ms = _lightshow_parse_time_ms(time_tok);
    return out->all || out->addr_count > 0;
}

// Reads ahead to the next valid command; stores it in show_next.
static void _lightshow_advance()
{
    char line[LIGHTSHOW_LINE_LEN];
    while (_lightshow_read_line(line, sizeof(line)))
    {
        if (_lightshow_parse_line(line, &show_next))
        {
            show_has_next = true;
            return;
        }
    }
    show_has_next = false;
}

// Rewinds the stream and primes show_next with the first command.
static void _lightshow_rewind()
{
    show_file.seek(0);
    show_fired_count = 0;
    _lightshow_advance();
}

// Reads the whole file once to check the format invariant the streamed parser
// depends on: timestamps must be non-decreasing from start to end.
//
// A legacy per-track file (one contiguous block per [track]) violates this at
// every block boundary, and the parser would answer the jump by firing the rest
// of the file in one burst. Better to refuse it at load and say why.
//
// Costs one full pass over the file (a 135 KB show is ~40 ms at the measured
// ~3.4 MB/s), which is paid once, at load, not per frame.
static bool _lightshow_validate(uint32_t *cmd_count, uint16_t *track_count, uint32_t *last_ms)
{
    char line[LIGHTSHOW_LINE_LEN];
    lightshow_instr_t ins;
    uint32_t count = 0;
    uint32_t prev_ms = 0;
    uint32_t total_ms = 0;
    uint16_t tracks = 0;
    bool have_prev = false;
    bool sorted = true;

    show_file.seek(0);
    while (_lightshow_read_line(line, sizeof(line)))
    {
        const char *p = line;
        while (*p == ' ' || *p == '\t')
            p++;
        if (*p == '[')
        {
            tracks++;
            continue;
        }
        if (!_lightshow_parse_line(line, &ins))
            continue;

        if (have_prev && ins.time_ms + LIGHTSHOW_BACKSTEP_TOLERANCE_MS < prev_ms)
        {
            Serial.printf("[Lightshow] NOT time-sorted at command %u: %u ms follows %u ms\n",
                          count + 1, ins.time_ms, prev_ms);
            sorted = false;
            break;
        }
        prev_ms = ins.time_ms;
        total_ms = ins.time_ms;
        have_prev = true;
        count++;
    }
    show_file.seek(0);

    if (cmd_count)
        *cmd_count = count;
    if (track_count)
        *track_count = tracks;
    if (last_ms)
        *last_ms = total_ms;
    return sorted;
}

// Applies a single command to the w_pixels working buffer.
// *scale* (0..1) dims SET/FADE colours — used when re-syncing a FADE that
// started before the current playhead position.
static void _lightshow_apply_instr(const lightshow_instr_t *instr, float scale = 1.0f)
{
    if (w_pixels == nullptr)
        return;

    // Per-second retention (see wellerman_pixel_t). SET and RAMP do not decay.
    float retention = 1.0f;
    if (instr->type == INSTR_FADE)
    {
        // Exponential decay: per-second retention = (100 - fade%) / 100
        retention = (100.0f - (float)instr->fade) / 100.0f;
    }

    uint8_t count = instr->all ? (uint8_t)majora_led_count : instr->addr_count;
    for (uint8_t a = 0; a < count; a++)
    {
        uint8_t idx = instr->all ? a : instr->addr[a];
        if (idx >= majora_led_count)
            continue;
        wellerman_pixel_t &px = w_pixels[idx];

        if (instr->type == INSTR_RAMP)
        {
            px.red = instr->r0;
            px.green = instr->g0;
            px.blue = instr->b0;
            px.ramp_r0 = instr->r0;
            px.ramp_g0 = instr->g0;
            px.ramp_b0 = instr->b0;
            px.ramp_r1 = instr->r1;
            px.ramp_g1 = instr->g1;
            px.ramp_b1 = instr->b1;
            px.ramp_start_ms = instr->time_ms;
            px.ramp_end_ms = instr->time_ms + instr->dur_ms;
            px.ramp_active = true;
            px.retention = 1.0f;
        }
        else
        {
            px.red = (float)instr->r * scale;
            px.green = (float)instr->g * scale;
            px.blue = (float)instr->b * scale;
            px.retention = retention;
            px.ramp_active = false;
        }
    }
}

// Re-syncs the engine to an arbitrary playhead position (after a seek).
// Rewinds the stream, re-applies every command that should be active, and
// primes the next command after *ms*.
static void _lightshow_resync(uint32_t ms)
{
    if (w_pixels == nullptr)
        return;

    // The whole buffer, not just the configured count: the strip length can change
    // at runtime, and a stale entry beyond the old count would light up on growth.
    for (int i = 0; i < MAJORA_MAX_LEDS; i++)
        w_pixels[i] = wellerman_pixel_t();

    show_file.seek(0);
    show_fired_count = 0;

    char line[LIGHTSHOW_LINE_LEN];
    while (_lightshow_read_line(line, sizeof(line)))
    {
        lightshow_instr_t ins;
        if (!_lightshow_parse_line(line, &ins))
            continue;
        if (ins.time_ms > ms)
        {
            show_next = ins;
            show_has_next = true;
            return;
        }
        show_fired_count++;

        if (ins.type == INSTR_FADE && ms > ins.time_ms)
        {
            // Approximate the decayed brightness at the current position
            float retention_per_sec = (100.0f - (float)ins.fade) / 100.0f;
            float k = powf(retention_per_sec, (float)(ms - ins.time_ms) / 1000.0f);
            _lightshow_apply_instr(&ins, k);
        }
        else
        {
            _lightshow_apply_instr(&ins);
        }
    }
    show_has_next = false;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

// Opens a .echoes file on the SD card for streaming. Call once before
// lightshow_play(). Returns true if at least one command was found.
bool load_lightshow(const char *filepath)
{
    if (!sdcard)
    {
        Serial.println("[Lightshow] No SD card — cannot load lightshow");
        return false;
    }
    if (show_file)
        show_file.close();

    show_file = SD_MMC.open(filepath, "r");
    if (!show_file)
    {
        Serial.printf("[Lightshow] Cannot open %s\n", filepath);
        return false;
    }

    // Validate before playing: an unsorted file would dump its remaining
    // commands in one burst mid-song (see the format note at the top).
    uint32_t cmd_count = 0, last_ms = 0;
    uint16_t track_count = 0;
    if (!_lightshow_validate(&cmd_count, &track_count, &last_ms))
    {
        Serial.println("[Lightshow] Refusing to play: re-export the project so the tracks are merged in time order");
        show_file.close();
        show_has_next = false;
        return false;
    }
    Serial.printf("[Lightshow] %s: %u commands, %u track header(s), last at %u ms\n",
                  filepath, (unsigned)cmd_count, (unsigned)track_count, (unsigned)last_ms);

    _lightshow_rewind();
    Serial.printf("[Lightshow] Streaming %s  (first command at %u ms, heap %u KB free)\n",
                  filepath,
                  show_has_next ? show_next.time_ms : 0,
                  (unsigned)(esp_get_free_heap_size() / 1024));
    return show_has_next;
}

// Starts playback, following the external playhead (audio position).
void lightshow_play()
{
    if (w_pixels == nullptr)
    {
        w_pixels = (wellerman_pixel_t *)malloc(MAJORA_MAX_LEDS * sizeof(wellerman_pixel_t));
        if (w_pixels == nullptr)
        {
            Serial.printf("[Lightshow] Cannot allocate the pixel buffer (%u bytes) — %u KB free\n",
                          (unsigned)(MAJORA_MAX_LEDS * sizeof(wellerman_pixel_t)),
                          (unsigned)(ESP.getFreeHeap() / 1024));
            return; // leave show_playing false: the engine never ran
        }
        Serial.printf("[Lightshow] Pixel buffer allocated: %u bytes for up to %u LEDs\n",
                      (unsigned)(MAJORA_MAX_LEDS * sizeof(wellerman_pixel_t)),
                      (unsigned)MAJORA_MAX_LEDS);
    }

    for (int i = 0; i < MAJORA_MAX_LEDS; i++)
        w_pixels[i] = wellerman_pixel_t();
    show_playhead_ms = 0;
    show_last_pos_ms = 0;
    show_fired_count = 0;
    show_current_index = 0;
    // Anchor the frame clock to now, so the first frame's dt is one loop
    // iteration rather than however long ago the last show ended.
    show_last_frame_ms = millis();
    _lightshow_rewind();
    show_playing = true;
    Serial.printf("[Lightshow] Playback attached (first command at %u ms)\n",
                  show_has_next ? show_next.time_ms : 0);
}

// Stops playback, closes the stream and clears the strip.
void lightshow_stop()
{
    if (!show_playing && !show_file)
        return;
    show_playing = false;
    if (show_file)
    {
        show_file.close();
        show_file = File();
    }
    show_has_next = false;

    // Release the pixel buffer so the RAM is back for the reactive animations
    // (and for the audio path). _sync_lightshow() does NOT stop between tracks
    // that both have shows — it goes straight to load + play — so this does not
    // churn a 10.4 KB malloc on every track change.
    if (w_pixels != nullptr)
    {
        free(w_pixels);
        w_pixels = nullptr;
    }

    for (int i = 0; i < (int)majora_led_count; i++)
        majora.setPixelColor(i, 0);
    majora.show();
    Serial.println("[Lightshow] Playback stopped");
}

bool is_lightshow_playing() { return show_playing; }
uint16_t lightshow_loaded_count() { return show_fired_count; }

// Feeds the engine with the current audio position (ms).
// Detects seeks and re-syncs the show when the playhead jumps.
void lightshow_set_playhead_ms(uint32_t ms)
{
    if (!show_playing)
        return;
    // A backward move, or a forward jump beyond 150 ms, is a seek
    if (ms + 150 < show_last_pos_ms || ms > show_last_pos_ms + 150)
        _lightshow_resync(ms);
    show_playhead_ms = ms;
    show_last_pos_ms = ms;
}

// One-entry memo for the fade decay factor. Consecutive fading pixels almost
// always share a FADE percentage and every pixel shares the frame's dt, so this
// turns a powf per pixel per frame into one per frame — worth having because the
// cost would otherwise scale with the strip length (200 LEDs at 66 fps).
static float _lightshow_decay(float retention, float dt_s)
{
    static float c_ret = -1.0f, c_dt = -1.0f, c_decay = 1.0f;
    if (retention != c_ret || dt_s != c_dt)
    {
        c_ret = retention;
        c_dt = dt_s;
        c_decay = powf(retention, dt_s);
    }
    return c_decay;
}

// Frame update — call every loop iteration.
// Fires due commands, advances ramps, decays fades, pushes to the strip.
void run_lightshow()
{
    if (!show_playing || w_pixels == nullptr)
        return;

    // The UI overlay owns the strip while it reports a gesture
    // (services/ui_feedback.h). Skip drawing, but keep the show's own state
    // advancing: the playhead and the decay are driven from the audio position and
    // millis(), so the show is exactly where it should be when the overlay lets go.
    if (ui_feedback_active())
    {
        show_last_frame_ms = millis();
        return;
    }

    uint16_t period = lightshow_frame_period_ms();
    uint32_t now = millis();
    uint32_t since = now - show_last_frame_ms;
    if (since < period)
        return;
    show_last_frame_ms = now;

    // Decay by the time that ACTUALLY passed, not the nominal period. The loop can
    // overrun (a long SD read, an HTTP request, a stall), and using the nominal
    // period would make fades drift slow exactly when the system is busy.
    float dt_s = (float)since / 1000.0f;
    if (dt_s > LIGHTSHOW_MAX_FRAME_DT_S)
        dt_s = LIGHTSHOW_MAX_FRAME_DT_S;

    uint32_t elapsed = show_playhead_ms;

    // 1 Hz heartbeat — proves the engine is alive and shows where it is
    static uint32_t last_beat = 0xFFFFFFFFUL;
    if (elapsed / 1000 != last_beat)
    {
        last_beat = elapsed / 1000;
        Serial.printf("[Lightshow] t=%u ms  fired=%u  next=%u ms  period=%u ms  leds=%u\n",
                      elapsed, show_fired_count,
                      show_has_next ? show_next.time_ms : 0,
                      (unsigned)period, (unsigned)majora_led_count);
    }

    // Fire all commands whose timestamp has passed (streamed from the SD)
    while (show_has_next && show_next.time_ms <= elapsed)
    {
        _lightshow_apply_instr(&show_next);
        show_fired_count++;
        show_current_index++;
        _lightshow_advance();
    }

    // Update pixels: lerp active ramps, decay fades, keep SETs
    for (int i = 0; i < (int)majora_led_count; i++)
    {
        wellerman_pixel_t &px = w_pixels[i];

        if (px.ramp_active)
        {
            if (elapsed >= px.ramp_end_ms)
            {
                px.red = px.ramp_r1;
                px.green = px.ramp_g1;
                px.blue = px.ramp_b1;
                px.ramp_active = false;
                px.retention = 1.0f;
            }
            else
            {
                float span = (float)(px.ramp_end_ms - px.ramp_start_ms);
                float t = span > 0 ? (float)(elapsed - px.ramp_start_ms) / span : 1.0f;
                if (t < 0.0f)
                    t = 0.0f;
                px.red = px.ramp_r0 + (px.ramp_r1 - px.ramp_r0) * t;
                px.green = px.ramp_g0 + (px.ramp_g1 - px.ramp_g0) * t;
                px.blue = px.ramp_b0 + (px.ramp_b1 - px.ramp_b0) * t;
            }
        }
        else if (px.retention < 1.0f &&
                 (px.red > 0.5f || px.green > 0.5f || px.blue > 0.5f))
        {
            // FADE: decay by real elapsed time. SETs and finished RAMPs carry
            // retention == 1.0 and are skipped entirely (the old code multiplied
            // them by a 1.0 factor every frame).
            float decay = _lightshow_decay(px.retention, dt_s);
            px.red *= decay;
            px.green *= decay;
            px.blue *= decay;
        }

        majora.setPixelColor(i, majora.Color(
                                     (uint8_t)px.red,
                                     (uint8_t)px.green,
                                     (uint8_t)px.blue));
    }
    majora.show();
}

#endif // SERVICE_LIGHTSHOW
