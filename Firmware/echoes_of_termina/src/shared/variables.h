#if !defined(PROJECT_GLOBAL_VARIABLES)
#define PROJECT_GLOBAL_VARIABLES

//* ---------------------- GLOBAL COMPONENT VARIABLES ----------------------

//* OLED display
bool display_started = false;
//* SD card
bool sdcard = false;

long loopTimer = 0;
long globalTimer = 0;
long loopCounter = 0;

uint32_t adcReading = 0;
uint32_t adcVoltage = 0;
#define MAX_ADC_SAMPLES 8
#define OVERSAMPLING_RESOLUTION 3 // 2^3 = 8 samples

typedef struct buttonState
{
    bool currentState = LOW; // Last confirmed debounced state
    bool lastState = LOW;    // Previous raw reading (used for debounce)
    uint32_t lastDebounceTime = 0;
    uint32_t debounceDelay = 50; // ms
    uint8_t counter = 0;
} struct_buttonState;

struct_buttonState button_states;

bool debugWifi = false;

char macAdress[6] = {0, 0, 0, 0, 0, 0};

#endif // PROJECT_GLOBAL_VARIABLES
