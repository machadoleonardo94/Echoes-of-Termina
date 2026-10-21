#if !defined(PROJECT_GLOBAL_VARIABLES)
#define PROJECT_GLOBAL_VARIABLES

//* ---------------------- GLOBAL COMPONENT VARIABLES ----------------------

//* OLED display
bool display_started = false;
//* SD card
bool sdcard = false;

long loopTimer = 0;
long globalTimer = 0;
long loopCounter = 0;

uint32_t adcReading = 0;
uint32_t adcVoltage = 0;
#define MAX_ADC_SAMPLES 8
#define OVERSAMPLING_RESOLUTION 3 // 2^3 = 8 samples

//* Button state lives in services/buttons.h now (one runtime struct per button,
//* owned by the polling state machine). The old shared `struct_buttonState` with
//* its single counter is gone with the single-button design.

bool debugWifi = false;

//* Addressable LED strip: live LED count, 1..MAJORA_MAX_LEDS.
//* Set from NVS (or the compile-time default) by apply_led_config() in
//* services/led_config.h, which runs in setup() before the strip is driven.
//* Every loop, bound check and wildcard must use THIS, not MAJORA_MAX_LEDS:
//* the max is only the array bound, while this is what is physically attached.
uint16_t majora_led_count = MAJORA_LED_COUNT_DEFAULT;

char macAdress[6] = {0, 0, 0, 0, 0, 0};

#endif // PROJECT_GLOBAL_VARIABLES
