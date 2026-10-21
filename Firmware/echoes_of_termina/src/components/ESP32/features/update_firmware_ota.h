#if !defined(UPDATE_FIRMWARE_OTA_WIFI)
#define UPDATE_FIRMWARE_OTA_WIFI

#include "shared/dependencies.h"

#include <ArduinoOTA.h>
#include <WiFiUdp.h>

//? ------------------------------------------------------------------------------------------
//*! Network firmware update (ArduinoOTA, UDP port 3232) — WiFi mode only.
//*!
//*  ORDERING IS LOAD-BEARING.
//*  ArduinoOTA::begin() opens a lwIP socket: WiFiUDP::begin() -> socket() ->
//*  lwip_socket() -> netconn_new() -> tcpip_send_msg_wait_sem(). That mailbox
//*  only exists after tcpip_init(), which arduino-esp32 runs lazily from
//*  esp_netif_init() the first time WiFi is used (WiFiGeneric.cpp tcpipInit(),
//*  reached via WiFi.mode()/WiFi.begin()/WiFi.softAP()).
//*  Calling begin() before any WiFi init — which is what this file used to do,
//*  from setup_ESP32() at the top of setup() — asserts inside lwIP:
//*      assert failed: tcpip_send_msg_wait_sem tcpip.c:455 (Invalid mbox)
//*  The panic resets the CPU, setup() runs again and calls begin() again, so the
//*  board sits in a boot loop and never answers espota: that is the "flash
//*  error" seen in the upload log.
//*  So: connect to WiFi FIRST, then start OTA — and stop OTA before the radio is
//*  torn down (standalone show, Bluetooth mode, mode switch), otherwise
//*  handle() would poll a socket whose netif no longer exists.
//? ------------------------------------------------------------------------------------------

static bool ota_started = false;

// True only while the OTA server is actually listening. main.cpp guards
// ArduinoOTA.handle() with this, so Bluetooth mode — where the WiFi radio is
// off and OTA was never started — never touches the network stack.
bool ota_running() { return ota_started; }

void setup_OTA()
{
    if (ota_started)
        return;

    // Refuse rather than panic: no WiFi means no TCP/IP stack means no socket.
    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("[OTA] Skipped — WiFi not connected (lwIP would not be up)");
        return;
    }

    ArduinoOTA.setHostname("echoes");
    ArduinoOTA.setPort(3232);
    // The portal owns mDNS ("echoes.local"). ArduinoOTA's own instance would
    // rename the device to esp32-xxxxxx, and its end() would tear down the
    // portal's responder. espota still finds the board by IP or by
    // echoes.local (resolved by the host's mDNS resolver).
    ArduinoOTA.setMdnsEnabled(false);

    ArduinoOTA.onStart([]()
                       {
    String type;
    if (ArduinoOTA.getCommand() == U_FLASH)
      type = "sketch";
    else // U_FS
      type = "filesystem";

    // NOTE: if updating FS this would be the place to unmount FS using FS.end()
    Serial.println("[OTA] Start updating " + type); });

    // No ESP.restart() here: the library reboots itself right after this
    // callback (_rebootOnSuccess defaults to true). Restarting from inside the
    // callback cuts the update flow short for no gain.
    ArduinoOTA.onEnd([]()
                     { Serial.println("\n[OTA] End — rebooting"); });

    // The upload blocks the loop task for tens of seconds, and that task is on
    // the 180 s task watchdog (setup_ESP32). Kick it on every progress report so
    // a slow upload cannot be mistaken for a hung loop.
    ArduinoOTA.onProgress([](unsigned int progress, unsigned int total)
                          {
    esp_task_wdt_reset();
    Serial.printf("[OTA] Progress: %u%%\r", (progress / (total / 100))); });

    ArduinoOTA.onError([](ota_error_t error)
                       {
    Serial.printf("[OTA] Error[%u]: ", error);
    if (error == OTA_AUTH_ERROR) Serial.println("Auth Failed");
    else if (error == OTA_BEGIN_ERROR) Serial.println("Begin Failed");
    else if (error == OTA_CONNECT_ERROR) Serial.println("Connect Failed");
    else if (error == OTA_RECEIVE_ERROR) Serial.println("Receive Failed");
    else if (error == OTA_END_ERROR) Serial.println("End Failed"); });

    ArduinoOTA.begin();
    ota_started = true;

    Serial.printf("[OTA] Ready on port 3232 at %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[OTA] Upload: pio run -t upload --upload-port %s\n",
                  WiFi.localIP().toString().c_str());
}

// Call this before anything powers the radio down. ArduinoOTA.end() closes the
// UDP socket and clears its initialized flag, so a later setup_OTA() starts a
// fresh server on the new netif.
void stop_OTA()
{
    if (!ota_started)
        return;

    ArduinoOTA.end();
    ota_started = false;
    Serial.println("[OTA] Stopped");
}

#endif // UPDATE_FIRMWARE_OTA_WIFI
