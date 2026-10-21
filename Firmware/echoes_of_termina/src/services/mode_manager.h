#if !defined(MODE_MANAGER_H)
#define MODE_MANAGER_H

//? ------------------------------------------------------------------------------------------
//*! Mode manager — persists and applies one of two operating modes.
//*!
//*!  BLUETOOTH mode (default):
//*!    • WiFi radio disabled
//*!    • ESP32-A2DP sink started as "Echoes_of_Termina"
//*!    • I2S output routed to MAX98357A DAC
//*!    • Reconnects to last paired device automatically
//*!
//*!  WIFI mode:
//*!    • Connects to saved WiFi via WiFiManager (opens captive portal if not configured)
//*!    • Starts web portal at http://<localIP>/ for SD management + volume control
//*!    • Falls back to Bluetooth mode if WiFi connection fails
//*!
//*!  Mode is saved to NVS ("device"/"mode") and read at every boot.
//*!  Triple-click of the PROG button at runtime toggles the mode and restarts.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"
#include <driver/rtc_io.h>

typedef enum
{
    MODE_BLUETOOTH = 0,
    MODE_WIFI = 1
} DeviceMode;

DeviceMode active_mode = MODE_BLUETOOTH;

// ---------------------------------------------------------------------------
// NVS persistence
// ---------------------------------------------------------------------------

void save_mode(DeviceMode mode)
{
    internal_storage.begin("device", false);
    internal_storage.putUChar("mode", (uint8_t)mode);
    internal_storage.end();
    Serial.printf("[MODE] Saved: %s\n", mode == MODE_WIFI ? "WiFi" : "Bluetooth");
}

DeviceMode load_mode()
{
    internal_storage.begin("device", true);
    uint8_t m = internal_storage.getUChar("mode", (uint8_t)MODE_BLUETOOTH);
    internal_storage.end();
    DeviceMode mode = (m == (uint8_t)MODE_WIFI) ? MODE_WIFI : MODE_BLUETOOTH;
    Serial.printf("[MODE] Loaded: %s\n", mode == MODE_WIFI ? "WiFi" : "Bluetooth");
    return mode;
}

// ---------------------------------------------------------------------------
// Runtime mode switch — saves preference then restarts cleanly
// ---------------------------------------------------------------------------

void switch_mode(DeviceMode new_mode)
{
    Serial.printf("[MODE] Switching to %s — restarting...\n", new_mode == MODE_WIFI ? "WiFi" : "Bluetooth");
    save_mode(new_mode);
    delay(300);
    rtc_gpio_hold_en((gpio_num_t)latch); // Hold latch HIGH through CPU reset
    ESP.restart();
}

// ---------------------------------------------------------------------------
// Bluetooth mode setup
// ---------------------------------------------------------------------------

void setup_bluetooth_mode()
{
    Serial.println("[BT] ---- Bluetooth Mode ----");
    heap_probe("bt: entry");

    WiFi.mode(WIFI_OFF);
    delay(100);
    heap_probe("bt: wifi off");

    // Route A2DP audio to the MAX98357A DAC over the legacy I2S driver
    // (A2DP_LEGACY_I2S_SUPPORT=1 is set in platformio.ini for ESP-IDF 5.x).
    i2s_pin_config_t pin_cfg;
    pin_cfg.mck_io_num = I2S_PIN_NO_CHANGE;
    pin_cfg.bck_io_num = I2C_BCLK;   // 17
    pin_cfg.ws_io_num = I2C_LRC;     // 18
    pin_cfg.data_out_num = I2C_DOUT; // 16
    pin_cfg.data_in_num = I2S_PIN_NO_CHANGE;
    a2dp_sink.set_pin_config(pin_cfg);
    heap_probe("bt: pin config");

    // PCM tap for the Bluetooth-mode animations (see led_animations.h).
    // set_raw_stream_reader() does NOT assign `is_output` — unlike
    // set_stream_reader(), which would silence the DAC if handed the wrong second
    // argument — and it fires before volume control, so the level it sees is
    // independent of the volume set here. Registered before start() so no audio
    // block is missed.
    a2dp_sink.set_raw_stream_reader(led_audio_bt_stream);
    Serial.println("[BT] Raw stream reader registered (LED animation tap)");

    // Reconnect to the last paired device (a few attempts) and start the sink.
    a2dp_sink.set_auto_reconnect(true, 3);
    heap_probe("bt: autoreconnect");
    a2dp_sink.start("Echoes_of_Termina");
    heap_probe("bt: start() returned");

    // Announce the state out loud, once per boot.
    //
    // This is the state the device spends most of its life in and the one it used to
    // report nowhere at all: the strip is dark, the speaker is silent, and a user standing
    // in front of it cannot tell "waiting for a phone" from "hung". The cue is
    // SYNCHRONOUS on purpose — it plays on the I2S driver the sink just installed, and it
    // returns with the wire released, so the reconnect attempts that follow cannot be
    // corrupted by a tone still writing the DMA.
    //
    // Deliberately ONCE. A repeating "I am waiting" beacon would answer "is it on?" better,
    // but it is also the sound that would be hated by day two; the change of state (the
    // connection) is what gets announced from here on, by cue_on_bt_connection_state().
    cue_play_now(CUE_BT_WAIT);
    // Volume is NOT set here any more. One persisted percent drives both
    // backends, and it is applied by controls_begin() once the mode is known —
    // for Bluetooth that is set_volume(percent * 127 / 100), i.e. the same full
    // scale this used to hard-code at boot. See services/controls.h.
    heap_probe("bt: volume deferred to controls_begin");

    Serial.println("[BT] Ready — pair with: Echoes_of_Termina");
}

// ---------------------------------------------------------------------------
// WiFi mode setup
// ---------------------------------------------------------------------------

void setup_wifi_mode()
{
    Serial.println("[WIFI] ---- WiFi Mode ----");

    bool connected = setup_WIFI(); // Connects or opens captive portal; false on timeout
    if (connected)
    {
        start_wifi_portal();
        // OTA only makes sense with the radio up: it needs the TCP/IP stack
        // that WiFi.mode()/WiFi.begin() brought up, and main.cpp only calls
        // ArduinoOTA.handle() while ota_running(). Started after the portal so
        // both share the one mDNS responder ("echoes.local").
        setup_OTA();
    }
    else
    {
        Serial.println("[WIFI] Could not connect — falling back to Bluetooth mode");
        active_mode = MODE_BLUETOOTH;
        setup_bluetooth_mode();
    }
}

#endif // MODE_MANAGER_H
