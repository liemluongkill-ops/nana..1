# Nana Current Code Truth

> Public export note (2026-09-16): this is a sanitized snapshot of accepted
> canonical architecture, not a live deployment report. Private evidence
> locations, active provider overrides, runtime logs, memory data, and pending
> workstream state are intentionally excluded.

## 2026-09-13 Memory v2 Phase 2 Code Truth

The current bounded Phase 2 implementation is present behind existing
ownership boundaries:

- `runtime/controlled_memory_retrieval.py` owns bounded lexical retrieval
  and the injected semantic adapter boundary. Semantic failure falls back
  deterministically; public semantic scoring remains blocked by policy.
- `runtime/memory_consolidation_phase2.py` is read-only preview only and never
  mutates durable memory.
- `runtime/public_cross_session_memory.py` accepts an explicit caller-supplied
  snapshot and filters it by exact public scope, verified provenance, consent,
  and retention without writing durable memory.
- `brain/gpt.py` owns the integration boundary that selects the lane-owned
  `public_long_term` collection and passes it as the snapshot, so public recall
  never scans private `long_term`.
- `runtime/memory_phase2_status.py` reports flags and read-only state.
- `runtime/memory_grounding.py` retains the narrow, opt-in grounding boundary.

All Phase 2 flags and the semantic provider default remain OFF/none. No
runtime implementation file changed during the 2026-09-13 closeout; only
Python 3.12 audit guards in two smoke files changed. This is bounded code
truth, not a claim of semantic-provider quality or livestream readiness.

Evidence packet: `<NANA_REPO>\nana\MEMORY_V2_PHASE2_FINAL_CLOSEOUT_20260913.md`


Last checked: 2026-08-12 for the general runtime baseline; the 3D avatar
section alone was refreshed from current code/build evidence on 2026-09-05.

Purpose: this file is the current code-backed architecture truth. Use it before
older wiki sections, phase history, or chat memory when deciding what Nana is
actually running through today.

Architecture level: this is Level 3 Implementation truth. For the conceptual
map, read `NANA_ARCHITECTURE_VISION.md` first. For system boundaries, read
`NANA_ARCHITECTURE_CORE.md`. Do not use this file's long module/file lists as a
Level 1 product model.

## 2026-09-05 Nana 3D Avatar WebGL Implementation Truth

Current accepted local path:

```text
<NANA_REPO>\nana\__main__.py
  -> nana.runtime.avatar_intent_gateway (127.0.0.1:8766, semantic-only)
  -> <NANA_AVATAR_WEB>\web Vite proxy /avatar-api
  -> Unity WebGL NanaTargetDrivenAvatarController
  -> baked Shinano humanoid
```

Editable avatar source remains under
`<AVATAR_SOURCE>`. WebGL builds use the isolated copy at
`<NANA_AVATAR_WEB>\unity`; do not apply its WebGL-only VRChat package metadata
patch to the editable project by default.

The accepted build performs NDMF/Modular Avatar processing before scene save,
then rebinds Animator/Neck/Head/Body and strips platform-only behaviours.
Evidence: `renderers=48->48`, `behaviours=141->1`; WebGL build succeeded at
`91443162` bytes with zero errors and one warning. Core contract smokes are
fresh `13/13 + 6/6 PASS`.

Runtime truth is narrower than the gateway vocabulary: only `look` has a
verified Unity WebGL mapping. The gateway defaults disabled and the Vite server
is currently stopped. Read `NANA_3D_AVATAR_STATE.md` for the complete file map
and limits.

## ESP32 / Presence Execution Hold

`ESP32-OWNER-HOLD-1` is currently `WAITING_OWNER` / `DEFERRED`, at Ba's
explicit request. This is a scheduling hold, not a code freeze and not a
milestone closure. Do not flash, provision, wire, power-cycle, test, optimize,
or reopen the ESP32/Presence scope unless Ba explicitly names that scope and
asks to continue. Before acting, obtain a fresh explicit owner confirmation.
All source, firmware, test tools, and historical evidence remain intact.

## 2026-08-12 Wiki Reconciliation Snapshot

This is the current read-only reconciliation snapshot, not a new runtime claim:

```text
handle_text.py=177 lines
registry total=4499
registry live=1397
registry archived=107
registry reserved=2995
runtime_loaded_count=46
smoke_command_registry_truth.py=12/12 PASS
smoke_imports.py=2 known analyzer warnings; direct imports PASS
nana.main_loaded=False
nana.main_cut_loaded=False
game_loaded_count=0
phase_loaded_count=0
```

The two import analyzer warnings are known false positives around
`bytearray/memoryview` in `nana.runtime.presence_media_protocol.decode_media_frame`
and `SystemExit` in `nana.tools.run_presence_session_core.main`. They are not
runtime import failures. The game capability freeze remains active, and the
Presence hold remains `WAITING_OWNER` / `DEFERRED`.

## Current Core Architecture Baseline

As of 2026-07-13, `CORE-ARCHITECTURE-BASELINE-1` is CLOSED.

- `CORE-RUNTIME-OWNERSHIP-1A` is CLOSED. `nana.autonomy.loop` is the sole owner
  of the canonical `AUTONOMY_LOOP` identity and its thread. `nana.cli.app` owns
  start/stop lifecycle rights only; CLI globals and package exports alias that
  same instance. `RUNTIME_TURN_STATE` and `set_runtime_turn_state` have one
  active source in `nana.cli.globals`, and updates carry a real timestamp.
- `CORE-RUNTIME-LIFECYCLE-1B` is CLOSED. `AutonomyLoop.stop()` returns `False`
  on timeout and remains `STOPPING` while its thread is alive. An unexpected
  tick exception propagates to `threading.excepthook`; the owned `_run`
  `finally` sets `STOPPED`. A later `start()` creates exactly one new thread, a
  second `start()` is idempotent, and final `stop()` joins it. Runtime shutdown
  is idempotent and safe after partial startup: signal the bridge before
  grace/cancel, cancel and await owned top-level async tasks, join registered
  pollers, then shut down voice and close VTS. `shutdown_complete` is not
  claimed while an owned producer remains alive.
- `CORE-CONFIG-CONTRACT-1` is CLOSED. Before constructing VoiceEngine, VTS,
  autonomy, or bridge services, `nana.cli.app` builds one immutable sanitized
  snapshot from `nana.runtime.startup_config_contract`. It resolves the active
  provider model, validates autonomy/voice/VTS/bridge/lifecycle startup values,
  fails closed on invalid configuration, and prints degraded warnings. Secret
  key and voice-ID values never enter the snapshot; only readiness booleans do.
- `CORE-CAPABILITY-BOUNDARY-1` is CLOSED. Active core, runtime, CLI, commands,
  autonomy, and brain modules contain zero direct `nana.game` imports. The four
  lazy game module loads are centralized in `nana.runtime.capabilities`; disabled cold
  core imports load zero `nana.game` modules. Existing game code and behavior
  remain preserved under `OWNER-DIRECTED-GAME-CAPABILITY-FREEZE`.
- `CORE-ARCHITECTURE-GUARD-1` is CLOSED. The active entrypoint remains
  `nana.__main__ -> nana.cli.app.main`; active sources import neither
  `nana.main` nor `nana.main_cut`. Cold import loads no legacy/phase modules and
  starts no thread. The autonomy idle-banter content smoke disables LLM before
  importing Nana, preventing a live API call.

Baseline rule: new capabilities attach at `nana.runtime.capabilities`; they do
not add direct game imports or new `AutonomyLoop` construction sites.
Startup-critical flags join the sanitized startup contract or are explicitly
documented as non-startup provider tuning. Do not introduce task-manager,
plugin, or provider frameworks without concrete evidence.

## Presence Progressive PCM Transport Truth

### Exact-1500 Stress Boundary (Silent Smoke Pass, LIVE DEFERRED UNDER OWNER HOLD)

The Presence voice path keeps the firmware and server per-stream PCM ceiling
at `4MiB`; the ESP32 allocates its bounded playback buffer in PSRAM. The Core
does not raise that ceiling for one oversized stream. Production safety comes
from `NANA_PRESENCE_TTS_SEGMENT_MAX_CHARS=600`: each progressive PCM/WebSocket
stream drains before the next begins.

Whole-reply truncation is not a default production policy. It activates only
when `NANA_PRESENCE_VOICE_LIMIT_ENABLED=1`; its configurable boundary is
`NANA_PRESENCE_VOICE_MAX_CHARS=1500`. `/voice-status` exposes whether the flag
is enabled plus input, source, omitted, and truncation values. With the flag
off, replies above 1500 characters pass unchanged into the segmenter.

`/presence-voice-stress 1500` is the explicit physical acceptance command. It
generates exact deterministic input without an LLM or microphone, then reports
completion, segment counts, bytes, underruns, and playback grade. The silent
adapter smoke passes exact 1500-character source handling as three simulated
streams (`562/592/344` provider characters; two separator spaces are removed),
with no truncation, duplication, or underrun. Physical acceptance still
requires three owner-heard runs and was not claimed overnight.

### Prior Short/Long Evidence

As of 2026-08-07, `PRESENCE-STREAM-HEARTBEAT-1` is physically accepted for
short and long replies. The active path remains:

```text
ElevenLabs PCM
  -> bounded Core producer/prefetch
  -> credited CRC32 WebSocket frames
  -> ESP32-S3 PSRAM ring
  -> I2S DMA / MAX98357
```

The ESP32 audio worker is deliberately below the WebSocket task priority and
yields one RTOS tick after each received PCM chunk. Session audio credits are
batched and drained by the session supervisor instead of blocking the audio
worker. This keeps heartbeat processing responsive during long playback while
retaining the half-duplex lease and fail-closed hard mute.

Physical evidence on 2026-08-07:

- Short Japanese reply: `first_audio=453ms`, normal playback, zero node
  underruns, complete drain.
- Long Japanese reply: `287/287` characters and exact
  `1651200/1651200/1651200B` sent/received/played, `first_audio=344ms`, normal
  playback, zero node underruns, complete drain.
- Session heartbeat: `462` received, `0` timeouts, one connected node.
- Prefetch had one measured starvation of `84.6ms`; it did not become an audio
  underrun or an audible defect. Repeat soak testing is still required before
  calling the path fully hardened.

The following verification list is a historical 2026-07-09 snapshot; current
import status is recorded in the 2026-08-12 reconciliation snapshot above.

Recorded verification: config contract `14/14`; ownership `6/6`; lifecycle
`9/9`; capability boundary `5/5` across the corrected 174-file
core/runtime/cli/commands/autonomy/brain scan; architecture guard `3/3`;
autonomy idle banter `ALL GREEN` with live LLM disabled; selected core baseline
regression `23/23` scripts; import audit `0 issues` in that historical analyzer
snapshot; fresh backup archives
fully read and verified `7/7`.

Owner lifecycle acceptance started Nana with `NANA_AUTONOMY_LLM_DISABLED=1`.
The canonical loop reported `running`, pause/resume commands affected it, and
`exit` printed `Nana tam biet!` before the process returned immediately to the
`<NANA_REPO>>` prompt without a shutdown hang.

Voice HTTP/callback and inline tags remain CLOSED; the GPT-5.6 Terra route
remains CLOSED; game development remains FROZEN. This architecture closeout
changed none of those systems. The owner smoke made no chat/API, TTS,
Discord-send, VTS, OBS, or game-input call.

## Current Presence Node Implementation Boundary

As of 2026-07-30, `PRESENCE-SESSION-1` has a **LIVE-PASSED CONTROL + BOUNDED
HALF-DUPLEX PCM + DISPLAY + REQUEST-DRIVEN CAMERA BASELINE**. Both audio
directions, display transport, physical audio/display coexistence, and the
three-frame authenticated Session/YuNet camera path are live-passed. `B4` is
also closed: a normal Core run completed one queued camera request followed by
clean mic/display/audio resume in the same physical Session lifecycle.
`PRESENCE-TRANSPORT-CLEANUP-1` remains **LIVE PASS**. The production runtime has one device boundary:
authenticated Wi-Fi/WebSocket
`nana.presence.session.v1` at `/presence/v1`. The authoritative subsystem truth
is `NANA_PRESENCE_NODE_STATE.md`.

`B5-SUPERVISOR-SAFETY` is LIVE PASS as of 2026-08-01.
`nana_presence_session_run()` owns bounded audio, microphone-uplink, and camera
workers and rolls partial startup back in reverse order through explicit
acknowledged deinit calls before deleting Session queue/event resources. The
speaker is hard-muted before and after cleanup. Core loopback proves an
in-flight playback disconnect fails promptly and reconnects cleanly, and an
occupied listener start leaves a reusable port. The physical acceptance then
interrupted active playback for three seconds: the owner observed immediate
silent hard mute, Core recorded one bounded failure with no underrun or queue
growth, the node reconnected and resynchronized display, and the next full
turn completed cleanly. Stationary V1 is `21/23` (`91%`).

Current firmware root:

```text
<NANA_REPO>\nana_presence_node
```

The current and only production selector is `NANA_BRINGUP_PRESENCE_SESSION`.
The current source/flashed image advertises audio uplink/downlink, the existing
20 FPS display controller, and request-driven camera capability after each
owner starts successfully. Display-only transport passed `8/8`; a later normal
Nana run physically passed simultaneous mic, speaker, and display lifecycle
under `B3` with exact drain and returned to `listening`.

Audio is PCM16/16 kHz mono and strictly half-duplex. A bounded VAD capture of at
most 20 seconds is explicitly allocated in the board's 8 MiB PSRAM. Each
accepted Nana reply is received completely into one bounded PSRAM payload of at
most 4 MiB before I2S playback begins. Control queues remain bounded static
internal RAM. MAX98357 is hard-muted at boot, idle, abort, underrun, disconnect,
and shutdown, and is enabled only while the playback worker owns an accepted
stream. The mic does not re-arm until exact reply drain and `turn_complete`.

Camera capture is one-shot and authenticated. Core sends `camera_snapshot`; the
node suspends the mic, starts OV5640 VGA/JPEG, transfers at most 192 KiB through
1024-byte CRC32 frames under Core-issued credits, waits for exact receipt,
stops the camera, and resumes the mic. The full JPEG framebuffer is allocated
in PSRAM. DMA remains staged through a small internal buffer; direct PSRAM DMA
is compile-time prohibited because it produced incomplete JPEGs without EOI on
this board. Core decodes frames in memory, runs YuNet presence-only inference,
and does not persist images.

The interactive `/presence-camera-snapshot` CLI command is a bounded usability
queue, not a second transport owner. It waits at most 30 seconds for a
camera-capable node and an idle media lease, retries only unavailable/busy
states, and preserves fail-fast behavior for real driver or protocol errors.

The old cabled media runtime has been removed, not merely disabled:

- Core no longer contains `nana.runtime.presence_voice_link` or
  `nana.runtime.presence_camera_observer`.
- Core startup no longer reads `NANA_PRESENCE_V1_ENABLED`,
  `NANA_PRESENCE_SERIAL_PORT`, `NANA_PRESENCE_SERIAL_BAUD`, or the old
  `NANA_PRESENCE_CAMERA_*` variables. Setting them is inert.
- `pyserial` is no longer a Nana Core runtime dependency.
- Firmware no longer contains or builds `nana_voice_link.c`,
  `NANA_BRINGUP_VOICE_LINK_V2`, or `NANA_BRINGUP_PRESENCE_V1`.
- `/presence-node-status` and `/presence-node-diagnostics` are retained as
  friendly aliases for Session status and diagnostics; they do not open COM.

USB-UART remains maintenance-only: firmware flashing, boot logs, one-time
Session URI/token provisioning into NVS, and explicitly selected isolated
single-device bench diagnostics. It is not a fallback media transport. The
2026-07-20/21 COM14 audio/camera/display results remain historical hardware
evidence only and must not be used as a current startup recipe or rollback
architecture.

`PRESENCE-SESSION-1` implements bearer authentication,
`HELLO -> WELCOME`, heartbeat ACK, stale-session close, duplicate-device
replacement, bounded frames, reconnect backoff, and credited PCM in both
directions. PCM uses CRC32-protected 1024-byte frames, exact stream/sequence
matching, explicit abort, and exact `playback_drained`/turn completion.

Two consecutive real full turns passed on 2026-07-24. The longer reply matched
`601600/601600/601600` bytes sent/received/played, had zero underruns and zero
heartbeat timeout, and followed an accepted microphone capture and STT result.
The earlier bounded tone remains `5/5`; real `VoiceEngine.say()` remains `4/4`.

`D3-FAILURE-RECONNECT` passed `6/6` physically on the preceding hard-muted
Session milestone: a real 3-second ESP32 radio interruption, Core-listener loss,
malformed Core frames, cancellation during reconnect, and repeated shutdown all
recovered or failed closed. The earlier board-reset acceptance supplies the
reboot leg. Current PCM protocol smokes additionally prove fail-closed abort;
physical interruption during active playback is now accepted `B5` evidence;
the longer `D4` soak remains open.

Current confirmed implementation facts:

- `main/nana_board_pins.h` remains the canonical GPIO map.
- Session media advertises only capabilities whose embedded owner started.
- Audio and the complete 192 KiB camera frame use explicit bounded PSRAM
  payloads; general allocations remain in internal RAM because broad PSRAM heap
  integration previously reset the board.
- `nana.runtime.presence_audio_quality` and
  `nana.runtime.presence_human_gate` remain transport-neutral policy modules for
  Session media; neither owns a camera nor a serial port.
- Core and firmware now implement bounded `display_state` and `display_event`
  messages, exact request ACK matching, a common 38-tag allowlist, reconnect
  state resynchronization, and listening/loading/reply/unclear lifecycle maps.
- Display commands run through the existing bounded non-blocking renderer queue;
  the renderer retains its 20 FPS target and smooth 350 ms transitions.
- Camera and Session transport share one compile-time 192 KiB JPEG bound;
  direct PSRAM DMA fails the build rather than silently changing driver mode.
- Isolated camera, microphone, speaker, display, and microSD profiles remain
  available only as deliberate bench diagnostics.
- Temporary display GPIO38/39/40 still conflicts with the onboard microSD bus;
  the card remains removed for the flashed display-capable image, and final
  one-board ownership remains an open integration gate.
- Nana Core remains the sole owner of identity, persona, memory, social state,
  STT, LLM reasoning, TTS policy, and planning.

Verification for the current boundary: Core config `18/18`, runtime lifecycle
`9/9`, Session media/display/camera/supervisor server `18/18`, firmware
supervisor source contract PASS, Core YuNet camera vision PASS, camera command
queue `3/3`, command registry `12/12`, architecture guard `3/3`, voice
audio-completion `10/10`, Python compile PASS, ESP-IDF 5.5.5 build PASS, and
physical display Session transport `8/8` PASS. The real board also passes
OV5640 start, warmup, stop, and three authenticated RAM-only VGA JPEG transfers
after Session workers and mic suspension. YuNet detected one face in all `3/3`
frames; transport errors were zero and no image was persisted.

The reproducible supervisor source build is `0x117990` bytes with `0x5f670`
bytes (`25%`) app-partition margin and SHA-256
`95103291069ABCE4E61D6CB9E143E9507F8FA728196F196747DFBFE023F22E28`.
The current Session protocol does not attest the running image hash. Physical
B5 acceptance therefore rests on bounded runtime telemetry plus the owner's
immediate-mute and clean-recovery observations, not on an inferred hash.
The integrated follow-up then completed a `640x480`/`18590B` snapshot with one
face at confidence `0.938`, followed by mic/Core/speaker/display operation.
Camera, uplink, and downlink completed `1/1`; display completed `4/4`; speaker
bytes were exactly `120320/120320/120320`; underruns, heartbeat timeouts,
failures, and last error were zero. The fixed stationary gate count is
`21/23`, or `91%`: `C4-DISPLAY-EVENTS`, basic-V1 `D2-STATE-SYNCHRONY`,
`B3-DISPLAY-COEXISTENCE`, `B4-CAMERA-COEXISTENCE`, and
`B5-SUPERVISOR-SAFETY` are closed. The
latest combined run accepted display commands `9/9`, returned to `listening`,
and drained `92160/92160/92160B` of speaker PCM with zero underruns and no
runtime error while the live mic/Core lifecycle remained active. Richer
animation behavior is a post-V1 enhancement.

Historical next order: resolve final pin/power ownership under `B6`, then run
`D4-SOAK`; the exact-1500 live gate and Opus follow-up are all deferred under
`ESP32-OWNER-HOLD-1` until Ba explicitly reopens Presence.

## Current Game Capability Development Policy

As of 2026-07-12, `OWNER-DIRECTED-GAME-CAPABILITY-FREEZE` is active.

This policy freezes Stardew, all Stardew farming work, osu, and other
game-specific development while preserving their code, tests, documents,
backups, and safety gates. These adapters are not current build targets and
must not appear in the next-task queue unless Ba explicitly names and unfreezes
one.

This freeze did not modify runtime code. Existing environment configuration can
still make an adapter report configured, enabled, or `auto_on`; none of those
states is proof of current live play or permission to resume development.

Authoritative scope: `NANA_GAME_CAPABILITY_FREEZE_STATE.md`.

## Current Private/Main LLM Route

As of 2026-07-12, the current private/main model stop point is:

```text
CORE-LLM-ROUTING-GPT56-TERRA-LIVE-DEFAULT
State: CLOSED
```

Current route ownership is explicit and lane-specific:

```text
private/main       gpt-5.6-terra | reasoning_effort=none
public             gpt-5.4-mini
cheap/casual       gpt-5.4-mini
autonomy banter    gpt-5.4-mini
reasoning sidecar  gpt-5.4
refiner/nuclear    gpt-5.5
```

`<NANA_REPO>\nana\config.py` declares the code defaults, while `<NANA_REPO>\.env`
contains the active local route. `<NANA_REPO>\nana\brain\llmgate_client.py` adds
`reasoning_effort=none` to normal and SSE Chat Completions payloads only when
the resolved request is the configured GPT-5.6 private/main model.

Private fallback order is:

```text
gpt-5.6-terra -> gpt-5.4 -> gpt-5.5 -> gpt-5.4-mini -> gemini-3.5-flash
```

Public, cheap, banter, reasoning, and refiner lanes retain their previous model
ownership. Luna is not an active runtime model. LLMGate transport, persona,
prompt construction, memory, voice, and TTS are unchanged.

Owner live verification recorded three successful private SSE events as
`LLMGate stream used: gpt-5.6-terra`, with no fallback. The tested replies
preserved Nana's persona and technical judgment; a constrained reply measured
458 characters against a requested 450-550 range, and the owner reported fast
response for normal short dialogue.

Rollback contract:

```text
NANA_LLMGATE_MAIN_MODEL=gpt-5.4
NANA_LLM_ALIAS_MAIN=gpt-5.4
NANA_LLM_ALIAS_CHAT=gpt-5.4
```

## Current Local Backup State

Current backup:

```text
<NANA_REPO>\backups\nana_current_20260713_210928
```

Previous verified rollback backup retained unchanged:

```text
<NANA_REPO>\backups\nana_current_20260709_132812
```

Stale local backups removed from the live machine:

```text
<NANA_REPO>\backups\nana_everything_20260621_150037
<NANA_REPO>\backups\nana_full_20260621_145226
<NANA_REPO>\nana\main.py.backup
<NANA_REPO>\nana\main.py.cut_backup
<NANA_REPO>\nana\main.py.cut_backup2
<NANA_REPO>\nana\main.py.cut_backup3
```

Do not tell new agents to inspect those old backup paths as current state.

## Current Voice Reliability Stop Point

As of 2026-07-12, the current private/local voice reliability stop point is:

```text
CORE-VOICE-HTTP-STREAMING-CALLBACK-LIVE-DEFAULT
State: CLOSED
```

Private/local reply semantics and voice delivery policy are separate:

```text
GPT reply mode: chat | story | casual | ...
Private voice policy: full
```

`<NANA_REPO>\nana\cli\chat_turn_pipeline.py` selects private voice policy `full`
without inspecting prompt markers. The old narrow reflective classifier is not
current code truth and must not be restored.

Default runtime gates:

```text
NANA_VOICE_STREAMING_ENABLED=1
NANA_VOICE_STREAMING_PILOT_ENABLED=1
NANA_VOICE_STREAMING_KILL_SWITCH=0
NANA_VOICE_STREAMING_DIRECT_ONLY=1
NANA_VOICE_STREAM_CALLBACK_OUTPUT_ENABLED=1
```

The defaults are declared in:

```text
<NANA_REPO>\nana\config.py
<NANA_REPO>\nana\voice\engine.py
```

For full replies up to 4,500 characters, `<NANA_REPO>\nana\voice\engine.py` selects
one ElevenLabs HTTP stream request. `<NANA_REPO>\nana\voice\lipsync.py` decodes the
continuous MP3 stream through FFmpeg, feeds a bounded PCM ring, and uses one
callback-driven `sounddevice.OutputStream`.

Completion invariant:

```text
provider_eof
AND decoder_clean_eof
AND pcm_queue_empty
AND pcm_ring_empty
AND callback_finished
=> audio_completed=True
```

Live owner evidence:

```text
medium: 968/968 | completed=True | remaining_chars=0
long:   3462/3462 | duration about 240s | completed=True
        remaining_chars=0 | abort=none | ring_starvations=0
auditory: clean from start through final phrase; no crackle, gap, or cut tail
```

The long run reported two PortAudio status underflows and 151.6 ms maximum
callback lateness, but the application ring retained 277.7 ms and no artifact
was audible. This is watch-only telemetry, not evidence for more buffer tuning.

This reliability milestone is closed. The approximately four-minute owner run
is a stress test beyond ordinary companion dialogue. Do not reopen HTTP
transport, PCM feeder/ring, callback output, buffering, or completion work
without a new reproducible audible failure. Voice expression, emotion tags,
and Voice Span Renderer belong to a separate future stage.

Rollback contract:

```text
NANA_VOICE_STREAM_CALLBACK_OUTPUT_ENABLED=0
  use blocking HTTP stream output

NANA_VOICE_STREAMING_KILL_SWITCH=1
  disable the HTTP stream path

NANA_VOICE_STREAMING_ENABLED=0
  use the legacy provider/full-file path
```

No legacy playback path was deleted. `STAGE-9AA` Voice Span Planner remains
preview-only and `tag_hint` remains metadata. Runtime inline audio tags are now
active through the separate provider adapter documented below; Voice Span
Renderer remains inactive.

## Current Inline Audio Tag Stop Point

As of 2026-07-12, the private/local inline-expression stop point is:

```text
CORE-VOICE-INLINE-AUDIO-TAGS-LIVE-CLOSEOUT
State: CLOSED
```

`<NANA_REPO>\nana\voice\inline_audio_tags.py` owns the curated provider adapter.
Private prompt construction receives its Eleven v3 tag guide, recognized tags
are hidden from terminal display, and normalized canonical tags remain in the
single text sent to ElevenLabs. `<NANA_REPO>\nana\voice\engine.py` reads the same
normalized result for cache identity, telemetry, and the secondary tone profile.

Current contract:

```text
private tagged reply
-> normalize aliases and enforce blocklist/max=5
-> hide recognized tags from terminal display
-> preserve canonical tags in one ElevenLabs HTTP stream
-> one callback playback and normal audio-completion invariant
```

The curated catalog contains 38 voice-direction tags and 6 pacing/breath tags.
Owner-disabled laughter/chuckle variants and `[softly]` remain blocked, along
with cough/throat-clear, shouting, singing, accents, sound effects, visual
actions, and multi-speaker timing directions. Unknown bracketed content is not
silently promoted into a TTS direction.

Live owner evidence:

```text
test 1 tags: thoughtful -> frustrated -> excited
status:      3/5 | blocked=0 | overflow=0 | unknown=0
audio:       767/767 | one HTTP stream | completed=True
feed:        rebuffer=0 | underflows=0 | ring_starvations=0

test 2 tags: annoyed -> short pause -> warmly
status:      3/5 | blocked=0 | overflow=0 | unknown=0
owner:       audible result accepted; stage closed
```

`STAGE-9AA` is still preview-only provider-neutral span metadata. Its
`tag_hint` is not the runtime source of inline tags, and Voice Span Renderer is
not active. Do not couple these systems or expand the curated catalog without
explicit owner direction or new reproducible audible evidence.

## Current Public/Core Text Stop Point

As of 2026-07-09, the current stream/output readiness stop point is:

```text
STAGE-9W-to-9Y-STREAM-AVATAR-VOICE-READINESS
```

Stream readiness, public avatar cue planning, and voice delivery planning are
implemented and smoke-verified in read-only mode. This does not mean live stream
mode is enabled:

- `/stream-ready-status` is a preflight dashboard. It can report
  `rehearsal_ready=True` while `live_ready=False` if stream state is offline.
- `/public-avatar-status` and `/public-avatar-preview <text>` build avatar cues
  from public intent, but VTS remains blocked while live/avatar policy is off.
- `/voice-delivery-status` and `/voice-delivery-preview <text>` plan voice
  packeting for long replies. They do not call TTS by themselves.
- Live private streaming replies also record their final 9Y delivery plan after
  the full reply is available, so `/voice-delivery-status` reflects the last
  real story/private stream turn.
- Private story voice dispatch defaults to one `voice.say(full_reply)` after the
  text stream completes. This keeps terminal text responsive while letting the
  voice engine split/fetch/play audio inside one TTS job, reducing gaps between
  spoken chunks. Mode A does not require an env var. Set
  `NANA_VOICE_STORY_LEAD_PACKETS_ENABLED=1` only when intentionally testing
  lead-packet rollback mode.
- Owner A/B listening confirmed the single-utterance path as the preferred
  runtime default. `/voice-budget-status` labels this as
  `story_mode=A_single_utterance`; rollback mode is `story_mode=B_lead_packets`.
  Legacy `NANA_VOICE_STORY_SINGLE_UTTERANCE_ENABLED=0` is ignored so stale env
  values do not silently disable Mode A.
- ElevenLabs fetch concurrency defaults to `3` and can be overridden with
  `NANA_ELEVENLABS_MAX_CONCURRENT=1..5`.

Historical verification snapshot on 2026-07-09 (superseded only for current
import-analyzer reporting by the 2026-08-12 snapshot above):

```text
smoke_stage9w_stream_ready_status.py 4/4
smoke_stage9x_public_avatar_reaction.py 5/5
smoke_stage9y_voice_delivery.py 5/5
smoke_stage9t_voice_reply_budget.py 5/5
smoke_command_registry_truth.py 12/12
smoke_stage3_stage_status.py 2/2
smoke_imports.py 0 issues
```

Previous public/core text stop point from 2026-07-08:

```text
STAGE-9G-to-9V-PUBLIC-CORE-TEXT-CLEAN-PAUSE
```

The Stage 9 public/core text layer is clean enough to switch workstreams:
identity challenge, service-role refusal, GPT-like voice self-check, repeated
quiet-room prompts, public viewer/session memory-lite, fluency polish, reply
evaluation/feedback, voice budget, core drift monitor, and core anchor recovery
are smoke-verified or user-run Discord checked. Do not keep polishing this layer
unless a regression appears. Prefer moving next to stream readiness, avatar
reaction, or voice/live delivery.

## Current Runtime Path

Current user-facing runtime entry:

```text
python -u <NANA_REPO>\nana\__main__.py
```

Current code path:

```text
<NANA_REPO>\nana\__main__.py
-> nana.cli.app.main()
-> nana.cli.handle_text.handle_text()
-> nana.core/*
-> nana.runtime/*
-> nana.brain/*
-> nana.memory / nana.runtime.memory_spine
-> nana.voice / nana.integrations / nana.social as needed
-> optional adapters only when command/gate enables them
```

Current verified import probe:

```text
handle_text_file= <NANA_REPO>\nana\cli\handle_text.py
nana.main_loaded= False
nana.main_cut_loaded= False
nana.game.stardew.registry_loaded= False
nana.game.osu.registry_loaded= False
nana.phases_loaded_count= 0
nana.runtime_loaded_count= 46
known_commands= 4499
stardew_static= 6
osu_static= 4
heavy_impl_loaded= False
```

Meaning:

- `<NANA_REPO>\nana\main.py` is not the current hot path.
- `<NANA_REPO>\nana\main_cut.py` is not the current hot path.
- Importing the current CLI dispatcher does not load `nana.main` or
  `nana.main_cut`.
- `<NANA_REPO>\nana\cli\handle_text.py` has been split into a thin dispatcher
  facade. It no longer loads `nana.phases` or the heavy command impl modules at
  import time.
- Remaining large command modules are lazy implementation buckets, not hot-path
  imports.

## Current Module Shape

Code size snapshot:

```text
cli              files=34   lines=8640
commands         files=15   lines=5546
core             files=18   lines=3104
runtime          files=74   lines=31698
brain            files=9    lines=3808
memory.py        files=1    lines=1258
voice            files=3    lines=1062
integrations     files=3    lines=623
game\stardew     files=165  lines=25714
game\osu         files=66   lines=28468
phases           files=29   lines=26374
autonomy         files=16   lines=2985
social           files=10   lines=2931
```

Interpretation:

- Core is partly split and real.
- `handle_text.py` is no longer the main gravity point. It is a small
  dispatcher facade.
- `commands/registry.py` is a facade. The old giant `registry_data.py` has been
  split into live/archive/reserved/adapter/regression buckets.
- `phases/` still carries old phase history and should be treated as
  compatibility/legacy unless a current router explicitly calls it.
- New agents must not treat the phase ladder as the current product
  architecture.

## Current Active / Legacy Boundary

Active core:

- `<NANA_REPO>\nana\__main__.py`
- `<NANA_REPO>\nana\cli\app.py`
- `<NANA_REPO>\nana\cli\handle_text.py`
- `<NANA_REPO>\nana\commands\*`
- `<NANA_REPO>\nana\core\*`
- `<NANA_REPO>\nana\runtime\*`
- `<NANA_REPO>\nana\brain\*`
- `<NANA_REPO>\nana\memory.py`
- `<NANA_REPO>\nana\voice\*`
- `<NANA_REPO>\nana\integrations\*`
- `<NANA_REPO>\nana\social\*`
- `<NANA_REPO>\nana\autonomy\*`

Legacy / archive / not build target:

- `<NANA_REPO>\nana\main.py`
- `<NANA_REPO>\nana\main_cut.py`
- most old phase command history under `<NANA_REPO>\nana\phases\`

`main.py` and `main_cut.py` can remain for audit until cleanup is proven, but
new work should not add features there unless the task is explicitly a narrow
extraction or archive cleanup. Old `main.py.*backup*` files were removed from
the live tree on 2026-07-09 after the current backup was created.

## Adapter State

Stardew:

```text
stardew_status= {'mode': 'auto_off', 'enabled': False, ...}
STARDEW_STATIC_COMMANDS:
  /stardew-status
  /stardew-observer-status
  /stardew-plan-status
  /stardew-goal-preview
  /stardew-bridge-status
  /stardew-help
```

Stardew current architecture:

```text
Python role: observer + planner
Executor: SMAPI/C# bridge
Default: no keyboard/mouse/pathfinding executor in Python
```

The current Stardew registry is a V2 shim. It does not reopen the retired
legacy Python movement stack.

osu:

```text
osu_status= {'mode': 'auto_on', 'enabled': True, ...}
```

Important caveat: this can be caused by `.env` containing a configured
`NANA_OSU_VENDOR_MODEL_PATH`. A configured path means osu is installed/known;
it is not proof that osu live play, real input, or score submission is active.

Discord:

- External Discord is a transport/bridge path, not Nana core identity.
- It should remain documented as an adapter/transport with live verification
  separate from smoke-only checks.

## Current Biggest Architecture Risk

The project is no longer blocked by `main.py`, `handle_text.py`, or the old
single-file command registry being large. The remaining risk is narrower:

```text
lazy command impl buckets + legacy phase/archive history + reserved registry strings
```

Current command boundary:

```text
<NANA_REPO>\nana\cli\handle_text.py              177 lines
<NANA_REPO>\nana\commands\registry.py             24 lines
<NANA_REPO>\nana\commands\registry_data.py         31 lines
<NANA_REPO>\nana\commands\registry_live.py       1391 lines
<NANA_REPO>\nana\commands\registry_reserved.py   3005 lines
<NANA_REPO>\nana\commands\registry_archived.py    117 lines
```

Registry truth:

```text
KNOWN_SLASH_COMMANDS total=4499
live=1397
archived=107
reserved=2995
known_registry=0
unknown=0
chat=0
```

Why this matters:

- The registry remains broad by design for suggestions and compatibility.
- Registry membership is not router truth.
- `registry_data.py` now makes that boundary explicit instead of presenting one
  giant flat command pile.

Current dispatcher split:

```text
handle_text.py
-> process/status/core identity routers
-> memory router
-> public stage routers
-> voice router
-> autonomy/stage/starter/review routers
-> adapter router
-> lazy core phase/action/phase-compat impl buckets
```

Do not start by rewriting game logic, TTS, Discord, or memory. The entrypoint
and command registry boundary are now honest enough; future cleanup should focus
on lazy impl size, old phase/archive history, or a specific feature/regression.

## Rules For Future Agents

- Do not claim `main.py` is the hot path unless fresh code proves it.
- Do not claim the whole project is green just because public core smokes pass.
- Do not claim an adapter is live because it is installed or configured.
- Do not add new feature work to `main.py`, `main_cut.py`, or old phase modules.
- If touching commands, start at `nana.cli.handle_text`, then move behavior into
  a domain router rather than adding more top-level imports.
- If touching Stardew, keep Python observer/planner only unless the user
  explicitly asks to change the contract.
- If touching osu, distinguish configured, enabled, running, real input, and
  score submit.
- If touching Discord, distinguish transport smoke from live end-to-end
  verification.

## 2026-08-06 Presence Audio Transport Truth

The current Presence Session source and latest ESP32-S3 build include an
authenticated, half-duplex, progressive PCM16/16 kHz mono downlink. The
previous complete-buffer playback path is still available and is selected when
the node or provider stream is unavailable.

Core sends unknown-length provider output as it arrives. The node advertises
`audio_downlink_stream`, accepts credited 1024-byte CRC32 frames, buffers eight
frames in PSRAM before enabling the speaker, and reports playback start,
completion, abort, first-audio timing, queue high-water, and underruns. This is
an implementation fact; it is not yet a physical latency acceptance claim.

The tested local contracts are server 19/19 and VoiceEngine PCM 7/7. The next
truth to establish is a real ElevenLabs -> Core -> COM14 -> MAX98357 turn with
the measured first-audio timestamp and zero underruns. LLM and STT latency are
separate from this transport and will not disappear merely by changing the
speaker link.

## 2026-08-06 Presence Re-Provisioning After Flash

After the progressive-stream firmware was flashed, COM14 remained visible but
the node reported `No valid Presence session config` and waited at
`NANA_SESSION_PROVISION_READY`. The flash had removed the node's NVS URI/token
pair; this was a provisioning-state issue, not a Wi-Fi, audio, or WebSocket
transport failure.

`provision_presence_session.py` now supports `--use-core-token`. It reads the
existing Windows DPAPI-protected Core credential in memory, sends it over the
USB provisioning channel without printing it, and pairs the node with the
current Core URI. The recovery command is:

```bat
python <NANA_REPO>\nana_presence_node\tools\provision_presence_session.py --port COM14 --uri ws://<CORE-LAN-IP>:8765/presence/v1 --use-core-token
```

The live recovery on 2026-08-06 saved `ws://<LOCAL_IP>:8765/presence/v1`
successfully; the board then established an active TCP session from
`<LOCAL_IP>` and reported `mic=capturing`.

## 2026-08-06 Presence Source-Refill Truth

The current progressive PCM path does not show hard data loss: accepted live
runs reported exact sent/received/played counts and zero node underruns. The
occasional light audible buzz is therefore still an observation, not proof of
a WebSocket cut.

Core previously requested 8192-byte HTTP body chunks. At PCM16/16 kHz mono
that is 256 ms, exactly the node's full eight-frame startup prebuffer. The
default is now 2048 bytes (64 ms). WebSocket media remains credited 1024-byte
CRC32 frames, and the ESP32 prebuffer remains eight frames/256 ms. This changes
refill granularity without increasing startup latency or weakening bounds.

Presence-specific source telemetry now includes:

```text
last_presence_pcm_network_chunks
last_presence_pcm_network_bytes
last_presence_pcm_max_network_gap_ms
last_presence_pcm_first_byte_ms
last_presence_pcm_first_audio_ms
```

`max_network_gap` is the maximum observed gap between source pulls at Core; it
can include upstream/provider delay and Core flow-control backpressure. It is
diagnostic evidence, not by itself proof that the ESP32 I2S DMA starved.

`/presence-stream-tone` is a bounded explicit command. It sends generated
PCM through the same progressive WebSocket/PSRAM/I2S route while excluding
ElevenLabs, STT, LLM, and microphone input. It never runs automatically.

## 2026-08-07 Presence Provider Read-Ahead Truth

The old pull-based source coupling described above is no longer current.
`VoiceEngine._iter_presence_pcm_response()` now owns a bounded producer thread
that reads provider PCM independently of Presence playback credits. It queues
at most 32 x 2048-byte chunks (64 KiB, about 2.0 seconds at PCM16/16 kHz mono).
Stopping or failing playback closes the HTTP response, signals the producer,
and performs a bounded thread join; the queue cannot grow without bound.

`last_presence_pcm_max_network_gap_ms` now measures provider `iter_content`
read blocking rather than time between pull calls. The status label is
`provider_wait`. Core additionally publishes prefetch high-water,
post-start starvation count, and maximum empty-queue wait. The ESP32 binary,
eight-frame/256 ms PSRAM prebuffer, 1024-byte credited frames, CRC32, hard mute,
and half-duplex lease are unchanged.

Live evidence supersedes the optimistic prefetch checkpoint: bounded producer
prefetch is now experimental and defaults OFF. A live 2320 ms source took
6840 ms to play and repeated syllable blocks while the prefetch queue was full,
Core starvation was zero, and node hard underruns were zero. The production
source mode is `pull_safe`. Set `NANA_PRESENCE_PCM_STREAM_PREFETCH_ENABLED=1`
only for an explicit A/B experiment.

Exact sent/received/played byte counts attest transport integrity, not temporal
playback integrity. `last_presence_pcm_playback_ratio` and
`last_presence_pcm_playback_grade` now expose stretched playback separately.
