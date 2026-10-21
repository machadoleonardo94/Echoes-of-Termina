#if !defined(I2S_OUTPUT_TEST_H)
#define I2S_OUTPUT_TEST_H

//? ------------------------------------------------------------------------------------------
//*! I2S output self-test — plays a 1 kHz tone straight through the IDF driver.
//*!
//*  Enable with -DI2S_TEST_BEEP=1. It runs early in setup(), before any mode claims the
//*  I2S peripheral, and installs I2S_NUM_0 exactly the way the A2DP path does (which is
//*  known to drive the MAX98357A correctly on this board):
//*      mode = MASTER|TX, 16-bit, RIGHT_LEFT, STAND_I2S, dma 8 x 64, fixed_mclk = 0
//*  then uninstalls the driver again so the A2DP sink or the MP3 player can take it over.
//*!
//*  Read the result like this:
//*    • tone audible  → the pins, the DAC and the IDF configuration are all fine, so a
//*                      failing playback path is losing its audio inside the decoder or
//*                      the library's own I2S handling.
//*    • tone silent   → the output stage itself is not working at this point in the boot,
//*                      independent of any audio library.
//? ------------------------------------------------------------------------------------------

#include "shared/dependencies.h"
#include <driver/i2s.h>
#include <math.h>

#if !defined(I2S_TEST_BEEP)
#define I2S_TEST_BEEP 0
#endif

#if I2S_TEST_BEEP

#define I2S_TEST_PORT I2S_NUM_0
#define I2S_TEST_RATE 48000
#define I2S_TEST_FREQ 1000
#define I2S_TEST_MS 2000
#define I2S_TEST_FRAMES 512 // frames per write (stereo int16 → 2 KB)

void i2s_output_test()
{
    const i2s_config_t cfg = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = I2S_TEST_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = (i2s_comm_format_t)I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = 0,
        .dma_buf_count = 8,
        .dma_buf_len = 64,
        .use_apll = false,
        .tx_desc_auto_clear = true,
        .fixed_mclk = 0};

    const i2s_pin_config_t pins = {
        .mck_io_num = I2S_PIN_NO_CHANGE,
        .bck_io_num = I2C_BCLK,
        .ws_io_num = I2C_LRC,
        .data_out_num = I2C_DOUT,
        .data_in_num = I2S_PIN_NO_CHANGE};

    esp_err_t err = i2s_driver_install(I2S_TEST_PORT, &cfg, 0, NULL);
    if (err != ESP_OK)
    {
        Serial.printf("[BEEP] i2s_driver_install failed: %s\n", esp_err_to_name(err));
        return;
    }
    err = i2s_set_pin(I2S_TEST_PORT, &pins);
    if (err != ESP_OK)
    {
        Serial.printf("[BEEP] i2s_set_pin failed: %s\n", esp_err_to_name(err));
        i2s_driver_uninstall(I2S_TEST_PORT);
        return;
    }

    Serial.printf("[BEEP] %d Hz tone for %d ms on BCLK=%d LRC=%d DOUT=%d — listen now\n",
                  I2S_TEST_FREQ, I2S_TEST_MS, I2C_BCLK, I2C_LRC, I2C_DOUT);

    static int16_t buf[I2S_TEST_FRAMES * 2];
    const uint32_t total_frames = (uint32_t)I2S_TEST_RATE * I2S_TEST_MS / 1000;
    const uint32_t period = I2S_TEST_RATE / I2S_TEST_FREQ; // 48 frames at 1 kHz
    uint32_t frames_done = 0;
    uint32_t phase = 0;

    while (frames_done < total_frames)
    {
        uint32_t n = total_frames - frames_done;
        if (n > I2S_TEST_FRAMES)
            n = I2S_TEST_FRAMES;

        for (uint32_t i = 0; i < n; i++)
        {
            // Fade the first and last 50 ms so there is no click.
            float amp = 12000.0f;
            uint32_t f = frames_done + i;
            uint32_t fade = I2S_TEST_RATE / 20;
            if (f < fade)
                amp *= (float)f / fade;
            else if (f > total_frames - fade)
                amp *= (float)(total_frames - f) / fade;

            int16_t s = (int16_t)(amp * sinf(2.0f * (float)M_PI * (float)phase / period));
            if (++phase >= period)
                phase = 0;
            buf[i * 2] = s;
            buf[i * 2 + 1] = s;
        }

        size_t written = 0;
        i2s_write(I2S_TEST_PORT, buf, n * 2 * sizeof(int16_t), &written, portMAX_DELAY);
        frames_done += n;
    }

    i2s_zero_dma_buffer(I2S_TEST_PORT);
    i2s_driver_uninstall(I2S_TEST_PORT);
    Serial.println("[BEEP] done");
}

#else

// Disabled: drop -DI2S_TEST_BEEP=1 from platformio.ini to compile this instead.
void i2s_output_test() {}

#endif // I2S_TEST_BEEP

#endif // I2S_OUTPUT_TEST_H
