# Nana Presence Node

ESP32-S3 firmware for Nana's removable hardware adapter. Nana Core on the PC
remains the owner of reasoning, identity, memory, and high-level behavior.

Unless a section says otherwise, run the commands below from
`components\presence-node` in the repository checkout.

## Confirmed board

- ESP32-S3 revision 0.2
- Dual-core 240 MHz
- 16 MiB flash
- 8 MiB embedded PSRAM physically present
- CH343 USB-UART bridge on `COM14` during the first bring-up
- OV5640 camera physically present; VGA/JPEG capture is live-verified
- Onboard microSD uses SDMMC 1-bit: `CMD=GPIO38`, `CLK=GPIO39`, `DATA0=GPIO40`

The first attempt to add PSRAM to the general heap reproduced a reset on this
ESP32-S3 revision 0.2 / AP 3.3 V N16R8 combination. The current Session build
keeps ordinary allocations in internal RAM and explicitly allocates the bounded
640000-byte microphone capture buffer, one bounded playback payload of at most
4 MiB, and one 192 KiB camera framebuffer from PSRAM. Camera DMA remains staged
through a small internal buffer; direct PSRAM DMA is disabled because it
produced incomplete OV5640 JPEGs on this board. Speaker control messages remain
in a bounded static internal-RAM queue.

## Bring-up rule

Test one boundary at a time:

1. Board-only serial diagnostics
2. Onboard microSD identity and non-destructive 32 MiB integrity smoke (passed)
3. MAX98357 and 8 ohm / 3 W speaker at low volume
4. INMP441 microphone capture telemetry
5. OV5640 identity, JPEG capture, and microSD export
6. PC-side face detection without identity enrollment
7. Sequential record-and-playback smoke
8. Network transport to Nana Core

## Power safety for the first smoke

- Disconnect USB before changing any jumper wire.
- Connect only the board's `USB-UART` port to the PC.
- Do not connect the 5 V adapter or MB-102 supply at the same time.
- INMP441 must use 3.3 V, never 5 V.
- The MAX98357 speaker output is differential: use `SPK+` and `SPK-`; neither
  speaker lead goes to ground.
- Test the amplifier at low volume from USB power. Use a separate regulated 5 V
  rail before testing high output power.

## MAX98357 speaker smoke

The seven-pin MAX98357 header is `VIN`, `GND`, `SD`, `GAIN`, `DIN`, `BCLK`, and
`LRC`. Its green screw terminal adds the two differential speaker outputs.

| MAX98357 | ESP32-S3 board | Purpose |
| --- | --- | --- |
| `VIN` | `5V` | Amplifier power |
| `GND` | `GND` | Common ground |
| `BCLK` | `GPIO41` | Shared audio bit clock |
| `LRC` | `GPIO42` | Shared audio word-select clock |
| `DIN` | `GPIO47` | ESP32 audio output to amplifier |
| `SD` | `GPIO2` | Active-high amplifier enable; low means hard mute |
| `GAIN` | Not connected | Module default gain |

`GPIO21` is reserved for the INMP441 data output. The microphone and amplifier
share the canonical `GPIO41` and `GPIO42` clock pins. Isolated bring-up profiles
own those pins one at a time. Production media will use the Wi-Fi Session
transport; USB is not a runtime media path. The isolated embedded speaker smoke
remains 44.1 kHz. This uses the exposed JTAG pins for audio clocks but leaves
native USB debugging available on the board's separate USB port.

Firmware owns the amplifier lifecycle: it drives `SD` low at the start of
`app_main`, starts I2S and transmits silence while muted, enables the amplifier
only for playback, then mutes it before disabling I2S. Every error path also
returns to mute. Add a `10 kOhm` resistor from `SD` to `GND` in the final wiring
so the amplifier remains below its shutdown threshold during reset and before
firmware takes control; the ESP32's software pull-down cannot cover that
pre-boot interval. The stronger external pull-down also dominates the
SD/MODE bias network commonly fitted to MAX98357 breakout boards.

## INMP441 VAD-gated serial capture

`NANA_BRINGUP_MICROPHONE_VAD_SERIAL` is the isolated microphone-quality test.
It starts no display, camera, speaker, Wi-Fi, or speech-to-text service. The
firmware calibrates room noise for two seconds, rejects an excessively noisy
calibration, keeps a 500 ms RAM pre-roll, starts after 160 ms above the adaptive
threshold, and stops after 1200 ms of silence while retaining a 300 ms tail.
Triggers shorter than 3 seconds are discarded and re-armed. One utterance of
at most seven seconds is
sent as CRC32-protected, 16 kHz mono PCM over USB-UART. The PC receiver validates
the payload and writes `captures/nana_mic_vad_test.wav`.

The onboard GPIO48 LED is dim green while VAD is armed, blue while speech is
being recorded, and off after the one-shot capture has closed.

Run the receiver immediately after flashing:

```powershell
python tools/capture_microphone_vad_serial.py --port COM14
```

The energy gate is a bring-up diagnostic for validating the physical INMP441
signal and speech-only packet contract. Production voice activity detection and
audio framing will be implemented independently inside the Wi-Fi Session.

## Retired USB media route

The experimental `Voice Link` and combined `Presence V1` production profiles
were removed on 2026-07-23. Nana Core no longer opens `COMxx`, accepts the old
`NANA_PRESENCE_V1_*` environment settings, or transports microphone/speaker
PCM over USB-UART. The ESP32 no longer builds `nana_voice_link.c`.

USB remains maintenance-only for firmware flashing, logs, one-time NVS
provisioning, and explicitly selected single-device bench diagnostics. Normal
Nana operation owns one authenticated Wi-Fi/WebSocket Session. Bounded speaker
downlink, microphone uplink, display commands, and request-driven camera JPEGs
now use that Session instead of reviving a second cable transport.

The speaker-quality smoke embeds the first six seconds of the selected
Nana ElevenLabs MP3 as 44.1 kHz, signed 16-bit mono PCM. At boot the firmware
waits two seconds and plays that sample exactly once, directly from flash through
I2S. It makes no Wi-Fi or ElevenLabs request. Playback is currently locked at
50 percent digital gain for the controlled-mute smoke, with a short boundary
fade; this is not a
calibrated acoustic volume percentage. `Nana voice smoke: PASS` proves that the
I2S driver accepted the whole sample; the owner listening test still proves the
physical sound quality.

## Temporary ST7789 face demo

`NANA_BRINGUP_DISPLAY` is the isolated display profile. It tests the purchased
1.69-inch, 240x280 ST7789 SPI module without starting camera, Wi-Fi, SD, audio,
or microphone services. Select it explicitly in `main/app_main.c` before a
display smoke. This camera-board wiring is a temporary bench jig:

| ST7789 module | ESP32-S3 camera board |
| --- | --- |
| `GND` | `GND` |
| `VCC` | `3V3` |
| `SCL` | `GPIO39` |
| `SDA` | `GPIO38` |
| `RES` | `GPIO1` |
| `DC` | `GPIO40` |
| `CS` | `GPIO14` |
| `BLK` | `3V3` |

Power the board off and remove the microSD card before wiring. GPIO38-40 are
borrowed from the onboard SD slot only for this demo. The color smoke was
owner-verified before enabling animation. The current firmware renders Nana's
face in landscape `280x240` with solid blue geometry and no pupils at a 20 FPS
target. `neutral` is the permanent startup baseline and includes natural idle
breathing plus a smooth blink on a varied roughly 4-7 second cadence. The tag
contract separates persistent expressions from one-shot gestures.

Persistent expressions (`23`):

```text
neutral listening pondering happy laughing glee
sleepy surprised shocked scared awe skeptical squint
mic_unclear core_loading
curious playful shy relieved pleading
love dizzy proud
```

One-shot gestures (`15`):

```text
wink_left wink_right double_blink
glance_left glance_right glance_up glance_down look_around scan
peek_left peek_right startled_blink double_take celebrate panic
```

`nana_display_set_expression(tag)` replaces the persistent baseline and accepts
expression tags only. `nana_display_trigger_event(tag)` can temporarily play any
non-neutral tag for its catalog duration, then returns to the current baseline.
Updating the baseline while an event is active does not interrupt the event; the
new baseline appears when that event finishes. Gestures therefore remain
one-shot, while an expression can be selected either as a persistent baseline or
as a temporary event.

The shared renderer supports rounded, slanted, arc, line, heart, spiral,
chevron, asymmetric, tear, sweat, and sparkle geometry. The two eyes have
independent blink envelopes for left/right wink gestures. State boundaries close
and reopen the eyes over 350 ms, while idle motion, blinking, glancing, shaking,
breathing, tears, sweat, sparkles, and the loading spinner remain frame-driven.
`panic` is the only current tag that adds a mouth; every other tag follows the
eyes-only design.

`nana_face_state_from_tag()`, `nana_face_state_tag()`,
`nana_face_state_tag_kind()`, `nana_face_tag_is_gesture()`,
`nana_display_set_expression()`, and `nana_display_trigger_event()` form the
transport-facing contract. Commands cross into the 20 FPS display owner through
a bounded non-blocking queue. Startup validates that all 38 tags are unique,
complete, correctly classified, and round-trip to the expected state.
`mic_unclear` narrows both eyes and shows a question mark beside the physical
right eye when speech was not understood. `core_loading` shows a cyan spinner
above the eyes after valid audio has been sent to Nana Core. Network control from
Nana Core is implemented in the current unflashed Session build as bounded
`display_state` and `display_event` messages with exact request ACK matching.
The node advertises display capability only after the 20 FPS controller starts.
Combined audio/display physical acceptance remains pending; the isolated display
bring-up is still available for maintenance.

### Owner animation selection

Export the current firmware catalog to one GIF per tag with the bundled Codex
Python runtime (which includes Pillow):

```bat
python tools\export_face_gallery.py
```

The exporter creates `output\face-gallery` exactly once and refuses to
overwrite an existing review. Open `index.html`, then delete unwanted GIF files
only from its `animations` directory. The remaining filenames are the owner-
approved tag selection. The current catalog contains the 38 animations retained
from the original 56-tag owner-review baseline.

## Presence Session V1: control plus bounded half-duplex PCM

`NANA_BRINGUP_PRESENCE_SESSION` is the selected local source profile for the
production Wi-Fi transport:

```text
ESP32 -- authenticated WebSocket --> Nana Core
      hello / welcome
      heartbeat / heartbeat_ack
      bounded reconnect

Nana Core -- credited binary PCM --> ESP32
          playback_begin / playback_ready
          1024-byte NPA1 + CRC32 frames
          playback_credit / playback_end / playback_drained

ESP32 -- VAD capture in PSRAM --> Nana Core
      capture_begin / capture_ready
      1024-byte NPA1 + CRC32 frames
      capture_credit / capture_end / capture_received

Nana Core -- after real reply drain --> ESP32
          turn_complete

Nana Core -- bounded camera_snapshot --> ESP32
ESP32 -- suspend mic, capture VGA JPEG into PSRAM --> Nana Core
      1024-byte NPA1 + CRC32 frames under Core-issued credits
      camera_begin / camera_end / camera_received
```

This profile advertises `audio_uplink=true`, `audio_downlink=true`,
`camera=true`, and `display=true` only when each embedded owner starts
successfully. It initializes separate microphone-uplink and speaker workers.
The microphone captures at most 20 seconds of PCM16/16 kHz mono into a bounded
PSRAM buffer, tears down I2S RX before transmission, and sends only under
Core-issued credits. Each accepted speaker reply is received completely into a
bounded PSRAM payload before I2S playback starts; its control queue remains
bounded static internal RAM. MAX98357 is hard-muted at boot, idle, disconnect,
abort, underrun, and shutdown, and enabled only during accepted playback. The
mic is not re-armed until Core reports that reply playback actually drained and
sends `turn_complete`. Core display commands use an approved 38-tag allowlist,
exact ACK matching, reconnect state resynchronization, and soft-fail lifecycle
mapping. Camera capture is request-driven: it suspends the microphone, starts
the OV5640 only for one bounded VGA JPEG, transfers the frame through the same
authenticated Session, stops the sensor, and then resumes the microphone.
Isolated hardware profiles remain available for maintenance diagnostics; there
is no USB media runtime fallback.

After one-time pairing, the normal Nana entry point loads the Presence bearer
token from the current Windows user's DPAPI-protected credential and enables
the listener automatically:

```bat
python -m nana
```

The listener binds `0.0.0.0:8765`. `0.0.0.0` is only the bind address; the
ESP32 URI must use the PC's current private-LAN address. Set
`NANA_PRESENCE_SESSION_ENABLED=0` before startup for an explicit one-run
opt-out. The compatibility launcher
`nana\tools\run_presence_session_core.py` remains available from the repository root for manual
host/port overrides and protected-credential maintenance.

Windows must allow that listener from the private home subnet. Run this once
from an elevated PowerShell window; it disables only Windows' conflicting TCP
block for this Python executable and adds a program-, port-, and subnet-scoped
rule:

```powershell
& .\tools\install_presence_firewall_rule.ps1
```

Do not replace this with a broad "allow Python everywhere" rule and do not
port-forward `8765` on the router.

After flashing this profile, pair the board and Core once through the physical
USB-UART port. `--generate-token` creates a strong shared token, writes it to
the board's NVS, and stores the PC copy with Windows DPAPI; it never prints the
token:

```bat
python tools\provision_presence_session.py --port COM14 --uri ws://<LOCAL_IP>:8765/presence/v1 --generate-token
```

Replace `<LOCAL_IP>` if `ipconfig` reports a different current LAN address.
Firmware redacts the token from logs. With Core absent, the ESP32 keeps trying
the saved URI using approximately 1, 2, 4, then 5-second bounded backoff plus
up to 250 ms jitter. It connects on its own when Nana starts; neither process
must be restarted in a particular order. Clear both the board NVS values and
the PC's DPAPI credential with:

```bat
python tools\provision_presence_session.py --port COM14 --clear
```

Inside Nana, both commands are read-only:

```text
/presence-session-status
/presence-session-diagnostics
```

The protected PC credential is encrypted for the current Windows account. It
is not stored as plaintext and is never placed on the command line. For daily
operation, provision once and then start Nana normally; there is no recurring
token prompt. A detached node subsequently connects over Wi-Fi, so USB is not
required for media.

Production VAD keeps the conservative 2-second calibration on the first boot
arm, uses a fresh 750 ms recalibration after later turns, starts after 160 ms
of stable speech, and ends after 600 ms of silence while retaining the bounded
pre-roll/tail. Core logs gate, STT, reasoning, TTS-provider, playback, and total
turn timing so later latency work is based on measured bottlenecks.

Read-only diagnostics do not start capture or playback. They report connection
health, both audio capabilities, capture starts/completions/rejections/failures,
wire/effective capture bytes, chunks and trim, plus playback sent/received/
played bytes, queue high-water, underruns, camera transfer counters, and elapsed
time.

Run the RAM-only physical camera smoke while the owner is present and the lens
has a well-lit face in view:

```bat
python tools\smoke_presence_session_camera.py
```

The tool requests three bounded VGA JPEG snapshots through the authenticated
Session, validates framing/CRC/credits, runs YuNet face-presence detection on
the PC, requires a face in a strict majority of frames by default, and persists
no image. Use `--min-present-frames 0` only for transport diagnosis, never for
`B4` acceptance. A protocol PASS is not physical image-quality or long-run
coexistence acceptance; the owner must still inspect the result.

In the normal Core runtime, `/presence-camera-snapshot` requests one RAM-only
frame. The CLI waits up to 30 seconds for a camera-capable node and an idle
audio/media lease, while genuine camera failures remain fail-fast.

Run physical audio smokes only while the owner is present and expecting sound:

```bat
python tools\smoke_presence_session_hardware.py --serial-port COM14 --uri ws://<LOCAL_IP>:8765/presence/v1
python tools\smoke_presence_voice_engine_hardware.py --serial-port COM14 --uri ws://<LOCAL_IP>:8765/presence/v1
```

The first validates a low-gain one-second tone plus Core restart and board
reboot reconnects. The second routes one real `VoiceEngine.say()` result to the
node. Both use an ephemeral bearer token, require exact playback drain, and
clear the temporary URI/token afterward. Machine PASS proves transport and I2S
completion; the owner still decides whether the physical sound is clean.

Security boundary: milestone 1 uses plaintext `ws://` and stores its bearer
token in NVS. Use it only on the trusted private LAN; never port-forward port
8765. WSS/device-bound credentials belong to a later deployment gate.

The 2026-08-06 auto-pair/latency source revision builds with ESP-IDF 5.5.5:
`nana_presence_node.bin` is `0x1179a0` bytes, leaving `0x5f660` bytes (25%) in
the smallest app partition. SHA-256:
`4C517F5939B88473A7D4AAA50D343CBD1DA94DE89B89F6BCE98EAB4B70965C47`.
The image is not considered live until it is flashed and owner-tested. At the
time of this build Windows did not enumerate CH343 `COM14`.

Current verification on 2026-07-30: Session media/display/camera loopback smoke
`16/16`, Core camera-vision smoke PASS, camera command queue `3/3`, and ESP-IDF
5.5.5 build PASS. The real
board also started, warmed, and stopped the OV5640 after Session workers were
running and the microphone owner was suspended. The owner-token physical smoke
then transferred three RAM-only `640x480` JPEGs; YuNet found one face in all
`3/3`, with zero errors and no persistence. Existing physical audio/display
coexistence and D3 failure/reconnect evidence remain accepted. A normal-Core
follow-up then completed one queued snapshot plus post-camera mic -> Core ->
speaker/display in the same Session: camera/uplink/downlink each completed
`1/1`, display completed `4/4`, and playback drained exactly
`120320/120320/120320B` with zero underruns, heartbeat timeouts, failures, or
last error. `B4-CAMERA-COEXISTENCE` is accepted.

The guarded source build is `0x116f00` bytes with `0x60100` bytes (26%)
free in the smallest app partition; SHA-256 is
`99879B5EA5D4EB24A1AACAE6B6D30F2A4C1D17CD3C8CECEB63C7721AB7ACF53C`.
It was not re-flashed during the overnight verification because Windows did not
enumerate CH343 `COM14`. The board retains the previously flashed,
runtime-equivalent staged-DMA camera image; the new compile-time guards do not
change the accepted runtime mode.

The provisioning tool waits for the firmware readiness marker before writing.
This avoids corrupting the UART command by transmitting while the ESP32 is
still in ROM/bootloader startup.

## Build and flash

Run these commands from an ESP-IDF terminal after the toolchain is installed:

```bat
idf.py set-target esp32s3
idf.py build
idf.py -p COM14 flash monitor
```

Exit the serial monitor with `Ctrl+]`.

`NANA_BRINGUP_CAMERA_LAN` owns the OV5640 once,
connects to the local Wi-Fi network, and exposes a token-protected VGA/JPEG
camera service. Provision Wi-Fi once over the physical `USB-UART` port:

```bat
python tools\provision_wifi.py --port COM14
```

The password is entered with hidden input, stored only in ESP32 NVS, and never
written to the PC config. After connection, the utility saves the board URL and
camera token to the git-ignored file
`.local\presence_node.json`, then opens the viewer.
The USB bring-up profile limits Wi-Fi TX power to 10 dBm and uses minimum modem
power save. Brownout protection remains enabled; a brownout still means the
physical 5 V path or cable must be corrected rather than hidden in firmware.
The station profile explicitly permits Vietnam's 2.4 GHz channels 1-13 and
probes only the configured SSID before connecting.

Bring-up diagnostics:

- `reason=201` means the configured SSID was not visible to the 2.4 GHz radio.
  Enable the router's 2.4 GHz band or provision its separate 2.4 GHz SSID.
- `Brownout detector was triggered` means the physical USB/5 V path dipped
  during a radio current burst. Correct the cable or regulated supply; do not
  disable brownout protection.

The LAN endpoints are:

| Endpoint | Purpose |
| --- | --- |
| `/` | Minimal live viewer |
| `/health` | Camera and transport health |
| `/capture.jpg` | One explicitly requested JPEG |
| `/stream.mjpg` | MJPEG stream, currently capped at one active client |

All endpoints require the generated token through `X-Nana-Token` or the
`token` query parameter. Test health and request exactly one frame with:

```bat
python tools\probe_camera_http.py
```

Use `--open` to open the viewer. Clear saved Wi-Fi credentials with:

```bat
python tools\provision_wifi.py --port COM14 --clear
```

LAN mode does not write camera frames to microSD. A continuously available
stream also does not mean continuous vision inference: the future PC Presence
Adapter is the sole stream consumer and will sample bounded frames or emit
person/motion events for Nana Core. Do not expose this service with router port
forwarding.

`NANA_BRINGUP_CAMERA_SERIAL` remains the offline diagnostic fallback. It sends
validated Base64 JPEG envelopes through USB-UART and exports one diagnostic
frame to microSD. Expected serial output includes:

```text
Sensor detected: PID=0x5640
Frame: 640x480 | ... | jpeg_complete=true
SD export: PASS | path=/sdcard/NANA.JPG
Camera smoke: PASS | sensor=OV5640 | capture=VGA JPEG
Serial camera stream: START | interval=1000ms | port=USB-UART
```

To copy three live frames to the PC and retain the newest one, run:

```bat
python tools\capture_camera_serial.py --port COM14 --frames 3
```

The output is `captures\nana_camera_latest.jpg`.
Power the board off before removing the card. After mounting it on the PC, or
after receiving the serial image, run:

```bat
python tools\detect_faces.py captures\nana_camera_latest.jpg --require-face
```

The annotated output is `nana_camera_latest_faces.jpg`. This proves face
detection only; it does not
assign a name or create a person record.

The build pins `esp32-camera 2.1.7` through `dependencies.lock`. The current
baseline uses one bounded 192 KiB 640x480 JPEG framebuffer in PSRAM and flips
the OV5640 vertically at the sensor. DMA is deliberately staged through a small
internal buffer; direct PSRAM DMA produced incomplete JPEGs and is guarded by a
build-time error. Higher resolutions and multiple framebuffers remain deferred
until supervisor safety, final pin/power ownership, and soak acceptance pass.

## GPIO allocation

The canonical allocation lives in `main/nana_board_pins.h`. Audio uses
`BCLK=GPIO41`, `WS=GPIO42`, `DOUT=GPIO47`, and reserves `DIN=GPIO21`. Camera,
microSD, PSRAM, boot/strap, USB, and onboard LED pins are not reused.

The PCB legend is `- = camera`, `~ = SD Card`, and `* = PSRAM`. The camera map
is `SIOD=4`, `SIOC=5`, `VSYNC=6`, `HREF=7`, `D2=8`, `D1=9`, `D3=10`, `D0=11`,
`D4=12`, `PCLK=13`, `XCLK=15`, `D7=16`, `D6=17`, and `D5=18`.

The identity boundary is documented in `docs/CAMERA_IDENTITY_BOUNDARY.md`.
Nana Core on the PC owns face embeddings, consent, `person_id`, relationship
state, and authorization. The ESP32 remains a bounded camera/transport node.
The LAN ownership and sampling contract is documented in
`docs/CAMERA_LAN_TRANSPORT.md`.
