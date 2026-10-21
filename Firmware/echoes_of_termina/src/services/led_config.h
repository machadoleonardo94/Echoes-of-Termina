#if !defined(SERVICE_LED_CONFIG)
#define SERVICE_LED_CONFIG

//? ------------------------------------------------------------------------------------------
//*! LED strip configuration — runtime LED count and the brightness ceiling.
//*!
//*  Both used to be compile-time assumptions (MAJORA_LEDS 28). They are settings
//*  now, because the same firmware drives either the 28-LED Majora mask or a strip
//*  of 28..200 LEDs, and the strip's safe brightness depends on how many LEDs it
//*  has.
//*!
//*  WHY THE CEILING DEPENDS ON THE COUNT
//*  A WS2812 draws ~60 mA at full white, and that is PER LED — a per-LED brightness
//*  cap does not shrink the strip total as the count grows. At 64/255 (25%) a
//*  144-LED strip is still ~2.16 A and a 200-LED strip ~3.0 A, both past the 2 A
//*  the battery can deliver in pulses. So the ceiling is a step table keyed on the
//*  LED count, chosen to hold the strip near a constant 1.3-1.7 A:
//*      <=28   255 (100%)   the Majora mask — unscaled, exactly as it behaves today
//*      <=56   127  (50%)
//*      <=96    63  (25%)
//*      <=144   38  (15.2%)
//*      <=200   31  (12.5%)
//*  The 200 step is the one that keeps the top of the range under budget: flat 15%
//*  would be 1.83 A at 200 LEDs, leaving ~170 mA for the ESP32, the amplifier and
//*  the SD card, which is not safe.
//*!
//*  The step table is the intent; _led_ceiling_clamp() is a derived safety net so
//*  the table cannot be edited into an unsafe state later. With the steps above the
//*  clamp never binds (it would allow 35 at 200 LEDs where the table gives 31).
//*!
//*  setBrightness() semantics, which are easy to get backwards: the library stores
//*  b+1 and deliberately rolls it over, so setBrightness(255) means NO scaling
//*  (colour values interpreted literally) and setBrightness(0) means OFF — not
//*  "full". Scaling happens in 8-bit space inside setPixelColor()
//*  (Adafruit_NeoPixel.cpp:3389), and updateLength() does not disturb it.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

#define LED_NVS_NAMESPACE "leds"
#define LED_NVS_KEY_COUNT "count"

// The lightshow engine walks the wildcard through a uint8_t index, so the array
// bound must stay inside that type. Raising MAJORA_MAX_LEDS past 255 needs that
// counter widened first.
static_assert(MAJORA_MAX_LEDS <= 255, "MAJORA_MAX_LEDS must fit the uint8_t wildcard index");

// Current budget for the strip alone. The battery delivers 2 A in pulses; the
// ESP32, the MAX98357A and the SD card need the difference.
#define LED_STRIP_BUDGET_MA 1700
#define LED_PER_LED_MA 60 // WS2812 worst case at full white, all three channels

// Step table (the intended ceiling for a given strip length).
static uint8_t _led_ceiling_step(uint16_t n)
{
    if (n <= 28)
        return 255; // unscaled: preserves every existing mask show byte-for-byte
    if (n <= 56)
        return 127; // 50%
    if (n <= 96)
        return 63;  // 25%
    if (n <= 144)
        return 38;  // 15.2%
    return 31;      // 12.5% for 145..200
}

// Derived clamp: the largest setBrightness value that still keeps the strip inside
// the current budget. setBrightness(b) scales by (b+1)/256, so the constraint is
// n * 60 mA * (b+1)/256 <= budget.
static uint8_t _led_ceiling_clamp(uint16_t n)
{
    if (n == 0)
        return 0;
    uint32_t stored = (uint32_t)LED_STRIP_BUDGET_MA * 256UL / ((uint32_t)LED_PER_LED_MA * n);
    if (stored > 256)
        stored = 256;
    if (stored == 0)
        return 0;
    return (uint8_t)(stored - 1);
}

static uint8_t _led_ceiling_for(uint16_t n)
{
    uint8_t step = _led_ceiling_step(n);
    uint8_t clamp = _led_ceiling_clamp(n);
    return step < clamp ? step : clamp;
}

// Rough worst-case strip current at the given ceiling, for the log line. Only a
// diagnostic: it assumes every LED at full white on all three channels.
static uint16_t _led_estimate_ma(uint16_t n, uint8_t ceiling)
{
    return (uint16_t)((uint32_t)LED_PER_LED_MA * n * (uint32_t)(ceiling + 1) / 256UL);
}

// ---------------------------------------------------------------------------
// NVS persistence — mirrors the volume slider's pattern in wifi_portal.h, which
// is the established way settings survive a reboot.
// ---------------------------------------------------------------------------

static uint16_t _led_clamp_count(uint16_t n)
{
    if (n < 1)
        n = 1;
    if (n > MAJORA_MAX_LEDS)
        n = MAJORA_MAX_LEDS;
    return n;
}

uint16_t load_led_count()
{
    // Read-WRITE even though this only reads: a namespace that has never been
    // written does not exist, and a read-only open logs
    // "[E][Preferences.cpp:50] begin(): nvs_open failed: NOT_FOUND" on every boot
    // of a fresh device. A read-write open creates it instead. Same in
    // led_animations.h.
    internal_storage.begin(LED_NVS_NAMESPACE, false);
    uint16_t n = internal_storage.getUShort(LED_NVS_KEY_COUNT, (uint16_t)MAJORA_LED_COUNT_DEFAULT);
    internal_storage.end();
    return _led_clamp_count(n);
}

void save_led_count(uint16_t n)
{
    n = _led_clamp_count(n);
    internal_storage.begin(LED_NVS_NAMESPACE, false);
    internal_storage.putUShort(LED_NVS_KEY_COUNT, n);
    internal_storage.end();
    Serial.printf("[LED] Saved count: %u\n", (unsigned)n);
}

// ---------------------------------------------------------------------------
// Applying the configuration
// ---------------------------------------------------------------------------

// Resizes the strip and applies the ceiling. Safe to call at runtime (the web UI
// does): updateLength() reallocates the pixel buffer and clears it, and the
// per-LED buffers are all sized MAJORA_MAX_LEDS so nothing can be overrun.
static void _led_apply(uint16_t count)
{
    majora_led_count = count;
    majora.updateLength(majora_led_count);

    uint8_t ceiling = _led_ceiling_for(majora_led_count);
    majora.setBrightness(ceiling);

    Serial.printf("[LED] count=%u (max %u) ceiling=%u/255 (~%u%% , ~%u mA worst case)\n",
                  (unsigned)majora_led_count, (unsigned)MAJORA_MAX_LEDS,
                  (unsigned)ceiling,
                  (unsigned)((uint32_t)(ceiling + 1) * 100UL / 256UL),
                  (unsigned)_led_estimate_ma(majora_led_count, ceiling));
}

// Boot path: read the persisted count and apply it. MUST run before the first
// show() — Adafruit's ESP32 driver grows its RMT encoding buffer to fit the
// largest frame ever transmitted and never shrinks it (esp.c:54-61, ~96 bytes per
// LED), so showing 200 LEDs once permanently commits 18.8 KB.
void apply_led_config()
{
    _led_apply(load_led_count());
}

// Runtime setter for the web UI: persist, then apply live.
//
// Note for anyone raising the count from the UI: Adafruit's ESP32 driver grows its
// RMT encoding buffer (~96 bytes per LED) to the largest frame ever transmitted and
// never shrinks it (esp.c:54-61). Setting 200 and going back to 28 therefore leaves
// ~18.8 KB committed for the rest of the session. Harmless, but it is why the
// count should be set to the strip actually attached rather than experimented with.
void set_led_count(uint16_t n)
{
    n = _led_clamp_count(n);
    if (n == majora_led_count)
        return;
    save_led_count(n);
    _led_apply(n);
}

// Current ceiling and its worst-case current draw, for the web UI. Surfacing these
// matters: the ceiling is derived from the count, so changing the count silently
// changes how bright the strip can be, and the user should see why.
uint8_t led_brightness_ceiling()
{
    return _led_ceiling_for(majora_led_count);
}

uint16_t led_worst_case_ma()
{
    return _led_estimate_ma(majora_led_count, _led_ceiling_for(majora_led_count));
}

#endif // SERVICE_LED_CONFIG
