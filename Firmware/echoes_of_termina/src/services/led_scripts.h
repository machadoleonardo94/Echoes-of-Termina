#if !defined(SERVICE_LED_SCRIPTS)
#define SERVICE_LED_SCRIPTS

#include "shared/dependencies.h"

void run_majoras();
void update_onboard_LED();
void update_strip();

void run_majoras()
{
    static uint32_t lastUpdate = 0;
    if (millis() - lastUpdate < 10)
        return;
    lastUpdate = millis();
    static uint8_t step = 0; // From 0 to 127 positions
    uint8_t red = 0;
    uint8_t green = 0;
    uint8_t blue = 0;

    float sin1 = sin(((millis() * (2 * PI)) / 1500.0));
    float sin2 = sin(((millis() * (2 * PI)) / 4000.0));
    float sin3 = sin(((millis() * (2 * PI)) / 1200.0));

    red = (40 + 80 * sin1 > 0 ? 40 + 80 * sin1 : 0);
    green = (40 + 80 * sin2 > 0 ? 40 + 80 * sin2 : 0);
    blue = (40 + 80 * sin3 > 0 ? 40 + 80 * sin3 : 0);
    uint16_t total = red + green + blue;

    // For eyes at address 27-28 (V1)
    for (int i = 0; i < MAJORA_LEDS - 2; i++)
    {
        if (i < 16) // Removes offset and dims for first 16 LEDs
            majora.setPixelColor(i, majora.Color(red / 5, green / 10, blue / 10));
        else
            majora.setPixelColor(i, majora.Color(red, green, blue));
    }
    majora.setPixelColor(MAJORA_LEDS - 2, red, 0, 0);
    majora.setPixelColor(MAJORA_LEDS - 1, red, 0, 0);
    /*
    // For eyes at address 00-01 (V2)
    majora.setPixelColor(0, red, 0, 0);
    majora.setPixelColor(1, red, 0, 0);
        for (int i = 2; i < MAJORA_LEDS; i++)
    {
        if (i < 12) // Normal spikes
        majora.setPixelColor(i, majora.Color(red, green, blue));
        else // Dim front leds
        majora.setPixelColor(i, majora.Color(red / 5, green / 10, blue / 10));
    }
    */
    majora.show();
}

#endif // SERVICE_LED_SCRIPTS
