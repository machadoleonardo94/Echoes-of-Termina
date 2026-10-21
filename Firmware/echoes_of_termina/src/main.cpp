#include "shared/dependencies.h"
#include <driver/rtc_io.h>

void setup()
{
  //* --- SERIAL BEGIN ---
  //* Begin serial communication with Serial Monitor.
  Serial.begin(115200);

  Serial.println("\n---------------------------------------------------");
  Serial.println("------------------ Void Setup  --------------------");
  Serial.println("---------------------------------------------------\n");

  setup_ESP32(); //* Enables watchdog
  heap_probe("setup_ESP32");
  setup_GPIOs(); //* Configures GPIOs

  majora.begin();

  //* Strip length + brightness ceiling from NVS (falls back to the compile-time
  //* default). MUST run before the FIRST show() anywhere: Adafruit's ESP32 driver
  //* grows its RMT encoding buffer to the largest frame ever transmitted and never
  //* shrinks it (esp.c:54-61, ~96 bytes per LED), so one 200-LED frame permanently
  //* commits ~18.8 KB — and equally, a show() at the default length on a 28-LED
  //* mask would waste 13.5 KB. There is deliberately no show() before this line.
  //* See services/led_config.h.
  apply_led_config();

  //* Which generated animation (if any) runs for songs with no .echoes show.
  //* Off by default: with it off, the PCM tap costs nothing on the audio path.
  apply_led_anim_config();

  // ── Strip self-test (diagnostics — remove once the mask is verified) ──────
  // Flashes the whole strip red/green/blue so a dead strip is visible in
  // seconds, independent of the lightshow engine or network.
  for (int i = 0; i < (int)majora_led_count; i++)
    majora.setPixelColor(i, majora.Color(15, 0, 0));
  majora.show();
  delay(200);
  for (int i = 0; i < (int)majora_led_count; i++)
    majora.setPixelColor(i, majora.Color(0, 15, 0));
  majora.show();
  delay(200);
  for (int i = 0; i < (int)majora_led_count; i++)
    majora.setPixelColor(i, majora.Color(0, 0, 15));
  majora.show();
  delay(200);
  for (int i = 0; i < (int)majora_led_count; i++)
    majora.setPixelColor(i, 0);
  majora.show();
  Serial.printf("[Boot] Strip self-test done on %u LEDs — free heap %u KB\n",
                (unsigned)majora_led_count,
                (unsigned)(ESP.getFreeHeap() / 1024));
  heap_probe("strip test");

  Wire.begin(i2c_sda, i2c_scl);
  Serial.printf("I2C initialized (SDA=%d, SCL=%d)\n", i2c_sda, i2c_scl);
  heap_probe("Wire.begin");

  rtc_gpio_hold_dis((gpio_num_t)latch); // Release any hold from previous restart
  pinMode(latch, OUTPUT);
  digitalWrite(latch, HIGH); // Latch power on

  //* Two-button input: PROG (GPIO0) + the external button on `button` (GPIO4).
  //* buttons_begin() owns both pins' modes (pull-up for the active-LOW PROG
  //* button, pull-down for the active-HIGH external one) — see services/buttons.h
  //* for the gesture ladder and services/controls.h for what each gesture does.
  buttons_begin(on_button_event);

  delay(1000);
  heap_probe("button+delay");

  setup_SDCARD(); //* Initializes SD card
  heap_probe("setup_SDCARD");

  //* A staged firmware image is applied here, before WiFi, I2S or the decoder
  //* have claimed any RAM (and before the ~1 s SD benchmark, which would be
  //* wasted work on the way to a reboot). Ignored when the file is absent, so
  //* the ordinary boot path never touches flash.
  //* See updatefromSD() in components/SDCARD/setup.h.
  if (SD_MMC.exists("/firmware.bin"))
    updatefromSD();

  //* Raw I2S output self-test — must run before a mode claims I2S_NUM_0
  //* (enable with -DI2S_TEST_BEEP=1)
  i2s_output_test();

  //* One-shot microSD throughput measurement (enable with -DSD_BENCHMARK=1)
  sd_benchmark();

  // load_lightshow("/storms.echoes");

  //* --- MODE SELECTION ---
  //* Mode is read from NVS. Triple-click PROG at runtime to switch.
  active_mode = load_mode();
  heap_probe("load_mode");

  if (active_mode == MODE_BLUETOOTH)
    setup_bluetooth_mode();
  else
    setup_wifi_mode();

  heap_probe("mode setup done");

  //* Control layer: needs the mode to be up (it pushes the saved volume into
  //* whichever backend came up, and subscribes to the phone's transport state in
  //* Bluetooth mode). Gestures are meaningless before this runs, so it is the
  //* last thing setup() does.
  controls_begin();

  Serial.println("\n---------------------------------------------------");
  Serial.println("----------------  END OF SETUP --------------------");
  Serial.println("---------------------------------------------------\n");
  esp_task_wdt_reset();
}

void loop()
{
  // Only pump OTA while its server is actually listening: in Bluetooth mode the
  // radio is off and ArduinoOTA was never started, and after a standalone show
  // tears WiFi down the UDP socket is gone (see stop_OTA()).
  if (ota_running())
    ArduinoOTA.handle();

  if (active_mode == MODE_WIFI)
  {
    handle_wifi_portal();
  }

  // Audio + lightshow pump — runs regardless of WiFi state, so a standalone
  // show keeps going after WiFi is torn down.
  run_audio_lightshow();

  // Button gestures. Polled (not interrupt-driven) — see services/buttons.h for
  // why GPIO0's node cannot be trusted with an edge ISR.
  buttons_poll();

  // The strip overlay (services/ui_feedback.h) owns the strip while it reports a
  // gesture; run_lightshow()/run_led_animation() stand down while it does, so this
  // runs after them and never draws a second frame on top of a live show.
  ui_feedback_pump();

  // State cues that were posted from an event callback rather than played on the spot
  // (the Bluetooth connection cue). One DMA period per pass, so this never blocks the
  // loop: the I2S transmit buffer absorbs the jitter between passes.
  cue_wire_pump();
  // handle_led_scripts();
  read_adc();
  esp_task_wdt_reset();
}
