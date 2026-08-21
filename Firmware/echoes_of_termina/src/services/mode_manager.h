#if !defined(MODE_MANAGER_H)
#define MODE_MANAGER_H

//? ------------------------------------------------------------------------------------------
//*! Mode manager — persists and applies one of two operating modes.
//*!
//*!  BLUETOOTH mode (default):
//*!    • WiFi radio disabled
//*!    • btAudio A2DP sink started as "Echoes_of_Termina"
//*!    • I2S output routed to MAX98357A DAC
//*!    • Reconnects to last paired device automatically
//*!
//*!  WIFI mode:
//*!    • Connects to saved WiFi via WiFiManager (opens captive portal if not configured)
//*!    • Starts web portal at http://<localIP>/ for SD management + volume control
//*!    • Falls back to Bluetooth mode if WiFi connection fails
//*!
//*!  Mode is saved to NVS ("device"/"mode") and read at every boot.
//*!  Triple-press of the button at runtime toggles the mode and restarts.
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
    rtc_gpio_hold_en(GPIO_NUM_13); // Hold latch HIGH through CPU reset
    ESP.restart();
}

// ---------------------------------------------------------------------------
// Bluetooth mode setup
// ---------------------------------------------------------------------------

void setup_bluetooth_mode()
{
    Serial.println("[BT] ---- Bluetooth Mode ----");

    WiFi.mode(WIFI_OFF);
    delay(100);

    // A2DP sink: receives audio from paired device, outputs via I2S DAC
    audio.begin();
    audio.I2S(I2C_BCLK, I2C_DOUT, I2C_LRC);
    audio.volume(1.0);

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
    }
    else
    {
        Serial.println("[WIFI] Could not connect — falling back to Bluetooth mode");
        active_mode = MODE_BLUETOOTH;
        setup_bluetooth_mode();
    }
}

#endif // MODE_MANAGER_H
