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
#define MAJORA_LEDS 28
Adafruit_NeoPixel majora(MAJORA_LEDS, LED_output, NEO_RGB + NEO_KHZ800); // 5mm PTH LEDs on Majora's Mask

#endif // PROJECT_GLOBAL_OBJECTS
