//? ------------------------------------------------------------------------------------------
//*! This file contains all the common definitions and includes for the project.
//? ------------------------------------------------------------------------------------------

#ifndef PROJECT_GLOBAL_DEPENDENCIES
#define PROJECT_GLOBAL_DEPENDENCIES

#define FIRMWARE_BUILD 9

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
//* mDNS (so the portal is reachable at http://echoes.local)
#include <ESPmDNS.h>
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
//* FTP server (serves SD_MMC; storage type selected via build flags in platformio.ini)
#include <SimpleFTPServer.h>
//* Timezone
#include <time.h>
//* Bluetooth A2DP audio (sink)
#include <BluetoothA2DPSink.h>
//* MP3/AAC/FLAC decoder + I2S audio player (ESP32-audioI2S)
#include <Audio.h>
//* Fancy LED WS2812B
#include <Adafruit_NeoPixel.h>
//* I2C 0.96" Screen
#include <Wire.h>
//* Over the Air update
#include <ArduinoOTA.h>

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
// Diagnostics for the boot-time heap corruption (temporary)
#include "services/heap_probe.h"
// The cue vocabulary (kinds + the two entry points) on its own, BEFORE the services
// that request a state cue: the WiFiManager portal callback lives in wifi_settings.h
// and mode_manager.h announces the Bluetooth state right after a2dp_sink.start(), and
// both of them are compiled before audio_cues.h can be. See audio_cues_api.h.
#include "services/audio_cues_api.h"
#include "services/wifi_settings.h"
#include "services/led_scripts.h"
// LED strip configuration (runtime count + brightness ceiling). Must precede
// lightshow.h, which walks the strip with the configured count.
#include "services/led_config.h"
// UI feedback overlay — the strip as the interface's answer. Before lightshow.h
// and led_animations.h on purpose: both of them stand down while the overlay owns
// the strip, so they need ui_feedback_active() above them.
#include "services/ui_feedback.h"
// Lightshow engine — must precede wifi_portal.h, which attaches shows to audio
#include "services/lightshow.h"
// Generated LED animations (PCM tap + envelope). After lightshow.h, which it
// yields to, and before wifi_portal.h, which pumps it.
#include "services/led_animations.h"
#include "services/wifi_portal.h"
#include "services/mode_manager.h"
// Button input (pin levels → gestures) and the action layer that binds gestures
// to features. buttons.h is the state machine, controls.h the interface; both
// come after wifi_portal.h and mode_manager.h because the actions they call live
// there.
#include "services/buttons.h"
// The two answer channels the interface uses: tones mixed into the Bluetooth
// stream (audio_cues.h) and the strip overlay (ui_feedback.h, included above the
// LED renderers so they can yield to it). Both are called by controls.h.
#include "services/audio_cues.h"
#include "services/controls.h"
#include "services/readings.h"

#endif