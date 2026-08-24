# Nana Architecture Core

## 2026-07-08 Architecture Reading Model

Nana architecture must be read in three levels:

```text
Level 1 - Vision:
  NANA_ARCHITECTURE_VISION.md
  Five conceptual blocks: Identity, Mind, Runtime, Capability, Infrastructure.

Level 2 - Architecture:
  NANA_ARCHITECTURE_CORE.md
  Major systems and boundaries inside those five blocks.

Level 3 - Implementation:
  NANA_CURRENT_CODE_TRUTH.md and subsystem docs.
  File paths, routers, adapters, bridges, smokes, and legacy/archive state.
```

This file is Level 2. It should explain system boundaries, not make every
implementation file look like a peer of Nana's identity.

## 2026-08-12 Reconciliation Snapshot

The current Level 3 runtime facts used by this architecture document are:

```text
handle_text.py=177 lines
registry total=4499; live=1397; archived=107; reserved=2995
runtime_loaded_count=46
smoke_command_registry_truth.py=12/12 PASS
smoke_imports.py=2 known analyzer warnings; direct imports PASS
nana.main_loaded=False; nana.main_cut_loaded=False
game_loaded_count=0; phase_loaded_count=0
```

The two import warnings are analyzer false positives for
`bytearray/memoryview` in `decode_media_frame` and `SystemExit` in the
read-only Session-core tool. They are not runtime import failures. The
`ESP32-OWNER-HOLD-1` remains `WAITING_OWNER` / `DEFERRED`; the game
capability freeze also remains active.

Current interpretation:

- Nana remains companion-first and streamer-capable.
- Stream, VTS, OBS, TTS, Discord, Stardew, osu, browser, and local tools are
  capabilities/adapters or output/presence layers.
- They must not be documented as the root identity of Nana.
- `handle_text.py`, `commands/registry.py`, `phases/`, `main.py`, and bridges
  are Level 3 implementation details.
- Do not pivot Nana away from stream capability unless the user explicitly
  changes that decision.

## 2026-07-13 Closed Runtime Foundation

The current core foundation has explicit ownership, lifecycle, startup
configuration, and capability boundaries:

```text
nana.autonomy.loop
  owns canonical AUTONOMY_LOOP identity and its thread

nana.cli.app
  owns runtime start/stop rights and top-level task handles
  validates one sanitized startup snapshot before construction

nana.runtime.capabilities
  owns lazy capability loading; active core does not import games directly
```

`CORE-CONFIG-CONTRACT-1` defines startup configuration as a fail-fast boundary,
not a replacement for every provider tuning variable. Invalid
startup-critical values stop before voice, VTS, autonomy, or bridge
construction. Degraded but safe combinations remain visible as warnings. API
keys and voice IDs never enter the snapshot or status text.

This foundation is frozen under `CORE-ARCHITECTURE-BASELINE-1`. New capability
work should attach at the boundary without creating another autonomy owner,
another lifecycle manager, or direct core-to-game dependency. The current
verified snapshot is `<NANA_REPO>\backups\nana_current_20260713_210928`.

## 2026-07-20 Presence Node Boundary

The ESP32-S3 Presence Node belongs to Nana's Infrastructure and
Output/Presence layers. It is a removable physical adapter, not an Identity or
Mind owner.

```text
Nana Core on PC
  owns identity, persona, memory, social state, authorization,
  STT, LLM, TTS policy, and planning

PC Presence Adapter
  owns device session, protocol validation, buffering, backpressure,
  retries, health, and Core-to-hardware translation

ESP32-S3 Presence Node
  owns local deadlines, peripheral drivers, bounded buffers, VAD,
  display animation, camera capture, watchdogs, and fail-safe mute
```

The adapter must expose capabilities rather than leak board-specific GPIO or
driver details into Nana Core. Core may request semantic actions such as
`listen`, `speak`, `set_expression`, `trigger_gesture`, or `sample_camera`; it
must not own I2S clocks, SPI transactions, amplifier GPIO, or camera frame
buffers.

Stationary V1 uses half-duplex audio and bounded camera sampling. It does not
include mobility, always-on face recognition, automatic identity enrollment,
full-duplex echo cancellation, or ESP32-side memory/LLM/TTS. Detailed
implementation and progress live in `NANA_PRESENCE_NODE_STATE.md`.

## 2026-07-08 Current Code Truth / Dispatcher Boundary

Current code-backed runtime path:

```text
<NANA_REPO>\nana\__main__.py
-> nana.cli.app.main()
-> nana.cli.handle_text.handle_text()
-> nana.core/*
-> nana.runtime/*
-> nana.brain/*
-> nana.memory / nana.runtime.memory_spine
```

`<NANA_REPO>\nana\main.py` is not the hot path. `<NANA_REPO>\nana\main_cut.py` is not the
hot path. Current import probe shows both stay unloaded when importing the
current CLI dispatcher:

```text
nana.main_loaded=False
nana.main_cut_loaded=False
```

The current architectural problem has moved:

```text
old problem: main.py gravity
2026-07-08 problem: handle_text.py + commands/registry.py + phases/ gravity
2026-07-09 current problem: lazy command impl buckets + legacy phase/archive history
```

Observed reality:

- `<NANA_REPO>\nana\cli\handle_text.py` is now a thin dispatcher facade
  (`177` lines as of 2026-08-12).
- `<NANA_REPO>\nana\commands\registry.py` is a compatibility facade (`24` lines).
- `<NANA_REPO>\nana\commands\registry_data.py` is a layered assembler (`31` lines),
  not a giant flat registry.
- Registry data is split into live/archive/reserved/adapter/regression buckets:
  `live=1397`, `archived=107`, `reserved=2995` as of 2026-08-12.
- Importing the dispatcher no longer loads `nana.main`, `nana.main_cut`,
  Stardew/osu registries, `nana.phases`, or heavy command impl buckets.
- This reduces the chance that new agents mistake old phase history or reserved
  command aliases for current core identity.

Current boundary:

- Active core: `__main__.py`, `cli`, `commands`, `core`, `runtime`, `brain`,
  `memory.py`, `voice`, `integrations`, `social`, and `autonomy`.
- Optional adapters: `game/stardew`, `game/osu`, external Discord bridge.
- Legacy/archive: `main.py`, `main_cut.py`, and most old phase command history
  under `phases/`. Old `main.py.*backup*` files were removed from the live tree
  on 2026-07-09 after the current backup was created.

Current adapter interpretation:

- Stardew is V2 observer/planner/bridge only. Python must not become the
  keyboard/mouse/pathfinding executor again unless the user explicitly changes
  the contract.
- osu being configured by `.env` is not the same as live play, real input, or
  score submission.
- Discord is a transport adapter, not Nana's core identity.

Completed architecture move:

```text
handle_text.py
-> domain routers
-> lazy impl buckets for heavy command groups
-> layered command registry facade
```

Do not begin the next cleanup by rewriting game logic, TTS, Discord, or old
phase modules. The entrypoint and command boundary are now honest enough; the
next cleanup should target a specific lazy impl bucket, phase/archive surface, or
feature regression.

## 2026-07-09 Stream / Avatar / Voice Readiness - STAGE-9W to STAGE-9Y

Current stream-facing architecture has moved from "next plan" to read-only
preflight and output-planning modules:

```text
STAGE-9W Stream Ready Status
-> STAGE-9X Public Avatar Reaction
-> STAGE-9Y Voice Delivery Planner
```

Architectural meaning:

- Stream readiness is a dashboard, not a permission grant. It can report
  `rehearsal_ready=True` while `live_ready=False` when stream state is offline.
- Public avatar reaction maps public reply intent into expression/body cues, but
  VTS remains policy-gated and must stay blocked while avatar/live policy says
  no.
- Voice delivery plans how long private/story replies should be packeted for
  speech. It does not call TTS by itself; it gives the runtime a safer
  lead-then-tail strategy so long replies do not lose their remaining text.
- Live private streaming replies now record their actual 9Y delivery plan after
  the full reply is available, so the status surface reflects real story turns
  as well as previews.
- For smoother story playback, private story mode streams text first but sends
  one full utterance to `voice.say()` after the reply completes. The voice engine
  then does internal chunk splitting/fetching/sequencing inside one job instead
  of creating multiple lead-packet jobs with audible gaps.
- Owner A/B listening chose this as Mode A, the runtime default. Mode B remains
  available only as rollback via `NANA_VOICE_STORY_LEAD_PACKETS_ENABLED=1`.
  The old `NANA_VOICE_STORY_SINGLE_UTTERANCE_ENABLED=0` toggle is intentionally
  ignored so Mode A stays always-on by default.
  Do not add `LEAD_ONE_FIRST`, prebuffering, or a new scheduler unless new live
  evidence shows Mode A is insufficient.
- ElevenLabs burst fetches default to concurrency `3` with
  `NANA_ELEVENLABS_MAX_CONCURRENT` override, because live testing showed rate
  limits at more aggressive burst settings.
- These modules belong to the output/presence capability layer. They do not
  redefine Nana's root identity.

Current commands:

```text
/stream-ready-status
/public-avatar-status
/public-avatar-preview <text>
/voice-delivery-status
/voice-delivery-preview <text>
```

Verification state on 2026-07-09:

```text
smoke_stage9w_stream_ready_status.py: 4/4
smoke_stage9x_public_avatar_reaction.py: 5/5
smoke_stage9y_voice_delivery.py: 5/5
smoke_stage9t_voice_reply_budget.py: 5/5
smoke_command_registry_truth.py: 12/12
smoke_stage3_stage_status.py: 2/2
smoke_imports.py: 0 issues
```

No live Nana runtime, TTS, VTS, OBS, Discord, Stardew, osu, or game input was
run for this verification.

## 2026-07-08 Public/Core Text Clean Pause - STAGE-9G to STAGE-9V

Current public/core text architecture is clean enough to pause here:

```text
STAGE-9G Core Self
-> STAGE-9H Public Voice Style
-> STAGE-9I Public Conversation Director
-> STAGE-9J Public Thread Memory-lite
-> STAGE-9K Public Running Joke Bank
-> STAGE-9L Public Fallback Recovery
-> STAGE-9M Public Memory/Test-noise Filter
-> STAGE-9N Public Viewer Memory-lite
-> STAGE-9O Public Scene Builder
-> STAGE-9P Public Vietnamese Fluency Polish
-> STAGE-9Q Public Reply Evaluator
-> STAGE-9R Public Reply Feedback
-> STAGE-9S Public Quiet-room Rhythm
-> STAGE-9T Voice Reply Budget
-> STAGE-9U Core Drift Monitor
-> STAGE-9V Core Anchor Recovery
```

Architectural meaning:

- Core Self and Core Anchor Recovery keep Nana from collapsing into a bot,
  helpdesk, or obedience-only assistant.
- Public voice, scene, and quiet-room rhythm shape the room-facing style without
  redefining Nana's identity.
- Public thread/viewer memory and running jokes are session-lite continuity,
  not durable private memory.
- Memory/test filtering protects post-stream learning from rehearsal spam.
- Fluency polish, reply evaluation, and feedback form a deterministic public
  reply quality loop.
- Voice reply budget protects private/story voice delivery from runaway TTS
  length, but does not grant TTS permission by itself.

Verification state on 2026-07-08:

```text
9G 8/8, 9H 17/17, 9N 5/5, 9O 6/6, 9P 5/5, 9Q 7/7,
9R 6/6, 9S 5/5, 9T 5/5, 9U 4/4, 9V 4/4.
smoke_stage7g_public_stage_identity.py: 35/35.
```

User-run Discord transcript was clean for identity challenge, service-role
refusal, GPT-like voice self-check, and quiet-room prompt.

Rule for future agents: treat this as the public/core text pause point. Do not
keep adding text polish unless a regression appears. Prefer moving next to
stream readiness, avatar reaction, or voice/live delivery.

## 2026-07-08 Public Core Stage 9G-9M Code Sync

Current code-observed public Core layer now extends beyond the 2026-06-27
audit batch:

```text
STAGE-9G Core Self
-> STAGE-9H Public Voice Style
-> STAGE-9I Public Conversation Director
-> STAGE-9J Public Thread Memory-lite
-> STAGE-9K Public Running Joke Bank
-> STAGE-9L Public Fallback Recovery
-> STAGE-9M Public Memory/Test-noise Filter
```

Meaning:

- Core self owns stable Nana identity, values, and service-role boundaries.
- Public voice style owns how Nana sounds on stage, not who Nana is.
- Conversation director and thread memory-lite provide short-lived public-room
  continuity without persistence or action permission.
- Running jokes and fallback recovery make public replies less repetitive and
  less generic while staying deterministic.
- Public memory filter marks repeated tests/rehearsals so post-stream learning
  does not treat them as organic audience preference.

Safety:

- These layers are public-stage Core behavior and metadata only.
- They do not grant game/Discord/VTS/TTS/OBS action permission.
- STAGE-9M affects learning eligibility/weight for post-stream review; it is
  not a durable memory write by itself.

Verification state:

```text
9G 8/8, 9H 17/17, 9I 6/6, 9J 5/5, 9K 5/5, 9L 5/5, 9M 7/7.
9C-A1 is now 9/9.
9F remains 3/3.
imports 0 issues, help 29/29, stage-status 2/2.
```

Public guard regression status:

```text
smoke_stage7g_public_stage_identity.py: 35/35
Resolved: fallback variety/tracking tests now pass.
```

The previous partial-fail blocker is no longer current. Live verification
remains separate and user-run only.

## 2026-06-27 Core Code Reality Check

Current code-checked core path:

```text
python -u <NANA_REPO>/nana/__main__.py
-> nana.cli.app.main()
-> nana.cli.handle_text.handle_text()
-> nana.commands / nana.core / nana.runtime
```

This was rechecked against code, not inferred from chat memory:

- `<NANA_REPO>/nana/__main__.py` imports `nana.cli.app.main`.
- `<NANA_REPO>/nana/cli/app.py` imports and awaits
  `nana.cli.handle_text.handle_text`.
- Import probe: `nana.cli.handle_text` resolves to
  `<NANA_REPO>/nana/cli/handle_text.py`, exposes `handle_text`, and does not load
  `nana.main`.
- `<NANA_REPO>/nana/main.py` still exists, but current CLI command routing is not
  built through it.

Current core state:

- Core Nana is companion-first: chat, persona, memory, awareness, voice/TTS
  wiring, public-stage identity, autonomy, social stream state, avatar planning,
  post-stream review/lessons, and correctness audits.
- Historical 2026-06-27 stop point was
  `STAGE-9D-9E-9F-AUDIT-BATCH-SMOKE-VERIFIED`; current 2026-07-08 code sync
  shows public Core work through STAGE-9M, and the public guard regression is
  resolved at 35/35.
- Stage 9D/9E/9F are audit/preview-only:
  lane leak audit, memory consolidation preview, and context budget audit.
- Stage 9C-B owner-approved lessons can write only the dedicated
  `memory["post_stream_lessons"]` lane after explicit owner approval.
- Mood continuity and intention planner are prompt/context bias only; they do
  not grant action permission.
- Stream/avatar/starter layers remain gated. Real dispatch paths stay behind
  their own enable gates.
- Stardew and osu remain optional game adapters/capabilities, not the core
  identity of Nana.

Verification run from code audit:

```text
smoke_stage9d_lane_leak_audit.py: 3/3
smoke_stage9e_memory_consolidation_preview.py: 3/3
smoke_stage9f_context_budget_audit.py: 3/3
smoke_stage9c_b_post_stream_lessons.py: 6/6
smoke_stage9c_a1_post_stream_review.py: 8/8
smoke_stage9c_a0_session_review_adapter.py: 8/8
smoke_imports.py: 0 issues
smoke_core_help_surface.py: 29/29
smoke_stage3_stage_status.py: 2/2
smoke_stage7g_public_stage_identity.py: 35/35
```

Rule for future agents: if a claim about Core Nana conflicts with this section,
check the code path above first, then update the wiki with fresh evidence.

## 2026-06-23 Runtime Rewire / Main.py Clarification

Current core runtime direction:

```text
python -u <NANA_REPO>/nana/__main__.py
-> nana.cli.app
-> nana.cli.handle_text
-> nana.core/*
-> nana.runtime/*
```

`<NANA_REPO>/nana/main.py` still exists and is still large, but it is no longer the
new-runtime command hot path. Treat it as legacy/compatibility/remaining
parking-lot code. `main_cut.py` is an artifact, not the place to build new
features. Old `main.py.*backup*` files were removed from the live tree on
2026-07-09 after the current backup was created.

Rule for future work: if a feature needs code that still lives in `main.py`,
extract that narrow piece into the owning module and verify it. Do not route new
architecture back through monolithic `nana.main`.

See `NANA_RUNTIME_REWIRE_STATE.md` for the exact file roles.

## 2026-06-23 Branch Restore / Before-After Note

Current restored baseline:

```text
Core Nana:
  before = legacy `nana.main` gravity in CLI/help/status
  now    = CORE-REWIRE-1E new-runtime command surface

Stardew:
  before = old Python movement / BusStop / static-travel experiments
  now    = V2 Observer -> Planner -> SMAPI/C# Bridge through 8G dry-run preview

osu:
  before = old executor/phase ladder could be mistaken as active
  now    = Boss/Cursor Dance survival, with tosu bridge and DELETE kill switch
```

This does not mean Stardew can walk/play fully yet. It means the correct branch
has been restored: Python observes/plans, SMAPI bridge previews/validates, and
real execution remains locked behind a future explicit approval.
## 2026-06-17 Resync Note

Current project stop point: osu Cursor Dance survival profile is accepted by
the user as good enough, and osu updates are stopped until the user explicitly
resumes them. Do not open more osu coding/tuning phases by default. Stardew
movement remains preserved separately for later return.

Last updated: 2026-06-17

Purpose:

This file preserves the core architecture direction for Nana so future chats do not need the full long conversation history.

## Core Identity

Nana is an embodied AI companion inside the user's PC/game/workflow environment.

For Stardew Valley, Nana should not be treated as a simple command macro. The desired model is:

- Nana is the driver.
- The API/bridge/supervisor is the driving instructor sitting beside her.
- The instructor may observe, brake, abort, reobserve, or request a reroute.
- The instructor should not become the driver for every tiny step unless the situation is unsafe.

Nana is also a player, not a wiki:

- Nana needs game sense, not encyclopedia memory.
- Store knowledge that helps her act: current location, route, anchors, safety,
  current objective, and stable decisions.
- Do not stuff every temporary log, hypothesis, or trivia item into memory.
- Do not turn ordinary clear movement into scientific tile-by-tile research.
- When the route is clear, Nana should move like a player: hold direction,
  watch the world, and brake only when the instructor has a real reason.

Adapter boundary:

```text
Nana core is companion-first.
Games are optional adapters/capabilities.
Stardew is a separate adapter and may be toggled.
osu is a separate adapter branch and does not share Stardew logic.
```

## Main Product Goal

The long-term goal is a livestream/VTube-capable Nana:

- responsive enough to feel present,
- visually and behaviorally believable,
- able to act in the game without long silent delays,
- able to explain or emote while acting,
- safe enough to avoid runaway input.

## Current Architecture Direction

The locked direction is:

```text
World Grid Layer
-> Dynamic Overlay
-> Movement Controller v2
-> Travel / Farming / Interaction
```

This means Nana should not rely on collision timeouts as her primary way to understand the world.
She needs a 2D understanding of the map: walkable tiles, blockers, walls, doors, warps, turns, narrow passages, NPCs, crops, tools, and interactable objects.

## System Segment Boundaries

The older `system_architecture_segments.md` file remains useful as a stable
architecture anchor. Treat these as segment boundaries, not current phase
progress:

```text
Game Adapter
-> Telemetry Snapshot
-> Freshness Guard
-> World Model
-> Objective Layer
-> Planner
-> Safety Supervisor
-> Gate Layer
-> Policy Layer
-> Movement / Interaction Runtime
-> Executor
-> Verifier
-> Recovery Manager
-> Trace Recorder
```

Meaning:

- Game Adapter talks to SMAPI/GameBridge and reads raw state.
- Telemetry Snapshot normalizes map, tile, facing, menu, inventory, time,
  stamina, and interrupt state.
- Freshness Guard decides fresh/stale/unknown/unsafe.
- World Model owns passability, doors, warps, anchors, blockers, and learned
  map memory.
- Planner and Objective Layer translate intent into route/action plans.
- Safety/Gate/Policy decide allow, hold, reobserve, recover, or block.
- Movement Runtime compresses routes into leases, micro pulses, checkpoints,
  drift handling, and stuck handling.
- Executor sends input only after gates approve.
- Verifier confirms post-state after every action or segment.
- Recovery Manager handles stale telemetry, stuck movement, wrong menu,
  route fail, inventory full, shop closed, and similar faults.
- Trace Recorder stores before/input/after/verifier/recovery data for replay.

Hard boundaries:

- `nana.game.stardew` owns Stardew maps, bridge data, NPC/shop/farm/crop/chest/
  shipping semantics, tile calibration, warps, doors, and Stardew routines.
- Shared runtime/core should own only generic interfaces and repeated runtime
  patterns after they are proven.
- Phase modules are checkpoint/release/regression surfaces. They should not
  become the permanent live runtime shape.
- Companion/persona/memory code must not read Stardew bridge files directly.
  It should consume normalized runtime events or summaries.
- Game modules must not orchestrate OBS/stream tools directly, and OBS/stream
  integrations must not dispatch game input.

Long-term package direction:

```text
nana/
  companion/        # chat, persona, memory, voice, stream presence
  runtime/          # telemetry, freshness, gate, policy, trace, verifier contracts
  game/
    stardew/        # Stardew adapter, world model, planner, executor
    future_game/
  integrations/
    obs/
    vts/
    voice/
    browser/
```

Dependency direction:

```text
Companion orchestrates Runtime.
Runtime calls game/integration capabilities through explicit interfaces.
Game modules and integrations do not directly orchestrate each other.
```

## Stream Persona Boundary

Locked direction after osu phase 10b:

```text
Game adapter = sensor/action/data provider.
Companion = Nana's identity, speech rules, personality, and public stream persona.
Output layer = VTS / ElevenLabs / subtitle / OBS.
```

The stream persona must live in the companion layer, not inside osu or Stardew.
Game adapters should only send filtered/safe event data:

```text
osu:
  play/menu/result state
  song/difficulty
  aim status
  reaction hint

Stardew:
  farming/fishing/travel/shop state
  safe location/activity summary
  blocker/completion state
  reaction hint
```

Companion then decides:

```text
may Nana speak?
which public persona voice should be used?
should output be silent, subtitle, voice, or avatar reaction?
```

Gameplay silence rule:

```text
When actively playing a map/gameplay segment:
  no voice
  no subtitle/chat line
  no stream commentary line
  VTS/reaction/status output may still update silently

When in menu/result/break/talk mode:
  speaking can be allowed by gate
  stream persona may generate public-safe lines
  voice/subtitle can be added later
```

Stream presence rule:

```text
Nana is a VTS character / virtual streamer, not a gameplay-only bot.

During gameplay:
  Nana should stay verbally silent.
  VTS reaction/status may update silently by broad state, not by every hit circle.
  Example states: tracking, fast_jump, dense_pattern, idle, result, fail.

During menu/result/break:
  Nana may speak if the speak gate allows it.
  Dialogue should flow through public-safe event filtering before subtitle,
  voice, or VTS output.
```

Before opening any real game input, readiness should include both stream
presence and executor safety:

```text
Stream Presence Readiness:
  VTS readiness:
    connected/authenticated
    hotkey/reaction usable
    fail -> no_vts_continue, no crash

  Voice/TTS readiness:
    provider configured
    ElevenLabs/API or local runtime available if exposed
    fail -> subtitle_only_mode

  Core chat reply TTS uses a sequencer (reorder buffer + dynamic
  timeout + random fallback phrase), not arrival-order playback.
  See `NANA_CORE_CHANGELOG.md` for the current implementation state.

  Dialogue readiness:
    stream persona prompt/config loaded
    mode: template | llm | fallback
    safe public event filter active
    fail -> template fallback

  OBS/subtitle readiness:
    subtitle path/parent writable
    public subtitle status fresh/stale
    write/clear gates known
    OBS API not required; OBS can watch the text file
    fail -> voice_or_vts_only, no crash

Executor Readiness:
  tosu/gameplay/calibration/benchmark/trajectory/schedule ready
  focus/window readiness available
  emergency abort/release readiness available
  executable remains false until a separate explicit input phase
```

Public event filter allowlist:

```text
Allowed:
  game=osu
  activity_state
  sanitized song/difficulty
  accuracy/misses/rank/score/combo/max_combo/passed
  osu_status/reaction/safe_hint
  stream_mode/speak_allowed

Blocked:
  local path
  API key/token/secret
  private chat history
  private memory
  system commands
  raw file contents
  username/account unless explicitly whitelisted
  timing/screen coordinates unless needed for public viewer text
```

Phase order:

```text
Phase 11: Game Silence Gate / Stream Speak Gate
Phase 12: Safe game event -> Nana core/companion ingest preview
Phase 13: Companion-level Stream Persona Core / template variation
Phase 14: ElevenLabs / subtitle / OBS output
```

Phase 12 scope:

```text
This is a data-path phase, not a personality-writing phase.

Goal:
  prove that filtered game events reach Nana core/companion and that the core
  can return a public-safe decision preview.

Examples of safe event fields:
  game
  activity_state: gameplay | menu | result | break
  song/activity
  difficulty/context
  reaction_hint
  score/accuracy/combo/misses/rank/result summary when available
  speak gate result

Core decision preview:
  received=True
  private_data=filtered
  stream_mode=silent_play | talk_ready | quiet_waiting
  speak_allowed=True/False
  assessment tags such as low_score, high_miss_count, good_accuracy, passed
  line_candidate=None during gameplay
```

Personality text, richer wording, randomized phrasing, ElevenLabs, OBS, and
subtitles should wait until after this data path is verified.

## Game Adapter Policy

```text
Adapter gates keep the core boot clean.

Stardew:
  adapter-only
  off by default in core-only mode
  can be enabled manually or by active-window auto rules

osu:
  adapter-only
  separate branch
  phase 1 currently covers bridge + .osu parse + router preview only
  no Vision
  no real input
  no online score submission
```

## Movement Philosophy

Nana should move like this:

```text
wide clear path:
  smooth held movement

medium path:
  shorter held lease

near turn / door / warp / target / NPC / blocker:
  slow down and use micro-pulse

stale bridge / unsafe / wrong map:
  no input
```

Avoid:

```text
one tile -> stop -> observe -> one tile -> stop
```

That is safe but too robotic for livestream.

Desired behavior:

```text
go fast when sure,
go slow when uncertain,
stop only when unsafe.
```

Product direction lock:

```text
Do not over-optimize Nana into a knowledge encyclopedia or a tile-by-tile
lab instrument.
The main path is the older practical driver-led model:
  know the route, hold the key, watch while moving, brake near real danger,
  reobserve, continue.
The newer fragmented micro/partial stack remains a safety tool, not the main
story for ordinary travel corridors.

Quarantine rule:
  keep the newer fragmented code in the repo if needed for safety/fallback,
  but cut it out of the ordinary travel happy path.
  Do not let it preempt clear travel when Nana can just keep walking like a
  player with an observer beside her.
```

Current architecture reminder:

```text
Nana is a game-playing embodied companion, not a NASA rover.
Clear, known, low-risk movement should be driven as intentional held movement
or bounded corridor/batch movement. The supervisor is the driving instructor:
it may brake, abort, reobserve, or replan, but it should not turn every clear
route into one-tile macro pulses.
```

## Driving Instructor Law

This is now a locked movement doctrine:

```text
Nana holds the steering wheel.
The supervisor is the driving instructor.
The instructor must not take the steering wheel from Nana.
The instructor may only brake or interrupt when safety requires it.
```

Allowed supervisor roles:

```text
observe
warn about turn / warp / target / NPC / blocker
request slow-down or micro only near real precision zones
brake on unsafe state
release_all on abort/unsafe
request reobserve/replan on stale, drift, wrong map, or blocked route
```

Not allowed as the default clear-path behavior:

```text
turn a clear route into one-tile macro pulses
open a new phase for every ordinary stop reason
replace Nana's held movement with supervisor-controlled step-by-step driving
micro-manage BusStop/Farm corridor progress when safety is clear
```

Implementation meaning:

```text
Executor = Nana driving layer
Supervisor = permission/brake layer

clear route:
  held-route / guarded-long-chain / path-run style held batch

within <=2 tiles of warp, door, target, turn, NPC, blocker during ordinary travel:
  slow down or micro-align

3-tile precision behavior is no longer the default for normal travel corridors.
It can remain only for justified hazard/NPC/special-domain cases because the
user observed that entering micro at 3 tiles makes Nana feel too stop-start.

unsafe/stale/off-route/menu/focus/wrong-map:
  release_all + hold/reobserve/replan
```

Current active movement slice:

```text
Farm-local approach -> Farm outbound corridor -> FarmToTown trusted corridor
```

When Nana starts at an arbitrary Farm tile such as `Farm [69,20]`, she is not
yet on the trusted `Farm y=17` outbound corridor. The architecture should first
acquire that outbound lane using normal movement-controller/held-route logic,
then hand off to the `FarmToTown` trusted corridor. This is not a reason to
create BusStop work phases or debug BusStop stop reasons again.

## Safety Rules

Live input must always be gated:

- dry-run before live where practical,
- exact operator token,
- env gate enabled,
- cadence/freshness ready,
- no menu/dialogue/saving/combat unsafe state,
- release movement keys on every exit path.

Any live movement path should have a finally-equivalent release-all guard.

## Strategic Foundation

The user wants to focus on two foundations first:

1. Movement.
2. Farming.

If these two become solid, most other systems become simpler:

- shopping = travel + interaction + menu choice,
- fishing = travel + tool action + minigame,
- mining = travel + tool/combat behavior,
- foraging = scan + route + pickup,
- daily planning = route/action scheduling on the same grid.

Product priority clarification:

```text
Primary Nana gameplay direction:
  farming + fishing

Money/support systems:
  crop income, fishing income, light travel/shopping as support

BusStop / Farm->Town travel:
  transit corridor, not a primary gameplay area
```

The route through BusStop exists mainly to move Nana from Farm toward Town/Beach
and back. It should be made reliable and smooth, but it should not consume the
same complexity budget as farming/fishing behavior.

## Role Split

Preferred collaboration model:

- Main chat: architecture, planning, live test commands, reviewing logs.
- Secondary coding chat: receives implementation handoff tasks.
- User: runs live tests and sends logs/screenshots.

The main assistant should avoid drifting into heavy coding unless the user explicitly asks.

## Current Working Method

The active Stardew movement work is phase-driven:

```text
live log
-> identify exact source/executor/stop reason
-> write one narrow implementation handoff
-> secondary chat implements and smokes with mocked/no-input paths
-> user runs observer -> dry-run -> live
-> repeat
```

Do not generalize from memory when a fresh log exists. The latest runtime log is
the source of truth for the next phase.

## Phase Sprawl Guard

The current phase-by-phase implementation is allowed as a safety-first way to
learn from live logs, but it should not continue forever as unrelated fragments.

Before creating a new movement phase, check whether the log is really a new
source/action or only a wiring/surfacing gap in an existing runner.

If several sources share the same behavior, prefer moving toward shared policy
helpers while keeping the old gated wrappers at first:

```text
PartialResumePolicy
MediumContinuePolicy
NearTurnContinuePolicy
DoorWarpMicroPolicy
RouteHandoffPolicy
```

Do not remove exact env/operator gates during consolidation. The first safe
step is to make old phase wrappers call shared policy logic, then collapse
duplication only after smokes and live logs prove the behavior.

## Trusted Corridor Policy

Some known corridors should not be treated like unknown mazes or micro-battle
zones. Once live logs and anchor memory prove a corridor is stable, movement
policy should prefer corridor-style held/renewable movement with normal safety
guards rather than opening more tiny phase branches for every local stop reason.

Locked examples:

```text
BusStop Trusted Corridor
Farm->BusStop->Town Trusted Corridor
Farm->Town Direct Transit Leg
```

Known anchors:

```text
Farm.ToBusStopApproach = Farm [79,17], step right -> BusStop [11,23]
BusStop main road      = BusStop [11,23] -> [43,23], mostly D on y=23
BusStop.ToTownApproach = BusStop [43,23], step right -> Town [0,54]
Town.ToBusStopApproach = Town [0,54]
```

Policy intent:

```text
known safe corridor:
  smooth held/renewable lease
  avoid reclassifying every 1-2 tiles into new phase branches
  brake/reobserve near warp anchor

unknown/hazard/micro zone:
  use Movement Controller v2, micro pulses, partial resume, and safety gates
```

This does not remove safety. Corridor policy must still release/hold on real
NPC/blocker, off-route movement, stale bridge, wrong map, menu/dialogue/focus
loss, or any unsafe state.

Architectural rule:

```text
BusStop should be a trusted held corridor,
not a micro/partial phase battlefield.
```

Driver implementation rule:

```text
For trusted Farm->Town corridor progress, the correct implementation shape is
not another BusStop phase. Use the existing held-key route runner/supervisor or
a path-run-style held batch runner:

far clear corridor:
  hold movement key / renewable held batch

within <=2 tiles of warp/door/target/turn for ordinary travel:
  brake and switch to micro/alignment

unsafe/off-route/stale/focus/menu/NPC hazard:
  release_all, reobserve, hold or replan

Do not interpret "within 2 tiles of transition" as permission to run a
warp_step before Nana is actually positioned and the required approval/token is
valid.

If old source constants still use `MICRO_ZONE_TILES=3`, treat that as legacy
behavior to review or override for travel corridors, not the current product
target.
```

Product rule:

```text
When Nana wants to go from Farm to Town, BusStop is just transit.
Do not stop in BusStop to "work" or run local phase logic unless safety fails.
```

Canonical policy wording:

```text
Farm -> BusStop -> Town is a trusted direct transit leg.
BusStop is a transit-only corridor segment, not a work zone.
It is part of the direct Farm->Town transit path and must not introduce
local work behavior during ordinary safe travel.

Under normal safe conditions:
  the current route/map matches the known Farm->Town corridor,
  movement remains on the expected BusStop corridor track toward Town
    (currently y=23),
  movement direction matches the corridor progression (currently D),
  hazard or dynamic blocker is none,
  bridge freshness and focus/menu safety are OK.

Nana should use corridor/held-route style movement and continue directly
toward Town.

The runtime must not introduce local BusStop work phases or
stop-reason-specific movement phases for ordinary safe corridor progress.
```

Expected behavior for Farm->Town:

```text
Farm [79,17] --D--> BusStop [11,23]
BusStop y=23 held corridor --D--> BusStop [43,23]
BusStop [43,23] --D--> Town [0,54]
```

The user expects this to behave like one direct travel leg to Town. Internally
it may release/reobserve at map transitions for safety, but product-level
behavior should not look like Nana is stopping to operate in BusStop.

Implementation status:

```text
FarmToTown trusted corridor is implemented in static travel.
Policy name: FarmToTown
Executor: trusted_corridor_farm_to_town

This is a corridor policy path, not another BusStop movement phase.

Farm-edge transition note:
  Farm [78,17] may need an internal transition-aware nudge to the in-bounds
  warp tile [80,17]. That runner is:
    trusted_corridor_in_bounds_warp_transition
  Expected verification reason:
    in_bounds_warp_tile_transition_verified
  This is still part of the Farm->Town direct transit leg, not a local BusStop
  phase and not a reason to reintroduce step-by-step BusStop work.
```

After the current Town gate fix, do not create more BusStop-only movement
phases for ordinary same-direction `D` progress on y=23 when hazard is none.
Fold Farm->BusStop->Town transit into a corridor policy or future
TravelSession/auto-loop consolidation, then return attention to farming and
fishing.

## Current Focus Slice

As of 2026-06-06, the active slice is not "all movement is almost done".
The active slice is:

```text
Farm/BusStop -> Beach static travel
Movement Controller v2
far-clear leases
near-turn micro pulses
door/warp micro pulses
handoffs after partial progress
```

The total Nana schedule is still large:

- route-loop reentry and partial-resume behavior,
- Beach/Town transition handling,
- final target approach,
- retry/replan policy,
- richer NPC/hazard interruption,
- farming semantics and planner,
- cleanup of telemetry/gate sprawl.

When answering progress questions, distinguish between:

```text
this small route slice is close
the whole Nana movement/farming architecture is not done yet
```
