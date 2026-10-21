#if !defined(CONNECT_WIFI)
#define CONNECT_WIFI

#include "shared/dependencies.h"

#include "shared/constants.h"

WiFiClientSecure wifi_client;

void saveCredentials(const char *ssid, const char *password);
void loadCredentials(char *ssid, char *password);
void get_saved_wifi();
bool setup_WIFI(bool portal_enabled = true);

void saveCredentials(const char *ssid, const char *password)
{
    internal_storage.begin("wifi", false);
    internal_storage.putString("ssid", ssid);
    internal_storage.putString("password", password);
    internal_storage.end();
}

void loadCredentials(char *ssid = nullptr, char *password = nullptr)
{
    internal_storage.begin("wifi", true);

    if (ssid != nullptr)
    {
        String savedSSID = internal_storage.getString("ssid", "");
        strncpy(ssid, savedSSID.c_str(), 32);
        ssid[32] = '\0';
    }
    if (password != nullptr)
    {
        String savedPassword = internal_storage.getString("password", "");
        strncpy(password, savedPassword.c_str(), 32);
        password[32] = '\0';
    }
    internal_storage.end();
}

void get_saved_wifi()
{
    char *ssid;
    char *password;

    // Gets saved values on preferences
    Serial.println("Getting WiFi data...\n");

    // Tries to connect to saved network
    WiFi.setSleep(false);
    WiFi.mode(WIFI_AP_STA);
    Serial.print("Connecting to WiFi ..");
    int dropCounter = 0;
    while (WiFi.status() != WL_CONNECTED)
    {
        Serial.print('.');
        dropCounter++;
        delay(500);
        if (dropCounter > 8)
            break;
    }
    if (WiFi.status() == WL_CONNECTED)
    {
        Serial.println(WiFi.localIP());
        Serial.println("Connected");
        return;
    }
    else
    {
        WiFi.mode(WIFI_OFF);
    }
    return;
}

bool setup_WIFI(bool portal_enabled)
{
    bool connected = false;

    WiFi.mode(WIFI_AP_STA);

    { // Scoped block so WiFiManager is destroyed before we start our own web server
        WiFiManager wifiManager;

        wifiManager.setAPCallback([](WiFiManager *myWiFiManager)
                                  {
            Serial.println("Entered config mode");
            Serial.println(WiFi.softAPIP());
            Serial.println(myWiFiManager->getConfigPortalSSID());
            // The hotspot only exists when there is nothing to connect to, which is
            // exactly the state a user cannot see: the board looks dead, and the phone
            // in their hand is the thing that would fix it. A repeating two-note beacon
            // says "come and configure me" without a screen. Played here rather than
            // from setup_WIFI()'s return, because autoConnect() blocks for up to a
            // minute and a cue after it would arrive a minute too late.
            cue_play_now(CUE_AP_UP); });

        wifiManager.setSaveConfigCallback([]()
                                          { Serial.println("Should save config"); });

        wifiManager.setConfigPortalTimeout(portal_enabled ? 60 : 1);

        // STA hostname (appears in the router's DHCP client list). Keep it
        // DNS-clean — no spaces — so it also shows up correctly on the network.
        wifiManager.setHostname("echoes");

        // Config-portal AP name shown when the device needs initial WiFi setup.
        if (!wifiManager.autoConnect("Echoes of Termina"))
        {
            Serial.println("Failed to connect and hit timeout");
            connected = false;
        }
        else
        {
            connected = (WiFi.status() == WL_CONNECTED);
        }
        // wifiManager goes out of scope (its internal web server on port 80 stops here)
    }

    if (!connected)
    {
        // No network: announce it before mode_manager falls back to Bluetooth. The
        // fallback then plays "bt-wait" straight after, so the user hears the whole
        // story — "no WiFi" falling, then "I am a speaker now, waiting" rising.
        cue_play_now(CUE_WIFI_FAIL);
        return false;
    }

    Serial.println("Connected to WiFi");
    Serial.println(WiFi.localIP());
    // The board is reachable now, which is the one piece of information the portal
    // address on a screen would have carried.
    cue_play_now(CUE_WIFI_OK);

    // Save credentials
    saveCredentials(WiFi.SSID().c_str(), WiFi.psk().c_str());

    // Start our persistent device portal (available at http://<localIP>/ )
    // startWebPortal();

    return true;
}

#endif // CONNECT_WIFI
