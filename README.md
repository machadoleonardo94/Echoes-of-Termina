# Echoes of Termina

An audio-visual devkit built from simple parts: a battery-powered ESP32 board with
a stereo speaker output, addressable LEDs, a microSD card and two buttons — plus the
desktop tool that authors the lightshows it plays.

![Echoes V1](Media/echoes-v1.jpg)

| | |
|---|---|
| **Hardware** | ESP32-WROOM-32 board, 2x MAX98357A I2S amplifiers, WS2812B output, microSD, TP4056 charger, 2 buttons |
| **Firmware** | Bluetooth A2DP sink, local SD playback, `.echoes` lightshow decoder, Wi-Fi control panel |
| **Desktop** | Python/PySide6 editor: drop in an MP3, design the LED animation, upload the project to the board |
| **Status** | v0.8.0 pre-release — bench bring-up done, mask full board validation pending |

The project is three separable pieces that meet at the microSD card and the
`.echoes` script format: `Hardware/` (KiCad boards), `Firmware/` (PlatformIO /
Arduino for ESP32), and `Executable/` (the Windows editor and analyser).

---

## Hardware

![PCB and mask render](Media/pcb-and-mask-render.jpg)

Deliberately small bill of materials — an ESP32 module, two I2S class-D amplifiers, a
microSD socket, a charging IC, a handful of passives and the LED chain.

| Block | Part | Notes |
|---|---|---|
| MCU | ESP32-WROOM-32 N8R2 / 32UE | 8 MB flash, 2 MB PSRAM (see the pinout caveat below) |
| Speaker output | 2x MAX98357A (I2S) | stereo pair, channel chosen per amplifier by solder jumper; 4 Ω / 3 W drivers on PicoBlade connectors |
| Microphone input | INMP441 on I2S_NUM_1 | wired and pinned out, **not tested and not coded** |
| Storage | microSD over 1-bit SDMMC | CLK / CMD / D0; hinged socket on the mask boards, spring-loaded tray on Echoes V2 |
| LED output | 5 V WS2812B data line (`Strip_out`) | 1 to 200 pixels, brightness ceiling scaled to the count |
| Charging | TP4056 + USB-C | charge status LEDs |
| Power | AP2112K-3.3, PMOS latch on `latch_enable` | 2x 220 uF polymer + 100 nF decoupling |
| Buttons | PROG (GPIO0, active low) + external (active high) | polled, debounced gesture layer |

### Boards

| Directory | What it is |
|---|---|
| `Hardware/PCBs/Echoes_of_Termina/` | The bench devkit board — the reference for the pinout and the first board to come up |
| `Hardware/PCBs/Echoes_V2/` | A second board revision kept as the upgrade path from `Echoes_of_Termina` — the bench board that takes a spring-loaded microSD tray |
| `Hardware/PCBs/Marajoara_V3/` | The mask board: ESP32-WROOM-32UE, 10 onboard WS2812B, DF13 ports for the LED strips, PSRAM usable |
| `Hardware/PCBs/Marajoara_V2/`, `Hardware/PCBs/Marajoara_PCB/` | Earlier revisions, kept for reference and upgrade paths |
| `Hardware/PCBs/LED breakout boards/Top_6_WS2812B/`, `Mid_2_WS2812B/`, `Bottom_8_WS2812B/` | Small daisy-chained LED carrier strips for the mask (6 / 2 / 8 pixels) |
| `Hardware/Printed parts/Basic Echoes case/` | Enclosure STLs for the bench board, plus its Fusion 360 archive (`Echoes box.f3z`) |
| `Hardware/Printed parts/Speaker_40mm_Case/` | Parametric OpenSCAD case for a 40 mm driver, with measured acoustics — see its own [README](Hardware/Printed%20parts/Speaker_40mm_Case/README.md) |

Every KiCad project lives under `Hardware/PCBs/` in its own directory, with that
board's fabrication outputs in its `production/` subdirectory; enclosure and printed
parts live under `Hardware/Printed parts/`.

The ESP32-C3 variant in `Media/` and `Firmware/Marajoara-C3/` has no board directory
here: it is legacy, has no microSD slot and no audio stage, and keeps its show in
internal flash instead.

![Echoes V1 PCB render](Media/echoes-v1-pcb-render.jpg)

### Pinout

Compile-time, selected per environment (`src/shared/pinout.h`); defining two
boards, or none, is a build error rather than a silent fallback.

| Signal | Echoes V1 (bench) | Marajoara V3 |
|---|---|---|
| I2S speaker DOUT / BCLK / LRC | 16 / 17 / 18 | 13 / 27 / 18 |
| I2S mic SCK / WS / SD | 27 / 26 / 34 | 26 / 25 / 34 |
| microSD CLK / CMD / D0 | 14 / 15 / 2 | 14 / 15 / 2 |
| LED data | 25 | 23 |
| Second LED data *(planned)* | — | 33 |
| Charger `CHRG#` / `STDBY#` *(planned)* | — | 4 / 35 |
| External button | 4 | 19 |
| Power latch | 13 | 32 |
| ADC input | 36 | 36 |
| I2C SDA / SCL | 21 / 22 | 21 / 22 |

The two rows marked *planned* belong to the next Marajoara revision, which is awaiting
manual routing and DRC; no built board carries them yet. They are recorded here because
that revision closes the pin budget: GPIO39 is then the only free pin, with GPIO5 and
GPIO12 left to their strapping roles and GPIO1/GPIO3 to the UART0 console. On V3 the
external button input is fed by a touch module, which presents itself as an ordinary
active-high press — no firmware difference.

The module's PSRAM chip owns GPIO16 and GPIO17 internally. Echoes V1 puts I2S on
both of those pins, so that environment must build with PSRAM disabled — the pinout
header asserts this and refuses to compile a contradictory combination. Marajoara V3
moved the amplifier onto pins the 1-bit SD bus no longer needs, which is what makes
PSRAM usable there.

---

## Firmware

Two mutually exclusive modes, chosen at boot from NVS and switchable at runtime:

**Bluetooth mode** — the board is an A2DP sink (`ESP32-A2DP`). The phone streams SBC
over the air, the sink decodes it and drives I2S directly: no file, no card. Feedback
tones and the volume overlay are mixed into the same output path.

**Wi-Fi mode** — the board joins a network (WiFiManager captive portal) and serves an
HTTP control panel on port 80, advertising itself over mDNS as `echoes.local`. The
portal lists the card, starts and pauses playback, sets volume, adjusts the LED strip,
manages files, stages a firmware image and reboots.

Local playback is the ESP32-audioI2S decoder reading MP3/AAC/WAV from the card —
there is no OPUS or Vorbis decoder in the firmware, which is why the editor
transcodes.

### Lightshow playback

`.echoes` scripts are not loaded into RAM. The decoder streams them off the card with
the playhead, so exactly one command is resident at a time and a long show costs the
same memory as a short one; seeks and rewinds re-scan from the start of the file. A
file that is not time-sorted is rejected at load rather than played as a burst.

A show follows the SD player's position. The same engine also runs a standalone show
with the radio switched off, which is what makes a card-plus-battery mask work
without a phone or a network.

### Two-button interface

Two buttons, one gesture engine. `services/buttons.h` turns pin levels into
debounced gestures and knows nothing else; `services/controls.h` decides what each
gesture does — it is the only place the interface is defined, so the map below can be
argued about (and changed) without touching the state machine.

| Button | Pin | Idle level | Note |
|---|---|---|---|
| `PROG` | GPIO0 | high, press shorts to GND | onboard boot button, single-purpose |
| `EXT` | GPIO4 | low, press feeds 3V3 | external button; a touch module on Marajoara V3 presents the same active-high press |

#### Gesture map

| Gesture | When it fires | `PROG` (GPIO0) | `EXT` (GPIO4) |
|---|---|---|---|
| **click** | released before 600 ms — and, on `EXT`, no further press within 400 ms | play / pause — with nothing loaded in Wi-Fi mode, starts a standalone show | next song — with nothing loaded in Wi-Fi mode, starts a standalone show |
| **double** | two presses in one burst | — | cycle the LED animation: none → envelope → FFT |
| **triple** | three presses in one burst | — | switch mode (reboots the board) |
| **hold / repeat** | still down at 600 ms, then one repeat every 150 ms | volume down, 5 % per tick | volume up, 5 % per tick |
| **very long** | still down at 4000 ms | restart the current song | bring Wi-Fi back after a standalone show tore it down |

The two buttons are deliberately asymmetric. `PROG` is single-purpose, so its clicks
are committed **the instant the button comes up** — no 400 ms wait. `EXT` carries the
full multi-click ladder, and only its clicks pay that window, because at the moment of
release a second tap may still be coming. The window is a per-button property
(`multi_gap_ms`), not a global constant.

#### How a press is classified

Every press must survive a 25 ms debounce before the ladder even sees it. Then,
per button:

| Tier | Fires when | Result |
|---|---|---|
| `MIN_PRESS` | the press ends within 50 ms of starting | discarded as an electrical blip; an open click burst is left untouched |
| `CLICK` | released before 600 ms | `BTN_EV_CLICK` — `PROG` commits it on release; `EXT` waits 400 ms with no further press first |
| `DOUBLE` | two presses in one burst (`EXT` only) | `BTN_EV_DOUBLE` |
| `TRIPLE` | three presses in one burst (`EXT` only) | `BTN_EV_TRIPLE` — a fourth press stays a triple |
| `HOLD` | still down at 600 ms | `BTN_EV_HOLD`, then `BTN_EV_REPEAT` every 150 ms, then `BTN_EV_RELEASE` on let-go |
| `VERY_LONG` | still down at 4000 ms | `BTN_EV_VERY_LONG` once — it stops the repeat train |

A hold swallows the burst it interrupted: press-press-hold is a `HOLD`, never a
`DOUBLE` followed by a hold. A very-long hold also undoes the volume ramp its own
first four seconds performed, so "restart this song" never leaves the volume yanked to
one end.

#### Feedback

Each action answers back, because the two backends look identical from the outside:

- **Tones** — `audio_cues.h` mixes short cues into the audio (play, pause, next,
  confirm, deny, mode-switch arpeggio, and a rising/falling volume tick whose pitch
  reports the level; cues mix after volume control so they stay audible at any
  setting).
- **Strip overlay** — `ui_feedback.h` draws a volume bar while a ramp runs; the
  animation cycle blinks 1 / 2 / 3 times in blue to say *which* animation was landed
  on (the audio-reactive modes otherwise look identical while silent); a triple-click
  gets three blocking white flashes before the reboot; the very-long Wi-Fi restore
  gets two green flashes.
- **Serial log** — every gesture prints `[BTN] PROG/EXT <event> (<held> ms)`.

The diagnostic build (`echoes-v1-btn`, `-DBUTTON_TRACE=1`) logs every raw edge and a
5-second idle-level census for both pins, so a dead wire or stuck button is visible
immediately — and it refuses the destructive gestures (mode switch, standalone show)
so a misread cannot cost the verification session.

The full evaluation, including measured press timings, the state diagrams and the
design decisions behind the map, is in
[`Firmware/echoes_of_termina/docs/two-button-ui.md`](Firmware/echoes_of_termina/docs/two-button-ui.md).

### Portal API

| Route | Purpose |
|---|---|
| `GET /api/status`, `/api/files` | device state, directory listing |
| `POST /api/play`, `/api/stop`, `/api/pause`, `/api/next`, `/api/prev` | transport |
| `POST /api/volume`, `/api/leds` | volume (persisted to NVS), strip length and brightness |
| `POST /api/upload`, `/api/download`, `/api/delete`, `/api/rmdir`, `/api/mkdir`, `/api/rename` | file management |
| `POST /api/stage-firmware`, `/api/reboot` | SD firmware update |

### Update paths

- **OTA (over the air, from the computer):** ArduinoOTA on boards in Wi-Fi mode (`espota`
  upload). The image travels over the network and is applied live.
- **Drop-in update (recommended, from the SD card):** an app image on the card as
  `/firmware.bin`, applied when the board is power-cycled — before Wi-Fi, I2S and the decoder
  claim RAM. How the image reached the card does not matter and the firmware cannot tell:
  `POST /api/stage-firmware` from a browser and a card moved to a PC reader both leave the same
  file in the same place.
- **Serial (first flash, from a USB-to-serial adapter on `PORT1`):** the bootloader and
  partition table — and the base image on a blank board — come from a PlatformIO session
  over the UART0 console on GPIO1/GPIO3. There is no data path over the USB-C connector,
  and no bridge to the card, by design; see the storage limitation below.
- **USB-C:** charging only. The connector carries power and nothing else, so once the
  board has a runnable image there is no reason to reach for a cable.

### Build

```powershell
cd Firmware\echoes_of_termina
pio run -e marajoara-v3            # mask board
pio run -e echoes-v1               # bench board
pio run -e echoes-v1 -t upload     # flash the bench board over OTA
```

| Environment | Board | Notes |
|---|---|---|
| `echoes-v1` | Echoes V1 | PSRAM disabled, 8 MB partitions |
| `esp32-N16` | Echoes V1 | 16 MB flash, no PSRAM |
| `marajoara-v3` | Marajoara V3 | PSRAM enabled |
| `echoes-v1-btn` | diagnostic | logs every button edge, refuses destructive gestures |
| `marajoara-v3-bench` | diagnostic | one-shot microSD throughput benchmark |

The two diagnostic environments are not release images.

---

## The script format

One command per line, absolute timestamps against the audio file, LED addresses given
as `*` (all), a range `7-8`, or a list `4,11`:

```
0:07.226  SET  150 150 0        4,11   # set a colour now
0:07.327  FADE 150 150 0 80     4,11   # decay it, 80 % brightness lost per second
0:13.599  RAMP 0:03 0 0 255 255 0 0 *  # blue to yellow over three seconds
```

`SET` writes a colour, `FADE` decays it at a rate, `RAMP` interpolates between a
start and an end colour across a duration. Fades are time-based rather than
frame-based, so a strip of any length animates at the same speed.

A project file keeps one `[TrackName]` section per track, and a track may carry a
human label after its header — shown in the editor's track selector, and ignored by
the firmware (which skips `[...]` lines entirely):

```
[Slot 4] Column + Eyes (Drums)
# Imported from notes.mid
# Mask sector: Column + Eyes
# Instrument: Drums

0:04.032  FADE 32 96 224 99  9-12
```

---

## Desktop editor

![Editor — Lost Woods project](Media/editor-lost-woods.jpg)

`Executable/echoes/` is a PySide6 application. You give it an MP3, it plays the track
while you place animations on the LED map, and it uploads the finished project to the
board over the portal — no cable, no card swap.

- LED canvas: the mask layout is loaded from DXF artwork in `assets/`, so redrawing
  the artwork moves the zones instead of requiring code changes. The default board is
  the 28-zone Majora mask; a plain strip is just a different layout.
- Script editor and command toolbox: add `SET`/`FADE`/`RAMP` at the current playhead
  with a click, with a timestamp readout driving the write position.
- Note highway and envelope views for timing against the music.
- Importers: Clone Hero `.chart` and MIDI, both mapped onto the LED ring so a chart
  becomes a lightshow without hand-authoring.
- Spectrum analyser window (also standalone via `Run Spectrum.bat`) for picking
  frequencies before deciding where an animation lives.
- Track slots, mixdown of multiple audio stems to one MP3, macro hotkeys, metadata
  (`song.ini`), and a hardware-version converter that remaps LED numbering between
  board revisions.
- Device panel: mDNS discovery of `echoes.local`, file browser, upload, and remote
  play/pause.

Requirements: Python 3.10+ with PySide6 (installed automatically on first run) and
`ffmpeg` on `PATH` if the source audio is not already MP3. Launch with
`Run Echoes.bat`.

![Standalone FFT spectrum analyser](Media/spectrum-analyser.jpg)

An importer is included for translating .chart and .midi files used in Clone Hero projects.
The importer composes the label as `zone (instrument)` — "Column + Eyes" + MIDI track
`PART DRUMS` → `Column + Eyes (Drums)` (the `PART ` prefix is dropped and shouting
names are title-cased; a bare `.chart` section such as `ExpertSingle` contributes no
instrument) — and writes `# Mask sector:` / `# Instrument:` comments into the body so
the saved project documents where the track came from. The track selector shows the
label alone (falling back to the stored section name for unlabelled tracks), and
Script Properties labels its rows *Track 1..N* and lets you edit each label, whose
field shows the full `zone (instrument)` text. Merged mask exports carry no headers,
so labels never reach the board.

![Importer — Template](Media/Importer-from-Clone-Hero.jpg)


---

## Demo

| Clip | Shows |
|---|---|
| [Song of Storms test](Media/song-of-storms-test.mp4) | A `.echoes` lightshow played back on the mask |
| [FFT strip test](Media/fft-strip-test.mp4) | The generated spectrum animation driving a strip |

Additional board photos in `Media/`: `c3-testfit.jpg` and `c3-no-audio.jpg`
show the C3 variant. It is **legacy** — kept for the record and visible in the photos,
not a board to build today — and it differs from the ESP32 designs in ways that matter:
it has **no microSD slot** and no audio stage, so the show lives in the module's
internal flash filesystem (`LittleFS`, uploaded with `pio run -t uploadfs`), which is
about 1.4 MB on the 4 MB module against a card that is measured in gigabytes. That
build also reads the whole script into RAM rather than streaming it, supports `SET` and
`FADE` only, and drives a fixed 28-pixel chain. See `Firmware/Marajoara-C3/`.

| C3 test fit | C3 board, no audio stage |
|---|---|
| ![C3 test fit](Media/c3-testfit.jpg) | ![C3 without audio](Media/c3-no-audio.jpg) |

---

## Repository layout

| Path | Contents |
|---|---|
| `Hardware/` | KiCad projects under `PCBs/` (each with its fabrication outputs), printed-part sources under `Printed parts/`, DXF artwork and STEP/STL models |
| `Firmware/echoes_of_termina/` | The ESP32 firmware — components, services, shared pinout, partition tables |
| `Firmware/Marajoara-C3/` | Legacy C3 build: lightshow player with no audio and no SD slot, shows held in the on-flash filesystem |
| `Executable/echoes/` | Desktop editor, FFT analyser, `projects/` folder, release builder |
| `Datasheets/` | Vendor datasheets (MAX98357A) |
| `Media/` | Photos, renders and demo clips used by this document |
| `dist/` | Packaged pre-releases: editor ZIP, firmware images, checksums |

---

## Status and known limitations

- **Bluetooth and SD playback both work**, but the portal cannot decode MP3 in
  parallel with serving the page. On a board without PSRAM the radio alone costs
  about 45 KB of internal RAM, and the I2S DMA buffers need ~32 KB of DMA-capable
  internal RAM before the decoder has any; below a free-heap threshold the portal
  drops Wi-Fi and hands the track to the standalone player instead. Design the
  feature around that rather than against it. The levers that should move it are in the
  build, not in the chip: `CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY` puts the BSS of
  lwIP, net80211, pp and bt into PSRAM, `CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP` prefers PSRAM
  for the Wi-Fi and lwIP buffers, and `CONFIG_SPIRAM_SPEED_80M` raises the card clock.
  **None of the three has been tried or measured** — they are the next experiment, on
  Marajoara V3, the only board here with PSRAM switched on. PSRAM does not delete the
  internal reserve even then: IDF holds back 32 KB of DMA-capable internal RAM regardless
  (`CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL`, default 32768), because DMA into external RAM
  has real limits.
- **The microphone is unbuilt.** The INMP441 is on the board and on its own I2S
  peripheral with its own pins, but there is no capture path in the firmware and
  nothing has been measured.
- **The C3 variant is legacy.** No microSD slot and no audio: a show has to fit in the
  module's internal flash, it is loaded into RAM wholesale rather than streamed, and
  the script dialect stops at `SET` and `FADE`. It survives in the photos and in
  `Firmware/Marajoara-C3/`, not as a supported target.
- If driving a large LED strip, the board cannot handle the FFT animation and audio gets choppy.
- The MAX98357A channel-select resistors decide which amplifier carries which channel;
  verify them per board before trusting a stereo test. The default value is 1M for (L+R)/2. Swap for 10k for Left channel, and 220k for Right Channel
- Marajoara V3 board is awaiting manual routing and DRC before production. It adds a second LED channel, carries the pin remap forced by the PSRAM errors found on Echoes V1, and pairs the cell-voltage ADC with `CHRG#`/`STDBY#` taps on the charger, so the firmware can tell charging from full from discharging — the pins are in the table above, the rules in `Firmware/echoes_of_termina/docs/two-button-ui.md` §13.
- FTP server would be a nice file management solution, but is also memory limited.
- **The ESP32-S3 cannot carry this firmware as it stands, and the reason is the radio, not
  the RAM.** Espressif lists Bluetooth Classic (BR/EDR) as ESP32-only; the S3 is Bluetooth LE
  only, and A2DP — the profile the sink speaks — is a Classic one, so Bluetooth mode cannot be
  built on an S3 at all, and LE has no media-streaming profile this project could use instead.
  The S3's other famous gap, the internal I2S DAC, does not apply here: the MAX98357A is the
  DAC. Everything else in the BOM is present on the S3 — two I2S peripherals for the
  speaker and the INMP441, SDMMC, RMT for the LED data, up to 8 MB of octal PSRAM, an LX7
  core with SIMD — but not more room where it counts: Espressif's own comparison puts a
  Wi-Fi station at 273 KB free on an S3 against 230 KB on an ESP32, and the ~32 KB of
  DMA-capable internal RAM the decoder waits behind is held back on both. It is still a new
  board rather than a substitution:
  GPIO22-25 do not exist on the S3 and carry the LED data line and I2C SCL today, the ADC is
  GPIO1-20 only, `latch` on GPIO32 is not an RTC GPIO there and would not hold through a
  reset, and an octal-PSRAM module takes GPIO33-37 for flash and PSRAM — which is where the
  mic data line and the ADC input live now. It is a fallback, not a plan: board work stays on
  the ESP32 N8R2, and an S3 is judged against Marajoara V3's measurements rather than instead
  of them.
- **There is no on-board data path to the card, deliberately.** File management is the Wi-Fi
  portal or pulling the card and using a reader on the PC; USB-C on the board is a charging
  port and nothing else. A bridge MCU between the host and the ESP32 was the
  alternative and was rejected: it needs a second chip, a link and a protocol on both sides, and
  the card still ends up as the interface for anyone who will not use Wi-Fi — which makes the
  socket itself a product decision rather than a BOM line. The mask boards keep the hinged
  socket, which holds a seated card against a device that gets carried and hung; Echoes V2,
  which lives on a bench, takes a spring-loaded tray so the card can be swapped without
  fingernails.
- **A USB bridge was dropped on cost, not on speed.** The speed was never the binding
  constraint: a project is 2–5 MB — one song plus its lightshow — and uploads happen one
  project at a time. USB Full-Speed tops out at 12 Mbit/s (about 1 MB/s), and a plain
  USB-serial bridge at 2 Mbaud carries roughly 180 KB/s, which still moves a 5 MB project
  in well under a minute; a cheap CH340G-class chip would do the job. What ruled it out
  was the price of the convenience: a second chip, a link and a protocol on both sides,
  for file movement the Wi-Fi portal already covers — while bulk library management is
  what an SD card reader is for, at several hundred times the throughput. So the budget
  went elsewhere: one project at a time drops over the Wi-Fi portal, and the library is a
  card reader's job.
