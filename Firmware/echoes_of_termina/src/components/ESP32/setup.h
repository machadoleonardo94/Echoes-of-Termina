#if !defined(SETUP_ESP32)
#define SETUP_ESP32

#include "shared/dependencies.h"
#include "driver/adc.h"
#include "esp_adc_cal.h"

esp_adc_cal_characteristics_t adc_cal;

void setup_ESP32()
{
    Serial.println("[ESP32] SETUP STARTED!");

    //* Setup Watchdog
    esp_task_wdt_init(180, true);
    esp_task_wdt_add(NULL);

    adc1_config_width(ADC_WIDTH_BIT_12);
    adc1_config_channel_atten(ADC1_CHANNEL_0, ADC_ATTEN_DB_12);
    esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_12, ADC_WIDTH_BIT_12, 1100, &adc_cal);

    Serial.println("[ESP32] SETUP FINISHED!");
}

void setup_GPIOs()
{
    Serial.println("[GPIO] SETUP STARTED!");

    // prog_switch (GPIO 0) is configured in setup_button_interrupt()

    Serial.println("[GPIO] SETUP FINISHED!");
}

#endif // SETUP_ESP32