#if !defined(RECORD_AUDIO_I2S_H)
#define RECORD_AUDIO_I2S_H

//? ------------------------------------------------------------------------------------------
//*! INMP441 microphone recording via I2S_NUM_1.
//*! The A2DP sink occupies I2S_NUM_0 for speaker output; this peripheral is fully independent.
//*! Both boards give the mic its own clock pins, so this setup works on either.
//*!
//*! The INMP441 requires SCK = 64 × WS, which is why the slots below are 32-bit —
//*! at 16 kHz that is SCK = 1.024 MHz.
//*!
//*! Wiring (signal names as printed on the breakout):
//*!   INMP441 VDD → 3.3V, GND → GND, L/R → GND (selects the left channel)
//*!   INMP441 SCK → MIC_SCK, WS → MIC_WS, SD → MIC_SD
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"
#include <driver/i2s.h>

//* I2S_NUM_1 — kept separate from the A2DP sink, which uses I2S_NUM_0
#define MIC_I2S_PORT    I2S_NUM_1
#define MIC_SAMPLE_RATE 16000
#define MIC_DMA_BUFFERS 8
#define MIC_DMA_BUF_LEN 512

static bool mic_running = false;

void setup_MIC_I2S()
{
    Serial.println("[MIC] SETUP STARTED!");

    const i2s_config_t mic_cfg = {
        .mode                 = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate          = MIC_SAMPLE_RATE,
        .bits_per_sample      = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format       = I2S_CHANNEL_FMT_ONLY_LEFT,  // L/R → GND
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count        = MIC_DMA_BUFFERS,
        .dma_buf_len          = MIC_DMA_BUF_LEN,
        .use_apll             = false,
        .tx_desc_auto_clear   = false,
        .fixed_mclk           = 0
    };

    const i2s_pin_config_t mic_pins = {
        .mck_io_num   = I2S_PIN_NO_CHANGE,
        .bck_io_num   = MIC_SCK,
        .ws_io_num    = MIC_WS,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num  = MIC_SD
    };

    esp_err_t err = i2s_driver_install(MIC_I2S_PORT, &mic_cfg, 0, NULL);
    if (err != ESP_OK)
    {
        Serial.printf("[MIC] i2s_driver_install failed: %s\n", esp_err_to_name(err));
        return;
    }

    err = i2s_set_pin(MIC_I2S_PORT, &mic_pins);
    if (err != ESP_OK)
    {
        Serial.printf("[MIC] i2s_set_pin failed: %s\n", esp_err_to_name(err));
        i2s_driver_uninstall(MIC_I2S_PORT);
        return;
    }

    mic_running = true;
    Serial.println("[MIC] INMP441 ready on I2S_NUM_1 @ 16 kHz");
    Serial.println("[MIC] SETUP FINISHED!");
}

// Reads up to num_samples from the INMP441 into buffer as 24-bit values
// right-aligned inside int32_t. Returns the number of samples actually read.
//
// Usage example:
//   int32_t buf[512];
//   size_t n = read_MIC_samples(buf, 512);
size_t read_MIC_samples(int32_t *buffer, size_t num_samples)
{
    if (!mic_running) return 0;

    size_t bytes_read = 0;
    i2s_read(MIC_I2S_PORT, buffer, num_samples * sizeof(int32_t), &bytes_read, portMAX_DELAY);

    // INMP441 left-justifies its 24-bit value in the upper bits of the 32-bit frame.
    // Shift right by 8 to obtain a right-aligned 24-bit signed sample.
    size_t samples_read = bytes_read / sizeof(int32_t);
    for (size_t i = 0; i < samples_read; i++)
        buffer[i] >>= 8;

    return samples_read;
}

void stop_MIC_I2S()
{
    if (!mic_running) return;
    i2s_driver_uninstall(MIC_I2S_PORT);
    mic_running = false;
    Serial.println("[MIC] INMP441 stopped");
}

#endif // RECORD_AUDIO_I2S_H
