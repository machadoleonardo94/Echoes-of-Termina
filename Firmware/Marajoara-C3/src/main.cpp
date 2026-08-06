//! Warning: GoHorse Develpment method was used to create this code.
//! It's presented as an "it just works" solution for Arduino users migrating to VSCode
//! and is not intended to be a best practice example of C++ programming.

//? Lux et Portabilis - Mavuika Let it Go firmware
//? Basic devboard firmware for smart LED strips, accelerometer, buttons and servo output.

//* Dependencies
#include "shared/dependencies.h"

//? strip: the NeoPixel object for controlling external LED strip
//? led_strip: the struct holding the current state of the LED strip (color, brightness, etc.)
//? onboard_led: the NeoPixel object for controlling the onboard status LED
//? status_led: the struct holding the current state of the onboard status LED

void setup()
{
  // Initialize serial communication for debugging
  Serial.begin(115200);
  delay(100);

  Serial.println("\n\nESP32-C3 LED Control Started");
  Serial.println("===========================");

  setup_ESP32();

  strip.begin();
  strip.show(); // Initialize all pixels to 'off'
  Serial.println("LED strip initialized");

  majora.begin();
  majora.show();

  Wire.begin(i2c_sda, i2c_scl);
  Serial.printf("I2C initialized (SDA=%d, SCL=%d)\n", i2c_sda, i2c_scl);

  pinMode(button, INPUT_PULLDOWN);
  pinMode(latch_enable, OUTPUT);
  digitalWrite(latch_enable, HIGH); // Latch power on

  delay(1000);

  // setup_WIFI();

  load_lightshow("/storms.echoes");
}

void loop()
{
  ArduinoOTA.handle();

  update_strip();

  serial_outputs();

  // Short press (< 500 ms) starts the lightshow; long press handled by check_power_button
  static bool btn_prev = false;
  static uint32_t btn_down_at = 0;
  bool btn_now = digitalRead(button);
  if (btn_now && !btn_prev)
    btn_down_at = millis();
  if (!btn_now && btn_prev && (millis() - btn_down_at < 500))
  {
    if (!is_lightshow_playing())
    {
      Serial.println("[Button] Short press — starting lightshow");
      start_lightshow();
    }
    else
    {
      Serial.println("[Button] Short press — stopping lightshow");
      stop_lightshow();
    }
  }
  btn_prev = btn_now;

  run_lightshow();

  if (!is_lightshow_playing())
    run_majoras();

  check_power_button();
}
