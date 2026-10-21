#if !defined(PROJECT_GLOBAL_OBJECTS)
#define PROJECT_GLOBAL_OBJECTS

#include "shared/dependencies.h"
#include "pinout.h"

//* ---------------------- GLOBAL OBJECT INSTANCES ----------------------

// Bluetooth A2DP sink (receives audio from a paired device)
BluetoothA2DPSink a2dp_sink;

// FTP server (power-user file management over the network)
FtpServer ftpSrv;

//* Preferences (NVS)
Preferences internal_storage;

//* Addressable LEDs
//
// The strip length is a RUNTIME setting (see services/led_config.h), because the
// same firmware drives either the 28-LED Majora mask or a 28..200-LED strip. That
// splits the old single constant into three things:
//
//   MAJORA_MAX_LEDS          compile-time array bound. Every per-LED buffer is
//                            sized with this, so it can never be overrun by a
//                            configured count. NOT a transmit length.
//   MAJORA_LED_COUNT_DEFAULT boot default when NVS holds no setting; also the
//                            constructor argument below, which only seeds the
//                            initial allocation (updateLength() replaces it).
//   majora_led_count         the live count (shared/variables.h). This is what
//                            every loop, bound check and wildcard must use.
//
// Why the NeoPixel object is not simply constructed at MAX: show() transmits
// numLEDs pixels, and a WS2812 frame costs ~29 us per LED (Adafruit's ESP32 path
// drives the RMT peripheral and blocks for the whole frame — esp.c:107). A
// 200-LED object would therefore spend ~6 ms per show() even for the 28-LED mask.
// updateLength() is called at boot with the configured count so the transmit
// length matches the hardware actually attached.
#define MAJORA_MAX_LEDS 200
#ifndef MAJORA_LED_COUNT_DEFAULT
#define MAJORA_LED_COUNT_DEFAULT 144
#endif
Adafruit_NeoPixel majora(MAJORA_LED_COUNT_DEFAULT, LED_output, NEO_RGB + NEO_KHZ800); // 5mm PTH LEDs on Majora's Mask

#endif // PROJECT_GLOBAL_OBJECTS
