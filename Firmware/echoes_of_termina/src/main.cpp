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
  setup_GPIOs(); //* Configures GPIOs

  majora.begin();
  majora.show();

  Wire.begin(i2c_sda, i2c_scl);
  Serial.printf("I2C initialized (SDA=%d, SCL=%d)\n", i2c_sda, i2c_scl);

  pinMode(button, INPUT_PULLDOWN);
  rtc_gpio_hold_dis(GPIO_NUM_13); // Release any hold from previous restart
  pinMode(latch, OUTPUT);
  digitalWrite(latch, HIGH); // Latch power on

  setup_button_interrupt(); //* Interrupt-driven triple-press detection on GPIO 0

  delay(1000);

  setup_SDCARD(); //* Initializes SD card

  // load_lightshow("/storms.echoes");

  //* --- MODE SELECTION ---
  //* Mode is read from NVS. Triple-press the button at runtime to switch.
  // active_mode = load_mode();
  active_mode = MODE_WIFI;

  if (active_mode == MODE_BLUETOOTH)
    setup_bluetooth_mode();
  else
    setup_wifi_mode();

  Serial.println("\n---------------------------------------------------");
  Serial.println("----------------  END OF SETUP --------------------");
  Serial.println("---------------------------------------------------\n");
  esp_task_wdt_reset();
}

void loop()
{
  if (active_mode == MODE_WIFI)
  {
    handle_wifi_portal();
    ArduinoOTA.handle();
  }

  check_button();
  // handle_led_scripts();

  esp_task_wdt_reset();
}
