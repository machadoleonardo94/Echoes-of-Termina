#if !defined(SERVICE_READINGS)
#define SERVICE_READINGS

#include "shared/dependencies.h"

void read_adc();

/*
ADC_ATTEN_DB_2_5
V(ADC)<1050mV
V_reg(max) = 4.2V
V(ADC)/V_reg <= 0.25
*/

// The button service used to live here (setup_button_interrupt() + check_button()
// with a shared press counter). It now has its own file — services/buttons.h —
// because that counter had grown four meanings on one button (1 = pause,
// 2 = WiFi, 3 = mode switch, long = show) and could not carry a second button.
// This file keeps the analog input only.

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
#endif // SERVICE_READINGS
