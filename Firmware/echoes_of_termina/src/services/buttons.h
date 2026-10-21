#if !defined(SERVICE_BUTTONS)
#define SERVICE_BUTTONS

//? ------------------------------------------------------------------------------------------
//*! Button input — two buttons, polled, debounced, event-emitting.
//*!
//*!  Two layers, deliberately apart:
//*!    buttons.h  (this file) — turns pin levels into gestures. Knows nothing
//*!                             about audio, WiFi or LEDs.
//*!    controls.h             — binds gestures to features. That is the file to
//*!                             edit when the interface changes.
//*!
//*!  Wiring (see shared/pinout.h):
//*!    BTN_PROG  prog_switch  GPIO0   pulled up,   press shorts to GND  (active LOW)
//*!    BTN_EXT   button       GPIO4   pulled down, press feeds 3V3      (active HIGH)
//*!
//*!  Polled, not interrupt-driven. GPIO0 is the boot-strapping pin and shares a
//*!  node with the USB adapter's auto-reset RC network (the 100 nF there makes an
//*!  injected glitch last tens of milliseconds), and it idles on a weak ~45k
//*!  internal pull-up. An edge ISR on that node latched every microsecond-scale
//*!  glitch the strip, the I2S clocks and the amplifier inject, which arrived in
//*!  the log as phantom presses. Polling costs one digitalRead() per button per
//*!  loop and lets a level have to survive a debounce window before it counts.
//*!
//*!  Gesture ladder, per button, in the order the state machine decides it:
//*!    MIN_PRESS   shorter than this is electrical noise and is discarded
//*!    CLICK       press released before HOLD_MS, and no further press within
//*!                multi_gap_ms of the release        → BTN_EV_CLICK
//*!    DOUBLE      2 presses in one burst                → BTN_EV_DOUBLE
//*!    TRIPLE      3 presses in one burst                → BTN_EV_TRIPLE
//*!    HOLD        still down after HOLD_MS              → BTN_EV_HOLD, then
//*!                BTN_EV_REPEAT every REPEAT_MS while it stays down (this is
//*!                what a volume ramp runs on), then BTN_EV_RELEASE
//*!    VERY_LONG   still down after VERY_LONG_MS         → BTN_EV_VERY_LONG
//*!
//*!  A hold cancels the burst it interrupts: press-press-hold is a HOLD, never a
//*!  DOUBLE followed by a hold.
//*!
//*!  ── multi_gap_ms, and why a button would set it to 0 ─────────────────────
//*!  Reserving the DOUBLE and TRIPLE slots costs latency on every single click:
//*!  the tap cannot be committed until the window closes, because at the moment
//*!  of release a second tap may still be coming. Measured on the bench board,
//*!  that is a 400 ms delay between the finger leaving the button and anything
//*!  happening — paid on the most frequent gesture there is.
//*!
//*!  So the window is a per-button property. A button whose clicks are
//*!  single-purpose sets it to 0: the click is emitted the moment the button comes
//*!  up, and the DOUBLE/TRIPLE tiers simply do not exist for that button (two
//*!  taps produce two clicks, which is what a user tapping twice on a
//*!  single-purpose button means). A button that carries a ladder keeps a window
//*!  and pays the latency on its clicks only.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

// Diagnostic build (-DBUTTON_TRACE=1): log every raw edge and a periodic idle
// level report for both pins. This is what proves an external button is wired
// correctly, and controls.h uses it to refuse disruptive actions.
#ifndef BUTTON_TRACE
#define BUTTON_TRACE 0
#endif

enum ButtonId : uint8_t
{
    BTN_PROG = 0, // GPIO0  — onboard PROG/BOOT button, active LOW
    BTN_EXT = 1,  // GPIO4  — external button on pin `button`, active HIGH
    BTN_COUNT = 2
};
enum ButtonEvent : uint8_t
{
    BTN_EV_CLICK = 0, // short press, burst closed
    BTN_EV_DOUBLE,    // two presses in one burst
    BTN_EV_TRIPLE,    // three presses in one burst
    BTN_EV_HOLD,      // held past HOLD_MS (fires once)
    BTN_EV_REPEAT,    // still held, every REPEAT_MS after HOLD
    BTN_EV_RELEASE,   // released after a HOLD
    BTN_EV_VERY_LONG, // held past VERY_LONG_MS (fires once, swallows repeats)
};

typedef void (*ButtonHandler)(ButtonId id, ButtonEvent event, uint16_t held_ms);

// ---------------------------------------------------------------------------
// Timing
// ---------------------------------------------------------------------------

#define BTN_DEBOUNCE_MS 25    // a level must hold this long before it is believed
#define BTN_MIN_PRESS_MS 50   // anything shorter is a blip, not a human
#define BTN_HOLD_MS 600       // press-and-hold starts here (volume ramps)
#define BTN_REPEAT_MS 150     // auto-repeat period while held
#define BTN_VERY_LONG_MS 4000 // reboot-class hold
#define BTN_MULTI_GAP_MS 400  // gap that closes a click burst
#define BTN_MAX_CLICKS 3      // a 4th press in a burst stays a TRIPLE

// ---------------------------------------------------------------------------
// Wiring table — one line per button, the only place a pin is named.
// `multi_gap_ms` is that button's click-decision window; 0 means "commit the
// click on release" (see the multi_gap_ms note above).
// ---------------------------------------------------------------------------

struct ButtonWiring
{
    uint8_t pin;
    uint8_t active_level; // the level a press produces
    const char *name;
    uint16_t multi_gap_ms;
};

static const ButtonWiring button_wiring[BTN_COUNT] = {
    // PROG is the single-purpose button: click = play/pause, hold = volume. It
    // has no DOUBLE or TRIPLE, so its clicks are instant.
    {prog_switch, LOW, "PROG", 0},
    // EXT carries the gesture ladder (click/double/triple), so its clicks wait
    // for the window to close.
    {button, HIGH, "EXT", BTN_MULTI_GAP_MS},
};

struct ButtonRuntime
{
    bool raw;               // most recent sample (may still be bouncing)
    bool stable;            // debounced level, true == pressed
    uint32_t raw_change_ms; // when raw last changed
    uint32_t pressed_ms;    // when the settled press began
    uint32_t last_repeat_ms;
    uint32_t burst_deadline_ms; // when a pending click burst stops waiting
    uint16_t last_held_ms;
    uint8_t clicks;         // presses counted in the open burst
    bool hold_reported;
    bool very_long_reported;
};

static ButtonRuntime buttons[BTN_COUNT];
static ButtonHandler button_handler = nullptr;
static uint32_t button_heartbeat_ms = 0;

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

static const char *button_id_name(ButtonId id)
{
    return button_wiring[id].name;
}

static const char *button_event_name(ButtonEvent event)
{
    switch (event)
    {
    case BTN_EV_CLICK: return "click";
    case BTN_EV_DOUBLE: return "double";
    case BTN_EV_TRIPLE: return "triple";
    case BTN_EV_HOLD: return "hold";
    case BTN_EV_REPEAT: return "repeat";
    case BTN_EV_RELEASE: return "release";
    case BTN_EV_VERY_LONG: return "very-long";
    }
    return "?";
}

static void button_emit(ButtonId id, ButtonEvent event, uint16_t held_ms)
{
    Serial.printf("[BTN] %s %s (%u ms)\n", button_id_name(id),
                  button_event_name(event), (unsigned)held_ms);
    if (button_handler != nullptr)
        button_handler(id, event, held_ms);
}

// ---------------------------------------------------------------------------
// Setup / poll
// ---------------------------------------------------------------------------

void buttons_begin(ButtonHandler handler)
{
    button_handler = handler;

    for (uint8_t i = 0; i < BTN_COUNT; i++)
    {
        const ButtonWiring &w = button_wiring[i];
        // The internal pull is what holds the pin at its idle level: `button`
        // has a pull-down resistor on the PCB, but there is no reason to make
        // the firmware depend on it (and the ESP32's own pull is enough).
        pinMode(w.pin, (w.active_level == LOW) ? INPUT_PULLUP : INPUT_PULLDOWN);

        ButtonRuntime &b = buttons[i];
        b.raw = b.stable = (digitalRead(w.pin) == w.active_level);
        b.raw_change_ms = millis();

        Serial.printf("[BTN] %s on GPIO%u, active %s — idle level %s\n",
                      w.name, (unsigned)w.pin,
                      (w.active_level == LOW) ? "LOW (press to GND)" : "HIGH (press to 3V3)",
                      b.stable ? "PRESSED AT BOOT" : "released");
    }
}

void buttons_poll()
{
    uint32_t now = millis();

    for (uint8_t i = 0; i < BTN_COUNT; i++)
    {
        const ButtonWiring &w = button_wiring[i];
        ButtonRuntime &b = buttons[i];

        // ── 1. Sample, and remember when the raw level last moved ───────────
        bool raw = (digitalRead(w.pin) == w.active_level);
        if (raw != b.raw)
        {
            b.raw = raw;
            b.raw_change_ms = now;
#if BUTTON_TRACE
            Serial.printf("[BTN] %s raw -> %s @%u ms\n", w.name,
                          raw ? "PRESSED" : "released", (unsigned)now);
#endif
        }

        // ── 2. Believe the level only once it has held still ────────────────
        if ((now - b.raw_change_ms) >= BTN_DEBOUNCE_MS && raw != b.stable)
        {
            b.stable = raw;

            if (raw)
            {
                // Press begins. `clicks` is deliberately NOT cleared here: a
                // press inside an open burst is the second or third click of
                // that burst. The burst closes itself in step 4 below.
                b.pressed_ms = now;
                b.hold_reported = false;
                b.very_long_reported = false;
            }
            else
            {
                uint32_t held = now - b.pressed_ms;

                if (held < BTN_MIN_PRESS_MS)
                {
                    // Electrical blip, not a human. Leave the open burst alone:
                    // a real tap in progress must not be disturbed by noise.
                    Serial.printf("[BTN] %s ignored %u ms blip\n", w.name, (unsigned)held);
                }
                else if (b.hold_reported)
                {
                    b.last_held_ms = (uint16_t)min(held, (uint32_t)0xFFFF);
                    button_emit((ButtonId)i, BTN_EV_RELEASE, b.last_held_ms);
                }
                else
                {
                    b.last_held_ms = (uint16_t)min(held, (uint32_t)0xFFFF);
                    if (w.multi_gap_ms == 0)
                    {
                        // Single-purpose button: nothing can follow this tap that
                        // would reinterpret it, so decide it now. This is the whole
                        // point of a 0 window — no waiting on a click that is never
                        // going to be a double.
                        button_emit((ButtonId)i, BTN_EV_CLICK, b.last_held_ms);
                    }
                    else
                    {
                        if (b.clicks < BTN_MAX_CLICKS)
                            b.clicks++;
                        b.burst_deadline_ms = now + w.multi_gap_ms;
                    }
                }
            }
        }

        // ── 3. While it is down: hold → very-long → repeat ──────────────────
        if (b.stable)
        {
            uint32_t held = now - b.pressed_ms;

            if (!b.hold_reported && held >= BTN_HOLD_MS)
            {
                b.hold_reported = true;
                b.clicks = 0; // the hold swallows whatever burst it interrupted
                b.last_repeat_ms = now;
                button_emit((ButtonId)i, BTN_EV_HOLD, (uint16_t)min(held, (uint32_t)0xFFFF));
            }
            else if (b.hold_reported)
            {
                if (!b.very_long_reported && held >= BTN_VERY_LONG_MS)
                {
                    // VERY_LONG claims the gesture: a reboot-class action is
                    // about to happen, so the repeat train that a ramp was
                    // running on stops here rather than carrying on underneath.
                    b.very_long_reported = true;
                    button_emit((ButtonId)i, BTN_EV_VERY_LONG, (uint16_t)min(held, (uint32_t)0xFFFF));
                }
                else if (!b.very_long_reported && now - b.last_repeat_ms >= BTN_REPEAT_MS)
                {
                    b.last_repeat_ms = now;
                    button_emit((ButtonId)i, BTN_EV_REPEAT, (uint16_t)min(held, (uint32_t)0xFFFF));
                }
            }
        }
        // ── 4. Or, if the burst went quiet, decide click / double / triple ──
        else if (b.clicks > 0 && (int32_t)(now - b.burst_deadline_ms) >= 0)
        {
            uint8_t n = b.clicks;
            b.clicks = 0;
            ButtonEvent event = (n >= 3) ? BTN_EV_TRIPLE
                               : (n == 2) ? BTN_EV_DOUBLE
                                          : BTN_EV_CLICK;
            button_emit((ButtonId)i, event, b.last_held_ms);
        }
    }

#if BUTTON_TRACE
    // Periodic idle census: the quickest way to see a dead wire (a pin that
    // never reads its idle level), a stuck button, or which pin a press lands on.
    if (now - button_heartbeat_ms >= 5000)
    {
        button_heartbeat_ms = now;
        Serial.print("[BTN] levels:");
        for (uint8_t i = 0; i < BTN_COUNT; i++)
            Serial.printf(" %s(GPIO%u)=%s", button_wiring[i].name,
                          (unsigned)button_wiring[i].pin,
                          digitalRead(button_wiring[i].pin) ? "HIGH" : "LOW");
        Serial.println();
    }
#endif
}

#endif // SERVICE_BUTTONS
