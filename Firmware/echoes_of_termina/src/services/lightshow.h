#if !defined(SERVICE_LIGHTSHOW)
#define SERVICE_LIGHTSHOW

//? ------------------------------------------------------------------------------------------
//*! Wellerman lightshow engine
//*  Parses and plays back timed LED instruction files stored on LittleFS.
//*
//*  File format (one instruction per line):
//*    M:SS.mmm  SET  R G B        addr,addr,...
//*    M:SS.mmm  FADE R G B fade%  addr,addr,...
//*
//*  SET  : turns LED(s) on at the given color, no decay (hodl = 1.0)
//*  FADE : turns LED(s) on at the given color with exponential decay.
//*         fade% is the percentage of brightness lost per second.
//*         e.g. FADE 255 0 0 80  -> red decays to 20% after 1 second.
//*
//*  Addresses are 1-indexed in the file (matching the physical label),
//*  converted to 0-indexed internally.
//*
//*  Timing: WS2812B @ 800 kHz, 28 LEDs = 840 µs data + 300 µs reset ≈ 1.15 ms.
//*  Timestep is set to 5 ms (200 fps) for comfortable real-time margin.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

#define LIGHTSHOW_TIMESTEP_MS 5 // Frame period in ms (must be > ~1.15 ms for 28 LEDs)
#define MAX_INSTRUCTIONS 1024   // Max instructions loaded into RAM (~20 KB)
#define MAX_ADDR_PER_INSTR 8    // Max LED addresses per single instruction line

#define INSTR_SET 0
#define INSTR_FADE 1

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

typedef struct
{
    uint32_t time_ms;                 // Absolute playback time in ms
    uint8_t type;                     // INSTR_SET or INSTR_FADE
    uint8_t r, g, b;                  // Target color
    uint8_t fade;                     // Decay rate % per second (FADE only)
    uint8_t addr[MAX_ADDR_PER_INSTR]; // 0-indexed LED addresses
    uint8_t addr_count;
} lightshow_instr_t;

typedef struct
{
    float red = 0.0f;
    float green = 0.0f;
    float blue = 0.0f;
    float hodl = 1.0f; // Per-frame brightness retention factor (0.0 – 1.0)
} wellerman_pixel_t;

// ---------------------------------------------------------------------------
// State (file-scoped)
// ---------------------------------------------------------------------------

static lightshow_instr_t show_instructions[MAX_INSTRUCTIONS];
static uint16_t show_instruction_count = 0;
static uint16_t show_current_index = 0;
static uint32_t show_start_ms = 0;
static bool show_playing = false;
static wellerman_pixel_t w_pixels[MAJORA_LEDS];

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

// Parses "M:SS.mmm" into milliseconds
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

// Parses address tokens into a 0-indexed address array; returns count.
// Supports comma-separated values ("3,12") and inclusive dash ranges ("7-8").
// Both notations may be mixed: "3,7-9,12"
static uint8_t _lightshow_parse_addrs(const char *addr_str, uint8_t *out)
{
    uint8_t count = 0;
    char buf[48];
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
                if (idx >= 0 && idx < MAJORA_LEDS)
                    out[count++] = (uint8_t)idx;
            }
        }
        else
        {
            int addr = atoi(tok) - 1; // 1-indexed file -> 0-indexed array
            if (addr >= 0 && addr < MAJORA_LEDS)
                out[count++] = (uint8_t)addr;
        }
        tok = strtok(NULL, ",");
    }
    return count;
}

// Parses a single text line into a lightshow_instr_t.
// Returns true if the line is a valid instruction.
static bool _lightshow_parse_line(const char *line, lightshow_instr_t *out)
{
    char buf[128];
    strncpy(buf, line, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    // Strip trailing CR/LF
    for (char *p = buf; *p; p++)
        if (*p == '\r' || *p == '\n')
        {
            *p = '\0';
            break;
        }

    // Skip blank lines and comments
    const char *p = buf;
    while (*p == ' ' || *p == '\t')
        p++;
    if (*p == '\0' || *p == '#' || *p == '/' || *p == '-' || *p == '<' || *p == '*')
        return false;

    // Make a mutable copy for strtok (p may point into buf already)
    char work[128];
    strncpy(work, p, sizeof(work) - 1);
    work[sizeof(work) - 1] = '\0';

    // Token: timestamp
    char *time_tok = strtok(work, " \t");
    if (!time_tok)
        return false;

    // Token: instruction type
    char *type_tok = strtok(NULL, " \t");
    if (!type_tok)
        return false;

    if (strcmp(type_tok, "SET") == 0)
        out->type = INSTR_SET;
    else if (strcmp(type_tok, "FADE") == 0)
        out->type = INSTR_FADE;
    else
        return false;

    // Tokens: R G B
    char *r_tok = strtok(NULL, " \t");
    char *g_tok = strtok(NULL, " \t");
    char *b_tok = strtok(NULL, " \t");
    if (!r_tok || !g_tok || !b_tok)
        return false;

    out->r = (uint8_t)atoi(r_tok);
    out->g = (uint8_t)atoi(g_tok);
    out->b = (uint8_t)atoi(b_tok);

    // For FADE, next token is the fade percentage; for SET it is the addresses
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

    // Token: addresses
    out->addr_count = _lightshow_parse_addrs(next_tok, out->addr);
    out->time_ms = _lightshow_parse_time_ms(time_tok);

    return out->addr_count > 0;
}

// Applies a single instruction to the w_pixels working buffer
static void _lightshow_apply_instr(const lightshow_instr_t *instr)
{
    float hodl;
    if (instr->type == INSTR_SET)
    {
        hodl = 1.0f; // No decay
    }
    else
    {
        // Exponential decay: per-second retention = (100 - fade%) / 100
        // Per-frame retention  = retention_per_sec ^ (TIMESTEP_MS / 1000)
        float retention_per_sec = (100.0f - (float)instr->fade) / 100.0f;
        hodl = powf(retention_per_sec, LIGHTSHOW_TIMESTEP_MS / 1000.0f);
    }

    for (uint8_t a = 0; a < instr->addr_count; a++)
    {
        uint8_t idx = instr->addr[a];
        w_pixels[idx].red = (float)instr->r;
        w_pixels[idx].green = (float)instr->g;
        w_pixels[idx].blue = (float)instr->b;
        w_pixels[idx].hodl = hodl;
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

// Loads a lightshow file from LittleFS into RAM.
// Call once (or whenever the file changes) before start_lightshow().
// Returns true if at least one instruction was loaded.
bool load_lightshow(const char *filepath)
{
    if (!LittleFS.begin(false))
    {
        Serial.println("[Lightshow] LittleFS mount failed");
        return false;
    }

    File f = LittleFS.open(filepath, "r");
    if (!f)
    {
        Serial.printf("[Lightshow] Cannot open %s\n", filepath);
        return false;
    }

    show_instruction_count = 0;

    while (f.available() && show_instruction_count < MAX_INSTRUCTIONS)
    {
        String line = f.readStringUntil('\n');
        lightshow_instr_t instr;
        if (_lightshow_parse_line(line.c_str(), &instr))
            show_instructions[show_instruction_count++] = instr;
    }

    f.close();
    Serial.printf("[Lightshow] Loaded %u instructions from %s\n",
                  show_instruction_count, filepath);
    return show_instruction_count > 0;
}

// Starts playback from the beginning. Call after load_lightshow().
void start_lightshow()
{
    for (int i = 0; i < MAJORA_LEDS; i++)
        w_pixels[i] = {0.0f, 0.0f, 0.0f, 1.0f};

    // Sequential LED chase: lights each LED one by one so we can map
    // which physical positions respond to which software indices.
    // Eyes (0,1) should blink first, then spikes (2-11), then front (12-27).
    for (int i = 0; i < MAJORA_LEDS; i++)
    {
        majora.setPixelColor(i, majora.Color(180, 180, 180));
        majora.show();
        delay(60);
        majora.setPixelColor(i, 0);
    }
    majora.show();

    show_current_index = 0;
    show_start_ms = millis();
    show_playing = true;
    Serial.printf("[Lightshow] Playback started — first instruction at %u ms\n",
                  show_instruction_count > 0 ? show_instructions[0].time_ms : 0);
}

// Stops playback and clears the strip.
void stop_lightshow()
{
    show_playing = false;
    for (int i = 0; i < MAJORA_LEDS; i++)
        majora.setPixelColor(i, 0);
    majora.show();
    Serial.println("[Lightshow] Playback stopped");
}

bool is_lightshow_playing() { return show_playing; }
uint16_t lightshow_loaded_count() { return show_instruction_count; }

// Frame update – call every loop iteration.
// Fires due instructions, decays active pixels, and pushes to the majora strip.
void run_lightshow()
{
    if (!show_playing)
        return;

    static uint32_t last_update = 0;
    if (millis() - last_update < LIGHTSHOW_TIMESTEP_MS)
        return;
    last_update = millis();

    uint32_t elapsed = millis() - show_start_ms;

    // Debug: print elapsed every second and dump first fired instruction
    static uint32_t last_debug_sec = 0;
    if (elapsed / 1000 != last_debug_sec)
    {
        last_debug_sec = elapsed / 1000;
        Serial.printf("[Lightshow] t=%u ms  idx=%u/%u  next_instr_t=%u ms\n",
                      elapsed, show_current_index, show_instruction_count,
                      show_current_index < show_instruction_count
                          ? show_instructions[show_current_index].time_ms
                          : 0);
    }

    // Fire all instructions whose timestamp has passed
    while (show_current_index < show_instruction_count &&
           show_instructions[show_current_index].time_ms <= elapsed)
    {
        const lightshow_instr_t &ins = show_instructions[show_current_index];
        Serial.printf("[Lightshow] FIRE t=%u %s rgb(%u,%u,%u) fade=%u addrs=",
                      ins.time_ms, ins.type == INSTR_SET ? "SET" : "FADE",
                      ins.r, ins.g, ins.b, ins.fade);
        for (uint8_t a = 0; a < ins.addr_count; a++)
            Serial.printf("%u ", ins.addr[a]);
        Serial.println();
        _lightshow_apply_instr(&show_instructions[show_current_index]);
        show_current_index++;
    }

    // Decay active pixels and update the strip
    bool any_lit = false;
    for (int i = 0; i < MAJORA_LEDS; i++)
    {
        if (w_pixels[i].red > 0.5f ||
            w_pixels[i].green > 0.5f ||
            w_pixels[i].blue > 0.5f)
        {
            w_pixels[i].red *= w_pixels[i].hodl;
            w_pixels[i].green *= w_pixels[i].hodl;
            w_pixels[i].blue *= w_pixels[i].hodl;
            any_lit = true;
        }
        majora.setPixelColor(i, majora.Color(
                                    (uint8_t)w_pixels[i].red,
                                    (uint8_t)w_pixels[i].green,
                                    (uint8_t)w_pixels[i].blue));
    }
    majora.show();

    // Stop automatically once all instructions have fired and all LEDs have faded out
    if (show_current_index >= show_instruction_count && !any_lit)
    {
        show_playing = false;
        Serial.println("[Lightshow] Playback finished");
    }
}

#endif // SERVICE_LIGHTSHOW
