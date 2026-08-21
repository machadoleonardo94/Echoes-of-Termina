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
    if (millis() - lastUpdate < 1000)
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

void check_button()
{
    if (!button_irq_pending)
        return;
    button_irq_pending = false;

    button_states.counter++;
    Serial.printf("[BUTTON] Press #%d\n", button_states.counter);

    if (button_states.counter >= 3)
    {
        button_states.counter = 0;
        DeviceMode new_mode = (active_mode == MODE_BLUETOOTH) ? MODE_WIFI : MODE_BLUETOOTH;
        switch_mode(new_mode); // saves to NVS and restarts
    }
}
#endif // SERVICE_READINGS