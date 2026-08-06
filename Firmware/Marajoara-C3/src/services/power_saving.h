#if !defined(SERVICE_POWER_SAVING)
#define SERVICE_POWER_SAVING

#include "shared/dependencies.h"

void check_power_button()
{
    static uint32_t pressed_since = 0;
    static uint8_t press_count = 0;
    static bool button_state = false;

    if (digitalRead(button) && (!button_state))
    {
        Serial.println("Button pressed!");
        button_state = true;
        pressed_since = millis();
        delay(5); // Debounce delay
    }
    if (!digitalRead(button))
        button_state = false;
    if (button_state && (millis() - pressed_since > 3000))
    {
        Serial.printf("Button held for %d ms, entering light sleep...\n", millis() - pressed_since);
        delay(10);
        for (int i = 0; i < MAJORAS_LEDS; i++)
            majora.setPixelColor(i, majora.Color(0, 0, 0));
        majora.show(); // does not turn off eyes, good luck with the timing
        delay(5000);
        digitalWrite(latch_enable, LOW); // Latch power off
        // lightsleep();
    }
    return;
}

#endif