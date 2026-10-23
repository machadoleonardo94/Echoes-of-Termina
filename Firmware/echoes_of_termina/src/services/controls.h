#if !defined(SERVICE_CONTROLS)
#define SERVICE_CONTROLS

//? ------------------------------------------------------------------------------------------
//*! Controls — the action layer: what each button gesture does.
//*!
//*!  services/buttons.h turns pin levels into gestures and knows nothing else.
//*!  This file is the only place that decides what a gesture MEANS, so the
//*!  interface can be argued about (and changed) without touching the state
//*!  machine.
//*!
//*!  ── The map ────────────────────────────────────────────────────────────────
//*!  The map that shipped after the two-button evaluation (docs/two-button-ui.md
//*!  §9). PROG is single-purpose, so buttons.h emits every tap as its own CLICK
//*!  and this file never sees a DOUBLE or TRIPLE from it; EXT carries the ladder
//*!  and pays the 400 ms click window.
//*!
//*!    gesture            PROG (GPIO0)                  EXT (GPIO4)
//*!    click              play / pause                  play when idle, else next song
//*!    double             — (no ladder)                 cycle LED animation
//*!    triple             — (no ladder)                 switch mode (reboots)
//*!    hold + repeat      volume down                   volume up
//*!    very long (4 s)    restart the current song      bring WiFi back
//*!
//*!  A click in WiFi mode with nothing loaded means "play": it starts a
//*!  standalone show off the SD card (see ctrl_play_from_idle), on either button.
//*!
//*!  ── Why there is a mode branch in every action ───────────────────────────
//*!  The device has two playback backends and they are not interchangeable:
//*!    Bluetooth mode  — the phone is the transport. AVRCP passthrough commands
//*!                      (play/pause/next/previous) are the only way in, and
//*!                      there is no local track, no playlist and no seek.
//*!    WiFi/standalone — the SD player is the transport: real playlist index,
//*!                      real restart, real pause. But the radio is DOWN while a
//*!                      track plays (see _play_standalone), so the portal is
//*!                      unreachable and AVRCP does not exist.
//*!  Same gesture, two backends. Keeping the branch in one visible place is the
//*!  point of this file.
//*!
//*!  ── Volume ────────────────────────────────────────────────────────────────
//*!  One model: a percent in NVS ("audio"/"volume"), applied to whichever
//*!  backend is live — a2dp_sink.set_volume(0..127) in Bluetooth mode,
//*  player->setVolume(0..21) locally. The percent is what survives a mode
//*  switch, so the two backends cannot drift apart.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"

// One hold tick of the ramp. At 150 ms per tick (buttons.h BTN_REPEAT_MS) a full
// 0..100 sweep takes ~3 s of holding.
#define CTRL_VOLUME_STEP_PERCENT 5

void on_button_event(ButtonId id, ButtonEvent event, uint16_t held_ms);

// Defined further down, next to the other playback actions; play/pause falls back
// to it when there is nothing loaded to pause.
static void ctrl_play_from_idle();

// ---------------------------------------------------------------------------
// Volume
// ---------------------------------------------------------------------------
//
// The level itself is NOT cached here. wifi_portal.h owns it (percent + the
// decoder step derived from it, one NVS key, one writer) because the portal's
// slider writes the same value; a copy in this file goes stale the moment the
// slider moves and would then be written back over it by the next button press.

static bool ctrl_volume_limit_logged = false;

// True until the current hold's first ramp tick has written NVS. See the note on
// _set_volume_percent(): the ramp persists at its start and at its end, never on
// every tick.
static bool ctrl_ramp_first_step = true;

// Set for the duration of a hold, with the level it started at, so a hold that
// turns into a very-long gesture can put the volume back (see ctrl_ramp_undo).
static bool ctrl_ramp_active = false;
static uint8_t ctrl_ramp_start_percent = 100;

// Pushes the current level into whichever backend is live. The caller has
// already stored the percent — this is only the backend-specific half.
static void ctrl_volume_apply()
{
    uint8_t percent = audio_volume_percent();

    if (active_mode == MODE_BLUETOOTH)
    {
        // 0..127 is the sink's range, so 100% lands on the full-scale value
        // setup_bluetooth_mode() used to hard-code.
        uint8_t step = (uint8_t)(((uint16_t)percent * 127) / 100);
        a2dp_sink.set_volume(step);
        Serial.printf("[CTRL] volume %u%% (A2DP step %u/127)\n", (unsigned)percent, (unsigned)step);
    }
    else
    {
        Serial.printf("[CTRL] volume %u%% (player step %u/21)\n",
                      (unsigned)percent, (unsigned)player_volume);
    }
}

static void ctrl_volume_step(int8_t direction)
{
    int16_t current = (int16_t)audio_volume_percent();
    int16_t next = current + direction * CTRL_VOLUME_STEP_PERCENT;
    if (next < 0)
        next = 0;
    if (next > 100)
        next = 100;

    if ((uint8_t)next == (uint8_t)current)
    {
        // The ramp is against an end. Log it once per gesture rather than once
        // per 150 ms tick: holding a button at 0% or 100% used to fill the log
        // with one line every tick. The bar still refreshes, so the strip does not
        // go dark under a finger that is still holding; no tick plays, because one
        // every 150 ms would be a siren rather than a report.
        if (!ctrl_volume_limit_logged)
        {
            ctrl_volume_limit_logged = true;
            Serial.printf("[CTRL] volume already at %s limit (%u%%)\n",
                          direction > 0 ? "upper" : "lower", (unsigned)current);
        }
        ui_show_volume((uint8_t)current);
        return;
    }

    ctrl_volume_limit_logged = false;
    // First tick of a gesture persists; the ticks after it only re-level, and
    // the release persists the value the finger left behind.
    audio_set_volume_percent((uint8_t)next, ctrl_ramp_first_step);
    ctrl_ramp_first_step = false;
    ctrl_volume_apply(); // and the live backend

    // Both feedback channels, for the one gesture that is otherwise silent AND
    // invisible: the strip works in either mode, the tick only in Bluetooth mode,
    // and there its PITCH reports the level (cues are mixed after volume control so
    // they stay audible at any setting, which rules out reporting level by loudness).
    ui_show_volume((uint8_t)next);
    cue_volume_tick((uint8_t)next);
}

// ---------------------------------------------------------------------------
// Destructive-action guard
// ---------------------------------------------------------------------------
//
// The diagnostic build (-DBUTTON_TRACE=1) exists to verify BUTTON INPUT. Two of
// the actions below would end that verification on the spot: a mode switch reboots
// the CPU (and, in WiFi mode, drops the OTA link the test firmware arrived on), and
// starting a standalone show tears WiFi down so the portal and this log go away.
// They are therefore logged and skipped there, so a misread gesture cannot cost you
// the session.
#if BUTTON_TRACE
#define CTRL_SUPPRESS_DESTRUCTIVE 1
#else
#define CTRL_SUPPRESS_DESTRUCTIVE 0
#endif

static bool ctrl_may_run(const char *what)
{
#if CTRL_SUPPRESS_DESTRUCTIVE
    Serial.printf("[CTRL] %s — suppressed (diagnostic build; reboot/show disabled)\n", what);
    return false;
#else
    (void)what;
    return true;
#endif
}

// ---------------------------------------------------------------------------
// Bluetooth transport state
// ---------------------------------------------------------------------------
//
// AVRCP absolute-volume/playback notifications are the only view of the phone's
// transport state. Until one arrives we assume the phone is playing, because
// that is the state in which someone reaches for the button — and a wrong guess
// costs one extra press, not a wrong action.
static esp_avrc_playback_stat_t bt_play_status = ESP_AVRC_PLAYBACK_PLAYING;
static bool bt_status_from_phone = false;

static void ctrl_on_bt_play_status(esp_avrc_playback_stat_t status)
{
    bt_play_status = status;
    bt_status_from_phone = true;
    Serial.printf("[CTRL] phone playback status -> %d\n", (int)status);
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

static void ctrl_play_pause()
{
    if (active_mode == MODE_BLUETOOTH)
    {
        if (!a2dp_sink.is_connected())
        {
            Serial.println("[CTRL] play/pause: no Bluetooth source connected");
            cue_play(CUE_DENY);
            return;
        }
        // The sink's own play()/pause() are AVRCP passthrough commands — they
        // ask the PHONE to start or stop, which is the only transport that
        // exists in this mode. Each of them also blocks this task for ~100 ms
        // (execute_avrc_command's PRESSED/delay/RELEASED), which is why nothing
        // in this file calls them in a loop.
        Serial.printf("[CTRL] play/pause: last known phone status %d (%s)\n",
                      (int)bt_play_status,
                      bt_status_from_phone ? "reported by phone" : "assumed");
        if (bt_play_status == ESP_AVRC_PLAYBACK_PLAYING)
        {
            a2dp_sink.pause();
            bt_play_status = ESP_AVRC_PLAYBACK_PAUSED;
            cue_play(CUE_PAUSE);
            Serial.println("[CTRL] AVRCP pause");
        }
        else
        {
            a2dp_sink.play();
            bt_play_status = ESP_AVRC_PLAYBACK_PLAYING;
            cue_play(CUE_PLAY);
            Serial.println("[CTRL] AVRCP play");
        }
        return;
    }

    // Local playback. With nothing loaded, "play/pause" can only mean play — and on
    // this device "play" means starting a standalone show off the SD card. Without
    // this, a click in the state the device actually idles in (WiFi up, nothing
    // playing) logged "No track to pause" and did nothing at all.
    if (!audio_has_track())
    {
        ctrl_play_from_idle();
        return;
    }

    bool was_paused = !audio_is_playing();
    audio_toggle_pause();
    cue_play(was_paused ? CUE_PLAY : CUE_PAUSE);
}

static void ctrl_next_track()
{
    if (active_mode == MODE_BLUETOOTH)
    {
        if (!a2dp_sink.is_connected())
        {
            Serial.println("[CTRL] next: no Bluetooth source connected");
            cue_play(CUE_DENY);
            return;
        }
        a2dp_sink.next();
        cue_play(CUE_NEXT);
        Serial.println("[CTRL] AVRCP next");
        return;
    }
    if (!audio_has_track())
    {
        Serial.println("[CTRL] next: nothing loaded");
        cue_play(CUE_DENY);
        return;
    }
    audio_next_track();
    cue_play(CUE_NEXT);
}

// "Restart" is exact on the local player (re-connect to the same file). AVRCP
// has no restart-track command, so Bluetooth mode sends previous(): most source
// apps restart the current track when it arrives early in the track and step
// back otherwise. That approximation is the source's decision, not ours.
static void ctrl_restart_track()
{
    if (active_mode == MODE_BLUETOOTH)
    {
        if (!a2dp_sink.is_connected())
        {
            Serial.println("[CTRL] restart: no Bluetooth source connected");
            cue_play(CUE_DENY);
            return;
        }
        a2dp_sink.previous();
        cue_play(CUE_NEXT);
        Serial.println("[CTRL] AVRCP previous (restart approximation)");
        return;
    }
    if (!audio_has_track())
    {
        Serial.println("[CTRL] restart: nothing loaded");
        cue_play(CUE_DENY);
        return;
    }
    audio_restart_track();
    cue_play(CUE_CONFIRM);
}

static void ctrl_cycle_animation()
{
    uint8_t next = (uint8_t)((led_anim_mode_get() + 1) % 3);
    set_led_anim_mode(next);
    Serial.printf("[CTRL] LED animation -> %s\n", led_anim_mode_name());

    // This is the gesture that most needs an answer: the two audio-reactive modes
    // look identical (both dark) until music plays, so without a confirmation the
    // user is cycling blind. The blink COUNT is the animation index + 1 — one blink
    // for NONE, two for ENVELOPE, three for FFT — so the strip also says WHICH one
    // was landed on, not merely that something changed.
    ui_flash((uint8_t)(next + 1), majora.Color(0, 0, 255));
    cue_play(CUE_CONFIRM);
}

static void ctrl_switch_mode()
{
    if (!ctrl_may_run("switch mode"))
        return;

    // switch_mode() reboots, so acknowledge the gesture first: three flashes and a
    // short arpeggio, then the device goes away for several seconds while the other
    // radio comes up. Without this the user cannot tell an accepted triple-click
    // from a missed one.
    //
    // cue_play_now() is the synchronous variant because everything after it blocks or
    // reboots, so the pump in loop() never gets a chance to render the arpeggio. It
    // plays on the I2S wire in both modes (in WiFi mode it borrows the peripheral for
    // ~200 ms) and declines when a stream owns it — in which case the arpeggio goes
    // through the mixer instead, with the old 260 ms of slack to play out.
    if (!cue_play_now(CUE_MODE))
        cue_play(CUE_MODE);
    delay(260);
    ui_flash_blocking(3, majora.Color(255, 255, 255));
    // Anything not yet in NVS would be lost with the reboot.
    audio_set_volume_percent(audio_volume_percent());
    switch_mode(active_mode == MODE_BLUETOOTH ? MODE_WIFI : MODE_BLUETOOTH);
}

// "Play" for this device: pick a random Lightshows project off the SD card and
// play its audio with its lightshow, radio down.
//
// This is what a click means when nothing is loaded — see ctrl_play_pause(). It is
// also why the old long-press show moved here: the gesture that starts playback is
// the one a user tries first, and it should not be hidden behind a one-second hold.
static void ctrl_play_from_idle()
{
    if (active_mode != MODE_WIFI)
    {
        // Bluetooth mode has no local player to start: the Audio constructor
        // installs I2S_NUM_0, which the A2DP sink already owns. (The old code
        // allowed this and produced a silent, half-torn-down state instead.)
        Serial.println("[CTRL] play: Bluetooth mode plays from the phone, not the SD card");
        cue_play(CUE_DENY);
        return;
    }
    if (!ctrl_may_run("play from idle (standalone show)"))
        return;
    if (!sdcard)
    {
        Serial.println("[CTRL] play: no SD card");
        cue_play(CUE_DENY);
        return;
    }
    Serial.println("[CTRL] nothing loaded — starting a standalone show");
    cue_play(CUE_PLAY);
    trigger_standalone_show();
}

// Brings the radio back after a standalone show tore it down, so uploads and the
// portal resume without a reboot. This is the button-side escape from the
// radio-down state: playback physically cannot run with WiFi up on a board
// without PSRAM, so once a show is playing there is no portal to reach.
static void ctrl_wifi_restore()
{
    if (active_mode != MODE_WIFI)
    {
        // In Bluetooth mode the radio belongs to the A2DP stack; bringing WiFi
        // up next to it is what wifi_enable() itself refuses to do.
        Serial.println("[CTRL] WiFi restore: not in WiFi mode");
        cue_play(CUE_DENY);
        return;
    }
    // Same reasoning as ctrl_switch_mode(): the two flashes below block the loop for
    // ~0.5 s, so the cue has to be rendered here rather than pumped, and the WiFi
    // re-enable that follows is exactly what the tone is confirming.
    if (!cue_play_now(CUE_CONFIRM))
        cue_play(CUE_CONFIRM);
    ui_flash_blocking(2, majora.Color(0, 255, 0));
    wifi_enable();
}

// ---------------------------------------------------------------------------
// Gesture → action
// ---------------------------------------------------------------------------

// The most useful thing a click can do, given what the device is doing right now.
// One click, two meanings, and the split is decided by the state the user can see:
// with nothing playing it means "play", otherwise it means "next".
static void ctrl_click_primary()
{
    if (active_mode == MODE_WIFI && !audio_has_track())
        ctrl_play_from_idle();
    else
        ctrl_next_track();
}

// Puts the volume back where the hold found it.
//
// A very-long hold starts life as a volume ramp: by the time it passes 4000 ms the
// ramp has already re-levelled the audio, and it was never the user's intent. So
// when the gesture turns into something else, the ramp it interrupted is undone —
// otherwise "restart this song" would also have yanked the volume to one end.
static void ctrl_ramp_undo()
{
    if (!ctrl_ramp_active || audio_volume_percent() == ctrl_ramp_start_percent)
        return;
    Serial.printf("[CTRL] hold became a very-long gesture — volume restored to %u%%\n",
                  (unsigned)ctrl_ramp_start_percent);
    audio_set_volume_percent(ctrl_ramp_start_percent);
    ctrl_volume_apply();
    ui_show_volume(ctrl_ramp_start_percent);
}

void on_button_event(ButtonId id, ButtonEvent event, uint16_t held_ms)
{
    (void)held_ms;

    // BTN_EV_RELEASE ends a hold. The ramp has already re-levelled the audio on
    // every tick; this is where the level it stopped at gets written to NVS, so a
    // three-second hold costs two flash writes instead of twenty. Any hold lands
    // here, including one that only ever reported "at limit" — which is correct,
    // it persists the unchanged value and _save_volume() skips the write.
    if (event == BTN_EV_RELEASE)
    {
        audio_set_volume_percent(audio_volume_percent());
        ctrl_ramp_active = false;
        return;
    }

    // A fresh hold is a fresh gesture: its ramp may report an end again, its first
    // tick persists, and its starting level is remembered in case the hold goes on
    // to become a very-long gesture.
    if (event == BTN_EV_HOLD)
    {
        ctrl_volume_limit_logged = false;
        ctrl_ramp_first_step = true;
        ctrl_ramp_active = true;
        ctrl_ramp_start_percent = audio_volume_percent();
    }

    // ── PROG: single-purpose, so its clicks are instant (no multi-click window) ──
    // The state machine cannot produce DOUBLE or TRIPLE for this button at all:
    // buttons.h emits each tap as its own CLICK when multi_gap_ms is 0.
    if (id == BTN_PROG)
    {
        switch (event)
        {
        case BTN_EV_CLICK: ctrl_play_pause(); break;
        case BTN_EV_HOLD:
        case BTN_EV_REPEAT: ctrl_volume_step(-1); break;
        case BTN_EV_VERY_LONG:
            ctrl_ramp_undo();
            ctrl_restart_track();
            break;
        default: break;
        }
        return;
    }

    // ── EXT: the gesture ladder ────────────────────────────────────────────────
    switch (event)
    {
    case BTN_EV_CLICK: ctrl_click_primary(); break;
    case BTN_EV_DOUBLE: ctrl_cycle_animation(); break;
    case BTN_EV_TRIPLE: ctrl_switch_mode(); break;
    case BTN_EV_HOLD:
    case BTN_EV_REPEAT: ctrl_volume_step(+1); break;
    case BTN_EV_VERY_LONG:
        ctrl_ramp_undo();
        ctrl_wifi_restore();
        break;
    default: break;
    }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

// Called from setup() AFTER the mode setup, so the A2DP sink is started and
// a2dp_sink.set_volume() has something to talk to.
void controls_begin()
{
    if (active_mode == MODE_BLUETOOTH)
    {
        a2dp_sink.set_avrc_rn_playstatus_callback(ctrl_on_bt_play_status);
        // The one transport-level event the user cannot otherwise perceive: a phone
        // connecting or dropping. The callback only posts a cue request — it runs on the
        // Bluetooth application task, which must not be blocked for the ~300 ms a tone
        // takes — and cue_wire_pump() plays it from loop().
        //
        // Registered here rather than in setup_bluetooth_mode() because that function is
        // compiled before audio_cues.h exists. The window this leaves open (start() to
        // controls_begin()) is a few milliseconds, while the sink's own auto-reconnect
        // waits reconnect_delay_ms before it even tries.
        a2dp_sink.set_on_connection_state_changed(cue_on_bt_connection_state);
    }

    // Seed the level from NVS (the single stored value, shared by both backends)
    // and push it into whichever backend came up.
    audio_load_volume_percent();
    ctrl_volume_apply();

    // Tones go into the Bluetooth stream only; the strip answers in both modes.
    cues_begin();

    Serial.printf("[CTRL] ready — mode %s, volume %u%%, cues %s\n",
                  active_mode == MODE_BLUETOOTH ? "Bluetooth" : "WiFi",
                  (unsigned)audio_volume_percent(),
                  cues_available() ? "on" : "off (strip only)");
    Serial.println("[CTRL] map: PROG click=play/pause hold=vol- very-long=restart");
    Serial.println("[CTRL] map: EXT  click=play/next double=animation triple=mode hold=vol+ very-long=wifi");
}

#endif // SERVICE_CONTROLS
