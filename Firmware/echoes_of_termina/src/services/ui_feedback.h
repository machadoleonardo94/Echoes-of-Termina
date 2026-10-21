#if !defined(SERVICE_UI_FEEDBACK)
#define SERVICE_UI_FEEDBACK

//? ------------------------------------------------------------------------------------------
//*! UI feedback — the strip as the interface's answer.
//*!
//*!  With two buttons and no screen, some actions cannot be confirmed anywhere else:
//*!    • a volume ramp that happens while nothing is playing is silent AND invisible
//*!    • "cycle LED animation" changes a setting whose two audible modes only look
//*!      different once music is playing, so the user is tapping blind
//*!    • a mode switch reboots the board — 8 s of nothing, right after a gesture that
//*!      needs to be acknowledged
//*!
//*!  So the strip carries the acknowledgements, in both modes, for free: 144 LEDs are
//*!  already there, already driven, and cost nothing on the audio path. See
//*!  docs/two-button-ui.md.
//*!
//*!  ── How it takes over ─────────────────────────────────────────────────────
//*!  The overlay OWNS the strip while a gesture is being reported, and the other two
//*!  renderers stand down (run_lightshow() and run_led_animation() both consult
//*!  ui_feedback_active()). That is not politeness: each of them ends its frame with
//*!  majora.show(), which blocks for the whole RMT transfer (~29 µs per LED, ~4 ms at
//*!  144 LEDs). Two renderers drawing the same frame would halve the frame rate for
//*!  no visible gain.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

// Forward declaration, on purpose. lightshow.h and led_animations.h both have to
// consult ui_feedback_active() to stand down while the overlay draws, so this file
// has to come BEFORE them — which means the frame period they own is declared here
// rather than pulled in. One translation unit, so the definition later in
// lightshow.h is the same function.
uint16_t lightshow_frame_period_ms();

// How long a report stays on the strip after the last update.
#define UI_VOLUME_HOLD_MS 1200 // keeps the bar up while a finger taps the ramp
#define UI_FLASH_TOTAL_MS 700  // one confirmation sequence, start to finish

enum UiFeedbackMode : uint8_t
{
    UI_FB_IDLE = 0,
    UI_FB_VOLUME, // a bar filled to the current level
    UI_FB_FLASH,  // N blinks, framed in a colour that says what was confirmed
};

static UiFeedbackMode ui_fb_mode = UI_FB_IDLE;
static uint32_t ui_fb_until_ms = 0;
static uint32_t ui_fb_last_frame_ms = 0;
static uint8_t ui_fb_volume = 0;
static uint8_t ui_fb_blinks = 0; // how many blinks to draw
static uint32_t ui_fb_color = 0; // packed NEO_RGB, 24-bit: a uint16_t would truncate it

// True while the overlay is drawing. run_lightshow()/run_led_animation() yield on it.
bool ui_feedback_active() { return ui_fb_mode != UI_FB_IDLE; }

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

// Bar to `percent` for UI_VOLUME_HOLD_MS. Called on every ramp tick, so the bar
// tracks the finger rather than latching a single value.
void ui_show_volume(uint8_t percent)
{
    ui_fb_mode = UI_FB_VOLUME;
    ui_fb_volume = percent > 100 ? 100 : percent;
    ui_fb_until_ms = millis() + UI_VOLUME_HOLD_MS;
    // Discard the frame pacing so the very next pump draws: a ramp tick is 150 ms
    // apart and the frame period can be longer than that on a long strip, which
    // would make the bar visibly step behind the finger.
    ui_fb_last_frame_ms = 0;
}

// `blinks` flashes in `color` (packed 24-bit, as majora.Color() returns). Used to
// confirm an animation change (blink count = animation + 1) and to acknowledge a
// mode switch before the reboot.
void ui_flash(uint8_t blinks, uint32_t color)
{
    if (blinks == 0)
        blinks = 1;
    if (blinks > 4)
        blinks = 4;
    ui_fb_mode = UI_FB_FLASH;
    ui_fb_blinks = blinks;
    ui_fb_color = color;
    ui_fb_until_ms = millis() + UI_FLASH_TOTAL_MS;
    ui_fb_last_frame_ms = 0;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

static void _ui_fb_clear()
{
    for (uint16_t i = 0; i < majora_led_count; i++)
        majora.setPixelColor(i, 0);
    majora.show();
}

// A single fill from the strip's start. Kept deliberately plain: a bar is read at a
// glance, and this has to look identical whether the endpoint is the left or the
// right end of whatever is attached.
static void _ui_fb_draw_volume()
{
    uint16_t n = majora_led_count;
    uint16_t lit = (uint16_t)(((uint32_t)ui_fb_volume * n + 50) / 100);
    if (ui_fb_volume > 0 && lit == 0)
        lit = 1; // "just above zero" must still show something

    for (uint16_t i = 0; i < n; i++)
    {
        if (i < lit)
        {
            // Green up to 70%, amber to 90%, red above: the bar says "loud" before
            // the speaker does.
            uint8_t r, g, b;
            if (ui_fb_volume >= 90)
            {
                r = 255;
                g = 40;
                b = 0;
            }
            else if (ui_fb_volume >= 70)
            {
                r = 255;
                g = 170;
                b = 0;
            }
            else
            {
                r = 0;
                g = 210;
                b = 60;
            }
            // The leading pixel is brighter, so the exact end of the bar is readable
            // on a diffused strip.
            bool head = (i + 1 == lit);
            majora.setPixelColor(i, majora.Color(head ? r : r / 3,
                                                head ? g : g / 3,
                                                head ? b : b / 3));
        }
        else
        {
            majora.setPixelColor(i, 0);
        }
    }
    majora.show();
}

static void _ui_fb_draw_flash(uint32_t now)
{
    // The blink is derived from elapsed time rather than counted per frame, so the
    // cadence does not depend on the frame period (which scales with strip length).
    uint32_t elapsed = UI_FLASH_TOTAL_MS - (uint32_t)(ui_fb_until_ms - now);
    uint32_t slot = UI_FLASH_TOTAL_MS / ui_fb_blinks;
    if (slot == 0)
        slot = 1;
    bool on = (elapsed % slot) < (slot / 2);

    uint32_t color = on ? ui_fb_color : 0;
    for (uint16_t i = 0; i < majora_led_count; i++)
        majora.setPixelColor(i, color);
    majora.show();
}

// Frame pump. Call once per loop(), after buttons_poll().
void ui_feedback_pump()
{
    if (ui_fb_mode == UI_FB_IDLE)
        return;

    uint32_t now = millis();
    if ((int32_t)(now - ui_fb_until_ms) >= 0)
    {
        ui_fb_mode = UI_FB_IDLE;
        // Clear on the way out. Without this the last overlay frame would stay lit
        // whenever nothing else is rendering (LED_ANIM_NONE and no show playing).
        _ui_fb_clear();
        return;
    }

    uint32_t period = lightshow_frame_period_ms();
    if (period == 0)
        period = 16;
    if (ui_fb_last_frame_ms != 0 && now - ui_fb_last_frame_ms < period)
        return;
    ui_fb_last_frame_ms = now;

    if (ui_fb_mode == UI_FB_VOLUME)
        _ui_fb_draw_volume();
    else
        _ui_fb_draw_flash(now);
}

// Blocking acknowledgement, for the one action that is about to take the CPU away:
// a mode switch reboots the board, so the user sees three flashes and then the
// strip go dark, which is what tells them the gesture was accepted rather than
// missed. ~0.9 s, paid once, on a gesture nobody makes by accident.
void ui_flash_blocking(uint8_t blinks, uint32_t color)
{
    for (uint8_t b = 0; b < blinks; b++)
    {
        for (uint16_t i = 0; i < majora_led_count; i++)
            majora.setPixelColor(i, color);
        majora.show();
        delay(120);
        for (uint16_t i = 0; i < majora_led_count; i++)
            majora.setPixelColor(i, 0);
        majora.show();
        delay(120);
    }
}

#endif // SERVICE_UI_FEEDBACK
