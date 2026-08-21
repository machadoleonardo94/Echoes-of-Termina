//? ------------------------------------------------------------------------------------------
//*! This file contains all the common definitions and includes for the project.
//? ------------------------------------------------------------------------------------------

#ifndef PROJECT_GLOBAL_DEPENDENCIES
#define PROJECT_GLOBAL_DEPENDENCIES

#define FIRMWARE_BUILD 8

//? ---------------------- Framework --------------------------
#include <Arduino.h>

//* ----------------------- SHARED ----------------------------
//* Constants
#include "constants.h"
//* Pinout
#include "pinout.h"

//* ---------------------- Libraries --------------------------
//* WiFi
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <WiFiManager.h>
//* Watchdog
#include <esp_task_wdt.h>
//* Non-volatile Storage
#include <Preferences.h>
#include <LittleFS.h>
//* SD Card
#include <SPI.h>
#include <FS.h>
#include <SD_MMC.h>
#include <Update.h>
//* Over the Air update
#include <ArduinoOTA.h>
//* Timezone
#include <time.h>
//* Bluetooth audio
#include <btAudio.h>
//* Fancy LED WS2812B
#include <Adafruit_NeoPixel.h>
//* I2C 0.96" Screen
#include <Wire.h>

//* Library Objects
// After libraries, before functions
#include "library_objects.h"
//* Variables
#include "variables.h"

//* ---------------------- PROJECT FIRMWARE IMPLEMENTATIONS --------------------------
//* Utilities:
// If any
//* Components:
#include "components/ESP32/setup.h"
#include "components/SDCARD/setup.h"

//* Services:
#include "services/serial_outputs.h"
#include "services/wifi_settings.h"
#include "services/led_scripts.h"
// #include "services/lightshow.h"
#include "services/wifi_portal.h"
#include "services/mode_manager.h"
#include "services/readings.h"

#endif