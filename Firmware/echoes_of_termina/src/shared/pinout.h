#ifndef PROJECT_GLOBAL_PINOUT
#define PROJECT_GLOBAL_PINOUT

#include "shared/dependencies.h"

//? ------------------------------------------------------------------------------------------
//*! Board pinout selection.
//*!
//*  Pick the board with a build flag in platformio.ini:
//*      -DECHOES_V1      → bench board (default if neither is given)
//*      -DMARAJOARA_V3   → Marajoara_V3
//*!
//*  Both boards carry an ESP32-WROOM-32 N8R2/32UE module, so the board file
//*  cannot imply the pinout — the environment has to state it. Defining both,
//*  or neither, is a hard error rather than a silent fallback onto the wrong
//*  hardware.
//*!
//*  Why compile time and not a runtime variable: the module's 2 MB PSRAM chip is
//*  wired internally to GPIO16 (CS) and GPIO17 (CLK), so whether the external
//*  RAM may be used at all is a property of the pinout, and psramInit() runs
//*  before setup(). PINOUT_PSRAM_AVAILABLE below is what testSPIRAM() in
//*  components/ESP32/setup.h is driven from.
//? ------------------------------------------------------------------------------------------

#if defined(ECHOES_V1) && defined(MARAJOARA_V3)
#error "Define only one board: -DECHOES_V1 or -DMARAJOARA_V3 (see platformio.ini)"
#elif !defined(ECHOES_V1) && !defined(MARAJOARA_V3)
#define ECHOES_V1 // default: the bench board
#endif

#if defined(ECHOES_V1)

// ── Echoes V1 (bench board) ─────────────────────────────────────────────────
#define PINOUT_NAME "Echoes V1"

// The MAX98357A sits on GPIO16/17/18 — the very GPIO16/17 the module's PSRAM
// chip uses for CS/CLK. Driving I2S therefore scrambles every external RAM
// access, so PSRAM must stay disabled on this board (see setup.h testSPIRAM).
#define PINOUT_PSRAM_AVAILABLE 0

//* Outputs:
#define LED_output 25

//* I2S_NUM_0 — MAX98357A speaker via the ESP32-A2DP sink
#define I2C_DOUT 16
#define I2C_BCLK 17
#define I2C_LRC 18

//* I2S_NUM_1 — INMP441 microphone. Wire its L/R pin to GND for the left channel.
#define MIC_SCK 27
#define MIC_WS 26
#define MIC_SD 34

//* microSD — 1-bit (CLK/CMD/D0 only)
#define SDMMC_CLK 14
#define SDMMC_CMD 15
#define SDMMC_D0 2

//* Extras:
#define prog_switch 0
#define button 4
#define latch 13

//* I2C:
#define i2c_sda 21
#define i2c_scl 22

//* Analog input:
#define adc_input 36

#elif defined(MARAJOARA_V3)

// ── Marajoara V3 ────────────────────────────────────────────────────────────
// Pin map taken from the board itself (Marajoara_V3.kicad_pcb, U2 = ESP32-WROOM-32UE
// pad → net). GPIO16 and GPIO17 are NOT broken out on this board, which is what
// makes the 2 MB PSRAM usable here.
#define PINOUT_NAME "Marajoara V3"
#define PINOUT_PSRAM_AVAILABLE 1

//* Outputs:
#define LED_output 23 // net: Strip_out

//* I2S_NUM_0 — MAX98357A speaker, driven by the A2DP sink.
//* The DAC's BCLK and DIN moved onto the SDMMC data nets that the 1-bit microSD
//* no longer needs (IO27 = old SD_D2, IO13 = old SD_D3); LRC stays on 18.
#define I2C_DOUT 13   // net: I2S_SPK_D    (was SD_D3)
#define I2C_BCLK 27   // net: I2S_SPK_BCLK (was SD_D2)
#define I2C_LRC 18    // net: I2S_LRC

//* I2S_NUM_1 — INMP441 microphone, its own peripheral and its own clock pins.
//* None of these clash with PSRAM, so they are unchanged from the original map.
#define MIC_SCK 26    // net: I2S_MIC_BCLK
#define MIC_WS 25     // net: I2S_MIC_WS
#define MIC_SD 34     // net: I2S_MIC_D (input-only pin)

//* microSD — 1-bit. The card only has CLK/CMD/D0/D1 wired to the MCU on this
//* board (no D2/D3), so 4-bit was never an option; D2/D3's nets now carry the
//* DAC's I2S signals.
#define SDMMC_CLK 14
#define SDMMC_CMD 15
#define SDMMC_D0 2

//* Extras:
#define prog_switch 0 // net: PROG
#define button 19     // net: Button_GPIO
#define latch 32      // net: latch_enable (GPIO32 is an RTC GPIO, so the hold works)

//* I2C:
#define i2c_sda 21
#define i2c_scl 22

//* Analog input:
#define adc_input 36 // net: ADC1_0

#endif

// ---------------------------------------------------------------------------
// Pinout invariants
// ---------------------------------------------------------------------------

// The module's PSRAM chip owns GPIO16 (CS) and GPIO17 (CLK). A pinout that
// claims either pin while expecting PSRAM to work will scramble every external
// RAM access — the failure that cost the V1 bring-up several days.
#if PINOUT_PSRAM_AVAILABLE
#if (LED_output == 16) || (LED_output == 17) ||   \
    (I2C_DOUT == 16) || (I2C_DOUT == 17) ||       \
    (I2C_BCLK == 16) || (I2C_BCLK == 17) ||       \
    (I2C_LRC == 16) || (I2C_LRC == 17) ||         \
    (MIC_SCK == 16) || (MIC_SCK == 17) ||         \
    (MIC_WS == 16) || (MIC_WS == 17) ||           \
    (MIC_SD == 16) || (MIC_SD == 17) ||           \
    (SDMMC_CLK == 16) || (SDMMC_CLK == 17) ||     \
    (SDMMC_CMD == 16) || (SDMMC_CMD == 17) ||     \
    (SDMMC_D0 == 16) || (SDMMC_D0 == 17) ||       \
    (prog_switch == 16) || (prog_switch == 17) || \
    (button == 16) || (button == 17) ||           \
    (latch == 16) || (latch == 17) ||             \
    (i2c_sda == 16) || (i2c_sda == 17) ||         \
    (i2c_scl == 16) || (i2c_scl == 17) ||         \
    (adc_input == 16) || (adc_input == 17)
#error "This pinout enables PSRAM but claims GPIO16/GPIO17 (the PSRAM chip's CS/CLK)."
#endif
#endif

#endif // PROJECT_GLOBAL_PINOUT
