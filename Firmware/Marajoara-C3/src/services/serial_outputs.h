#if !defined(SERVICE_SERIAL_READINGS)
#define SERVICE_SERIAL_READINGS

#include "shared/dependencies.h"

void serial_outputs()
{
    loopCounter++;

    // Detect serial reconnect (USB CDC resets on monitor open — dump status immediately)
    static bool prev_serial_ready = false;
    bool now_ready = (bool)Serial;
    if (now_ready && !prev_serial_ready)
    {
        delay(50);
        Serial.println("\n\n=== Serial reconnected — boot status ===");
        Serial.printf("  Lightshow : %u instructions loaded | %s\n",
                      lightshow_loaded_count(),
                      is_lightshow_playing() ? "PLAYING" : "idle");
        Serial.printf("  WiFi      : %s | RSSI %d dBm\n",
                      WiFi.localIP().toString().c_str(), WiFi.RSSI());
        Serial.println("========================================");
    }
    prev_serial_ready = now_ready;

    if (millis() - loopTimer > 10000)
    {
        Serial.printf("\n\nTotal main loops per 10 seconds: %d \n", loopCounter);
        loopTimer = millis();
        loopCounter = 0;

        String tempIP = WiFi.localIP().toString();
        int8_t wifi_rssi = WiFi.RSSI();

        char macBuf[18];
        snprintf(macBuf, sizeof(macBuf), "%02X:%02X:%02X:%02X:%02X:%02X",
                 macAdress[0], macAdress[1], macAdress[2],
                 macAdress[3], macAdress[4], macAdress[5]);
        String tempMac = String(macBuf);

        Serial.printf("\n\n");
        Serial.printf("->| HARDWARE | IP: %s | MAC: %s | RSSI: %d | \n", tempIP, tempMac.c_str(), wifi_rssi);
        Serial.printf("\n\n");
    }
}

#endif // SERVICE_SERIAL_READINGS
