#if !defined(SERVICE_AUDIO_CUES_API)
#define SERVICE_AUDIO_CUES_API

//? ------------------------------------------------------------------------------------------
//*! Audio cues — the vocabulary, on its own.
//*!
//*!  This header exists for one reason: INCLUDE ORDER. The state cues are requested from
//*!  places that are compiled before the cue engine itself can be:
//*!
//*!      services/wifi_settings.h      the WiFiManager config-portal callback (hotspot up)
//*!      services/mode_manager.h       right after a2dp_sink.start() (waiting for a source)
//*!      services/wifi_portal.h        wifi_enable() after a standalone show
//*!
//*!  All three are pulled in by shared/dependencies.h *before* services/audio_cues.h,
//*!  because the engine needs `active_mode`, `a2dp_sink` and `player` to exist first. So
//*!  the enum and the two entry points live here, the engine implements them there, and
//*!  the early headers can call them without knowing anything about how they are played.
//*!
//*!  Two entry points, two intents — see audio_cues.h for the full explanation:
//*!      cue_play(kind)      request a cue; whoever can render it (stream mixer or I2S
//*!                         wire) picks it up on its next pass. Non-blocking, safe from
//*!                         an event callback.
//*!      cue_play_now(kind)  play it HERE and NOW, returning when it is over. For boot
//*!                         and mode-transition states, where nothing else is running
//*!                         and the code after the call needs the wire back.
//? ------------------------------------------------------------------------------------------

#include <stdint.h>

// Kinds, in the order the UI and the state machine use them.
//
// CUE_VOLUME_TICK must stay LAST: it is the only kind whose pitch is supplied by the
// caller (the volume ramp reports the level by pitch, 400 Hz .. 1400 Hz).
//
// The five state cues below it are the ones that answer "what is the board doing?".
// They are grouped and named after the state, not the sound, so the pattern table in
// audio_cues.h reads as the state machine it announces.
enum CueKind : uint8_t
{
    CUE_PLAY = 0,   // rising two-tone: "playing"
    CUE_PAUSE,      // falling two-tone: "paused"
    CUE_NEXT,       // single mid blip
    CUE_CONFIRM,    // rising two-tone, higher: animation / wifi restored
    CUE_DENY,       // falling buzz: nothing to act on
    CUE_MODE,       // three-note arpeggio: mode switch accepted (before the reboot)
    CUE_BT_WAIT,    // Bluetooth mode, no source: "on, and waiting" (once per boot)
    CUE_BT_PAIRED,  // Bluetooth mode, a source connected
    CUE_AP_UP,      // WiFi mode, config hotspot created: "phone, configure me"
    CUE_WIFI_OK,    // WiFi mode, associated and addressed
    CUE_WIFI_FAIL,  // WiFi mode, no network (portal timeout / reconnect failed)
    CUE_VOLUME_TICK,
    CUE_KIND_COUNT
};

// A cue is a short sequence of tones. Frequencies are in Hz, durations in ms;
// a duration with hz == 0 marks a silence gap.
#define CUE_MAX_TONES 4

struct CueTone
{
    uint16_t hz;
    uint16_t ms;
};

// Requests a cue without waiting for it. Never blocks; callable from an event callback
// (the A2DP connection callback does exactly that). Whoever can render it — the stream
// mixer while music plays, the I2S wire while it does not — picks it up on its next pass.
void cue_play(CueKind kind, uint8_t param = 0);

// Plays a cue to completion on the calling task and returns true when it was heard.
// Returns false when the output stage is busy (a stream is playing) or unavailable.
// Blocks for the length of the cue — ~0.3 s — so it is for boot and mode transitions,
// never for a gesture response. See audio_cues.h for the wire rules.
bool cue_play_now(CueKind kind);

// One short tick whose pitch rises with the level (volume ramp).
void cue_volume_tick(uint8_t percent);

// True when tones can be mixed into a live Bluetooth stream (the mid-stream path).
bool cues_available();

// Pumps the direct-to-I2S cue path. Called once per loop(); a no-op outside Bluetooth
// mode and while nothing is playing, so it costs one comparison in the common case.
void cue_wire_pump();

#endif // SERVICE_AUDIO_CUES_API
