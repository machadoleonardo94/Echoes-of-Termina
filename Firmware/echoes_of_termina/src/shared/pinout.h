#ifndef PROJECT_GLOBAL_PINOUT
#define PROJECT_GLOBAL_PINOUT

#include "shared/dependencies.h"

// Echoes V1

//* Outputs:
#define LED_output 25

//* I2S PINS (I2S_NUM_0 — MAX98357A speaker via ESP32-A2DP sink):
#define I2C_DOUT 16
#define I2C_BCLK 17
#define I2C_LRC 18

//* I2S PINS (I2S_NUM_1 — INMP441 microphone):
//* Wire INMP441 L/R pin to GND for left-channel output.
#define MIC_SCK 27 // INMP441 SCK
#define MIC_WS 26  // INMP441 WS
#define MIC_SD 34  // INMP441 SD

//* SDMMC PINS:
#define SDMMC_CLK 14
#define SDMMC_CMD 15
#define SDMMC_D0 2

//* Extras:
#define prog_switch 0
#define button 4
#define latch 13

//* I2C PINS:
#define i2c_sda 21
#define i2c_scl 22

//* Analog Input:
#define adc_input 36

// Echoes V2
/*
//* Outputs:
#define LED_output 32

//* I2S PINS (I2S_NUM_0 — MAX98357A speaker via ESP32-A2DP sink):
#define I2C_DOUT 16
#define I2C_BCLK 17
#define I2C_LRC 18

//* I2S PINS (I2S_NUM_1 — INMP441 microphone):
//* Wire INMP441 L/R pin to GND for left-channel output.
#define MIC_SCK 26 // INMP441 SCK
#define MIC_WS 25  // INMP441 WS
#define MIC_SD 34  // INMP441 SD

//* SDMMC PINS:
#define SDMMC_CLK 14
#define SDMMC_CMD 15
#define SDMMC_D0 2
#define SDMMC_D1 4
#define SDMMC_D2 27
#define SDMMC_D3 13

//* Extras:
#define prog_switch 0
#define button 19
#define latch 23

//* I2C PINS:
#define i2c_sda 21
#define i2c_scl 22

//* Analog Input:
#define adc_input 36
*/

#endif // PROJECT_GLOBAL_PINOUT
