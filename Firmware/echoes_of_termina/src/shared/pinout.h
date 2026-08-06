#ifndef PROJECT_GLOBAL_PINOUT
#define PROJECT_GLOBAL_PINOUT

#include "shared/dependencies.h"

//* Outputs:
#define LED_output 25

//* I2S PINS (I2S_NUM_0 — MAX98357A speaker via btAudio):
#define I2C_DOUT 16
#define I2C_BCLK 17
#define I2C_LRC  18

//* I2S PINS (I2S_NUM_1 — INMP441 microphone):
//* Wire INMP441 L/R pin to GND for left-channel output.
#define MIC_SCK 26  // INMP441 SCK  → ESP32 GPIO 26
#define MIC_WS  27  // INMP441 WS   → ESP32 GPIO 27
#define MIC_SD  34  // INMP441 SD   → ESP32 GPIO 34 (input-only)

//* Extras:
#define prog_switch 0
#define button_input 4
#define latch_output 13

//* I2C PINS:
#define i2c_sda 21
#define i2c_scl 22

//* Analog Input:
#define adc_input 36

#endif // PROJECT_GLOBAL_PINOUT
