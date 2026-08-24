#if !defined(SERVICE_READINGS)
#define SERVICE_READINGS

#include "shared/dependencies.h"

void read_adc();
void check_button();
void setup_button_interrupt();

/*
ADC_ATTEN_DB_2_5
V(ADC)<1050mV
V_reg(max) = 4.2V
V(ADC)/V_reg <= 0.25
*/

void read_adc()
{
    static uint32_t lastUpdate = 0;
    if (millis() - lastUpdate < 60000)
        return;
    lastUpdate = millis();
    adcReading = analogRead(adc_input);
    adcVoltage = 4 * esp_adc_cal_raw_to_voltage(adcReading, &adc_cal);
    Serial.printf("[ADC] Reading: %d, Voltage: %d mV\n", adcReading, adcVoltage);
}

volatile bool button_irq_pending = false;
volatile uint32_t button_irq_time = 0;

void IRAM_ATTR button_isr()
{
    uint32_t now = millis();
    if (now - button_irq_time >= 50) // ISR-level debounce
    {
        button_irq_time = now;
        button_irq_pending = true;
    }
}

void setup_button_interrupt()
{
    pinMode(prog_switch, INPUT); // GPIO 0 has a permanent bootstrap pull-up
    attachInterrupt(digitalPinToInterrupt(prog_switch), button_isr, FALLING);
}

// Button debounce: a 1- or 2-press action waits 1 s after the last press so a
// double-click never fires the single-press pause along the way.
static uint32_t s_last_press_ms = 0;
static bool s_debounce_pending = false;

void check_button()
{
    if (button_irq_pending)
    {
        button_irq_pending = false;

        static uint32_t lastPress;

        if (millis() - lastPress > 5000) // Reset counter if more than 5 seconds since last press
            button_states.counter = 0;

        lastPress = millis();
        s_last_press_ms = lastPress;

        while (digitalRead(prog_switch) == LOW)
            delay(10); // Wait for button release

        uint32_t held_ms = millis() - lastPress;

        if (held_ms > 1000)
        {
            // Long press: standalone show — WiFi off, play a random lightshow
            // project from the SD card (memory-test workaround).
            button_states.counter = 0;
            s_debounce_pending = false;
            Serial.printf("[BUTTON] Long press (%u ms) — standalone show\n", held_ms);
            trigger_standalone_show();
            return;
        }

        button_states.counter++;
        s_debounce_pending = true;
        Serial.printf("[BUTTON] Press #%d\n", button_states.counter);

        if (button_states.counter >= 3)
        {
            // 3 presses: switch operating mode — act immediately
            s_debounce_pending = false;
            button_states.counter = 0;
            DeviceMode new_mode = (active_mode == MODE_BLUETOOTH) ? MODE_WIFI : MODE_BLUETOOTH;
            switch_mode(new_mode); // saves to NVS and restarts
            return;
        }
    }

    // Debounced action: fires 1 s after the last press. A second press within
    // that window cancels the single-press pause and becomes the 2-press WiFi
    // re-enable instead.
    if (s_debounce_pending && millis() - s_last_press_ms >= 1000)
    {
        s_debounce_pending = false;
        int n = button_states.counter;
        button_states.counter = 0;

        if (n == 1)
        {
            audio_toggle_pause();
        }
        else if (n == 2)
        {
            if (active_mode == MODE_WIFI)
            {
                wifi_enable();
                // Playing with WiFi on is choppy — pause until WiFi is off
                if (player != nullptr && player->isRunning())
                {
                    _audio_pause();
                    Serial.println("[BUTTON] WiFi on — playback paused");
                }
            }
            else
            {
                Serial.println("[WIFI] Skip — not in WiFi mode");
            }
        }
    }
}
#endif // SERVICE_READINGS