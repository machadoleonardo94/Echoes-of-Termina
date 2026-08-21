#if !defined(PROJECT_GLOBAL_OBJECTS)
#define PROJECT_GLOBAL_OBJECTS

#include "shared/dependencies.h"
#include "pinout.h"

//* ---------------------- GLOBAL OBJECT INSTANCES ----------------------

// Sets the name of the audio device
btAudio audio = btAudio("Echoes_of_Termina");

//* Preferences (NVS)
Preferences internal_storage;

//* Addressable LEDs
#define MAJORA_LEDS 28
Adafruit_NeoPixel majora(MAJORA_LEDS, LED_output, NEO_RGB + NEO_KHZ800); // 5mm PTH LEDs on Majora's Mask

#endif // PROJECT_GLOBAL_OBJECTS
