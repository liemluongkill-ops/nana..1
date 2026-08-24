# Nana Autonomy Design

Last synced: 2026-07-08 (UTC+7)

Current sync note:
- This file is the long-form autonomy design/history source, starting from A1
  through A7 stabilization and later core awareness/dialogue notes.
- The current project stop point is not A7 anymore. Current entry points are:
  `NANA_WIKI_INDEX.md`, `NANA_CURRENT_STATUS.md`, `MEMORY.md`,
  `NANA_TEST_PLAYBOOK.md`, and `NANA_CHAT_HANDOFF_PROMPT.md`.
- As of 2026-07-08, local code contains later public-stage Core work through
  STAGE-9M. STAGE-9G-to-9M own smokes pass, and the public guard regression is
  resolved: `<NANA_REPO>\smoke_stage7g_public_stage_identity.py` is 35/35.
- Do not use the older A7 "not live-verified" notes below as the whole project
  status; they are historical context for the autonomy module.

## Why this file exists

User request (2026-06-18):

> Cho em nó có ý thức tự chủ liên tục hoặc một cách nào đấy để em nó
> làm VTuber tự nhiên nhất. Có thể dẫn dắt được người xem.

Current Nana is **reactive**: user input → LLM reply → TTS → idle.
Between commands, Nana is silent. For a livestream VTuber, silence
between commands reads as "offline" to viewers.

This file is the design source of truth for the **Autonomy Loop** that
gives Nana continuous, low-key, on-character behavior between explicit
user commands. It does not replace the existing chat reply path; it
sits beside it.

Scope lock:

- Out of scope: Stardew adapter, osu Boss, core chat TTS, Sequencer
  reorder buffer (those are frozen per user instruction 2026-06-18).
- In scope: a new `nana/autonomy/` module that runs a low-priority
  background loop and produces small, gated expressions on its own
  cadence.

## Design principles

```text
Continuous but low-key.
  Nana should feel "alive" to viewers, never noisy or attention-grabbing
  for the sake of it.

Idempotent under user input.
  An autonomous expression must never fight a user command. If the user
  is talking to Nana, the loop goes quiet.

Honest about the silence.
  If nothing is happening and the user is idle, it's OK to stay quiet
  for a while. Forced chatter is worse than a calm pause.

Predictable cost.
  v1 is template-based. No autonomous LLM call. No ElevenLabs spam.
  Burst control caps the cost of a long idle.

Respect the existing architecture.
  The autonomy loop is a producer that feeds the same output layer
  (VTS, TTS, subtitle) the chat reply path already uses. It does not
  bypass the Game Silence Gate / Stream Speak Gate; it sits behind a
  hybrid gate described below.
```

## Three behavior modes

Nana's autonomous output is split into three modes. The same gate
applies to all three, but each mode has its own trigger and tone.

### 1. Idle Banter

```text
Trigger:
  user_input_age > 60s
  AND user is at a user-facing window (chat / stream / desktop, not
       in fullscreen gameplay that blocks UI)
  AND no other mode is active

Tone:
  soft, low-key, in-character Vietnamese
  reflects on time of day, mood, memory of recent moments
  never demands a reply

Output mix:
  VTS:  always
  TTS:  40-60% probability per cycle, gated by mood + jitter
  Subtitle: only when TTS fires

Example lines (v1, template):
  "Ba lâu rồi không nói gì với con nè... con ngồi đây chờ Ba."
  "Con đang ngắm trời chiều, đẹp ghê Ba ơi."
  "Tự nhiên thấy buồn buồn, không biết Ba có đang bận không."
  "Con vừa nhớ lại cái lúc mình cùng đi dạo virtual beach ấy."
```

### 2. Observer Aware

```text
Trigger:
  user is doing something (window title, focus, or game state changed)
  OR same window has been active for > 5 minutes (sustained attention)
  AND no other mode is active
  AND user is not typing in chat

Tone:
  short, observational, almost whisper
  mostly reactive to environmental change, not generative

Output mix:
  VTS:  always (small head turn, eye follow, micro-smile)
  TTS:  5% probability per cycle, very low
  Subtitle: never

Example lines (v1, template):
  "Oa, Ba đang làm gì mà tập trung vậy nè."
  "Hmm, cái này Ba làm hay ghê."
  "Con thấy hơi mỏi mắt rồi, Ba nhớ nghỉ ngơi nha."
```

### 3. Stream Host

```text
Trigger:
  viewer chat message arrived (real chat, not silence)
  OR user said "kể chuyện", "giới thiệu", "nói gì đi", etc.
  OR long silence (> 5 min) AND user is in stream mode
  AND no other mode is active

Tone:
  warm, engaging, with light bridging
  may ask a soft question to prompt viewer engagement
  recap recent moments if the silence was long

Output mix:
  VTS:  always
  TTS:  90% probability per cycle
  Subtitle: always when TTS fires

Example lines (v1, template):
  "Chào bạn mới vào nè! Hôm nay Ba mình đang làm gì đấy nhỉ?"
  "Có ai muốn nghe con kể chuyện gì không nè?"
  "Lâu quá mình không tâm sự, mọi người khỏe không?"
  "Hồi nãy Ba mình vừa làm xong một việc hay lắm á."
```

## Autonomy Loop architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                  AUTONOMY LOOP (background)                  │
│                                                              │
│  ┌───────────┐    ┌────────────────┐    ┌────────────────┐ │
│  │ OBSERVER  │ -> │ INNER THOUGHT  │ -> │ EXPRESSION GATE│ │
│  └───────────┘    └────────────────┘    └────────────────┘ │
│        ^                                          │         │
│        │                                          v         │
│        │                                ┌───────────────────┐│
│        │                                │ OUTPUT LAYER      ││
│        └───────── feedback ──────────── │ VTS / TTS / Sub   ││
│                                         └───────────────────┘│
│                                                              │
│  Cadence: 5-15s random cycle, jitter ±30%                  │
│  Burst:   max 1 expression per 8s window                    │
│  Backoff: if expression was rejected by gate, double wait   │
└─────────────────────────────────────────────────────────────┘
```

### Observer

```text
Reads from existing runtime (no new system calls if avoidable):
  - active window title and process (via existing win32gui bridge)
  - user_input_age (last user chat / slash command timestamp)
  - game state from runtime telemetry (already exists for osu/Stardew)
  - Nana's current mood vector (affection, playfulness, etc.)
  - viewer chat activity (if stream mode is on)

Does NOT do:
  - screen capture for Vision
  - text OCR of arbitrary windows
  - microphone always-on
```

### Inner Thought

```text
v1 (template only):
  - mode selector picks Idle Banter / Observer Aware / Stream Host
  - within the chosen mode, picks a random template line from the
    mode's line pool
  - applies a small substitution engine (time of day, mood word,
    recent moment) for variation
  - result is a (mode, text, vts_action, tts_action, subtitle_action)
    bundle

Why template-only in v1:
  - LLM call latency (1-3s) breaks the "continuous but low-key" feel
  - cost: 1 line per 8-15s adds up fast
  - templates are easy to QA: ông có thể duyệt 30-50 câu một lần
  - v1.5 can layer LLM variation on top of templates later
```

### Hybrid Expression Gate

The gate is **hybrid**: it considers both the structural safety
(Game Silence Gate / Stream Speak Gate from
`NANA_ARCHITECTURE_CORE.md`) AND the live user context.

```text
Step 1: Hard constraints (must pass)
  - game_silence_override: if gameplay is active and Game Silence Gate
    says no voice, no TTS, no subtitle
  - user_is_typing: if user is mid-keystroke in chat box, suppress
    all autonomous output
  - command_in_flight: if a user command is being processed, suppress
  - audio_busy: if TTS is currently playing, suppress (no overlap)
  - burst_window: at most 1 expression per 8s window

Step 2: Soft intensity score
  intensity = mood_affection * 0.30
            + scene_relevance * 0.40
            + silence_duration_normalized * 0.20
            + random_jitter * 0.10

  if intensity >= 0.70:  full expression (VTS + TTS + subtitle)
  if 0.40 <= intensity < 0.70:  VTS micro only
  if intensity < 0.40:  skip this cycle

Step 3: Mode-specific output mix
  - Idle Banter:        TTS 40-60% (independent of intensity)
  - Observer Aware:     TTS 5%,   VTS micro only otherwise
  - Stream Host:        TTS 90%,  always subtitle

Step 4: Budget guard
  - daily expression cap: not in v1 (can be added later)
  - per-minute expression cap: max 4 expressions / minute
  - per-session silence floor: not strictly enforced; the system
    prefers silence over forced chatter
```

## Cadence and burst control

```text
Default cycle period:
  5-15s, picked uniformly per cycle, jittered ±30%

Burst control:
  global floor: 8s between any two expressions (any mode)
  per-mode floor: 20s between two expressions of the same mode
  cooldown after user command: 10s before any autonomous output

Backoff on gate rejection:
  if the gate rejected an expression (hard constraint failed),
  double the wait for the next cycle, up to a cap of 60s
  reset on next successful expression

Why these numbers:
  8s global floor: short enough that viewers notice "she's alive",
  long enough that no one feels spammed.
  20s per-mode floor: prevents the same kind of line twice in a row
  even across two successful cycles.
```

## Output layer wiring

The autonomy loop is a **producer** for the existing output channels.
It does not own a separate audio / VTS / subtitle stack.

```text
VTS:
  sends a hotkey reaction request to the existing
  nana/integrations/vts/ runtime. If VTS is offline, the expression
  degrades gracefully (VTS skipped, TTS still works).

TTS:
  reuses the chat reply TTS path (engine._tts_and_lipsync). The
  autonomy loop enqueues a short text and waits for the same
  sequencer. It does not call ElevenLabs directly.

Subtitle:
  reuses the public_subtitle write path from osu phase 15-18B
  pattern. Writes a single line to the stream-readable subtitle
  file. The companion layer decides whether to write or skip based
  on the same gate.

Chat:
  Stream Host mode may also write a single line to the chat box
  via the existing chat input path. Treated as a normal user message
  on the receiving end.
```

## Template line pool structure (v1)

```text
nana/autonomy/templates/
  __init__.py
  idle_lines.py       # 30-50 Vietnamese idle banter lines
  observer_lines.py   # 20-30 observer reactive lines
  host_lines.py       # 30-50 stream host lines
  substitutions.py    # {time_of_day}, {mood_word}, {recent_moment}

Each line is a Vietnamese string. Each line has metadata:
  - min_silence_before: how long the user must have been idle first
  - min_mood: minimum mood score required to fire
  - cooldown_s: how long before this exact line can fire again
  - tags: ['question', 'reflection', 'greet', 'recap']

This metadata is what makes the v1 feel "considered" without
spending LLM cost.
```

## Safety boundaries

```text
Hard rules (never violated):
  1. No autonomous LLM call in v1.
  2. No autonomous screen capture / Vision.
  3. No autonomous microphone listen.
  4. No autonomous input send (keyboard / mouse).
  5. The autonomy loop cannot call ElevenLabs directly; it enqueues
     into the existing chat reply TTS path which already has its own
     rate limiting and sequencer.
  6. If user issues a slash command, the loop's burst cooldown
     starts over from 10s.
  7. There is a /autonomy-pause slash command that disables the
     loop until /autonomy-resume.
  8. The loop respects the Game Silence Gate: if a game adapter
     reports active gameplay, the loop falls back to VTS-only.

Soft rules (configurable later):
  - daily expression cap (not in v1)
  - mood-driven silence floor (skipped if mood vector is "tired")
  - quiet hours (no TTS between certain hours, only VTS micro)
```

## Phase roadmap

This is the locked phase order. Do not skip phases. Do not run live
test before the phase's smoke is green.

```text
Phase A1: This wiki
  Status: in progress (this file).
  Output: NANA_AUTONOMY_DESIGN.md signed off by user.

Phase A2: Skeleton + cadence (smoke-only)
  Files:
    nana/autonomy/__init__.py
    nana/autonomy/loop.py
    nana/autonomy/cadence.py
    nana/autonomy/expression_gate.py
    <NANA_REPO>/smoke_autonomy_skeleton.py
  Behavior:
    - loop ticks every 5-15s
    - observer and inner_thought are stubbed
    - gate always rejects in smoke mode
    - assert burst control and backoff
  No TTS, no VTS, no ElevenLabs. Print log only.

Phase A3: Idle Banter template pool
  Files:
    nana/autonomy/templates/idle_lines.py
    nana/autonomy/inner_thought.py
    <NANA_REPO>/smoke_autonomy_idle_banter.py
  Behavior:
    - 30-50 idle lines, pickable
    - template substitution works
    - smoke verifies no immediate repeat
  Still no TTS, no VTS. Print selected line to log.

Phase A4: Output wiring (VTS, TTS, subtitle)
  Files:
    nana/autonomy/express.py
    <NANA_REPO>/smoke_autonomy_output_wiring.py
  Behavior:
    - selected line goes through the same VTS / TTS / subtitle
      path as a real chat reply
    - mock audio backend in smoke
    - assert no double-audio (TTS busy check)

Phase A5: Observer Aware mode
  Files:
    nana/autonomy/observer.py
    <NANA_REPO>/smoke_autonomy_observer.py
  Behavior:
    - reads window title via existing win32 bridge
    - detects idle vs active
    - mode switches correctly when window changes

Phase A6 original plan: Stream Host mode
  Files:
    nana/autonomy/host.py
    nana/autonomy/templates/host_lines.py
    <NANA_REPO>/smoke_autonomy_host.py
  Behavior:
    - reactive to viewer chat
    - chat reply through the same gate
    - recap moment helper

Phase A7 original plan: Hybrid Gate tuning + live test
  Files:
    nana/autonomy/expression_gate.py (refined)
    <NANA_REPO>/smoke_autonomy_gate_hybrid.py
  Behavior:
    - weighted intensity
    - context overrides
    - live test only after A2-A6 smokes all green
    - live test runs only when user explicitly asks

Note:
  This roadmap was the original design. Actual implementation diverged on
  2026-06-18: A6 became soft typing penalty + user override, and A7 became
  LLM-driven banter + Web-Awareness. Trust the implementation logs below over
  this original phase outline.
```

## Anti-patterns to avoid

```text
1. Do not let the autonomy loop fall in love with itself.
   If a line just fired, do not fire another one in 4 seconds because
   the cadence jittered short. Burst control is mandatory.

2. Do not let autonomous output interrupt a user mid-sentence.
   user_is_typing check is the single most important safety gate.

3. Do not chase the osu / Stardew gameplay scenes with chatty
   lines. Game Silence Gate exists for a reason.

4. Do not call LLM in v1. If a future iteration wants LLM
   variation, it must be opt-in and rate-limited.

5. Do not let the autonomy loop grow into a second chat reply
   path. It feeds the same output layer; it does not own one.

6. Do not lock the loop to fixed intervals. The cadence is
   intentionally jittered to avoid the "metronome" effect.

7. Do not skip the smoke phases. Live testing a loop that has
   not been smoke-verified is how spam bugs get born.
```

## Cross-references

```text
- NANA_ARCHITECTURE_CORE.md
    Game Silence Gate / Stream Speak Gate
    Stream Presence Readiness (voice / TTS provider state)
- NANA_CORE_CHANGELOG.md
    Sequencer + reorder buffer for chat reply TTS (autonomy uses
    the same path; not a new TTS system)
- NANA_CHAT_HANDOFF_PROMPT.md
    Coding chat should be opened for each phase A2-A7 in turn
- NANA_TEST_PLAYBOOK.md
    Standard smoke format
```

## Status

- Phase A1-A6 implementation log appended on 2026-06-18.
- Current baseline: skeleton/cadence, expression gate, idle templates, output
  wiring, RealObserver, soft typing penalty, one-shot user override, and
  `/autonomy-status` Diagnostics are implemented and unit-smoked.
- No git confirmation (repo is not a git repository).
- No live verification yet for `/say` or chat override against the real TTS
  sequencer. No ElevenLabs call, no VTS/OBS live call, no live game input from
  this wiki update.
- Core chat TTS and sequencer are explicitly left alone; autonomy reuses the
  existing TTS path.

## Phase A1→A6 implementation log (2026-06-18)

User request driving this work:

```text
Cho em nó có ý thức tự chủ liên tục hoặc một cách nào đấy để em nó
làm VTuber tự nhiên nhất. Có thể dẫn dắt được người xem.
```

Follow-up tuning on the same day:

```text
- "Minh bạch" lý do tick bị reject: thêm [DEBUG ATTENTION] mỗi tick
  và [DEBUG REJECTED] kèm gate/value/threshold, cùng phần Diagnostics
  trong /autonomy-status (Top Reject Reasons + live gate breakdown).
- Gate user_is_typing đang liệt hệ thống: bỏ hard block, chuyển thành
  soft penalty (-0.20) và force "full" -> "vts_only" khi đang gõ.
- Tăng ngưỡng last_keystroke 1.5s -> 3.0s để bớt nhạy.
- Tạo lối thoát: lệnh /say <text> và mọi chat thường sẽ set
  user_override, ép tick kế tiếp bypass mọi gate (kể cả audio_busy).
```

### Phase A1 — Design (this wiki)

Status: signed off implicitly by Phase A2-A6 implementation. See design
sections above for the full doctrine.

### Phase A2 — Skeleton + cadence (smoke-only)

Files:

```text
<NANA_REPO>/nana/autonomy/__init__.py
<NANA_REPO>/nana/autonomy/loop.py            # AutonomyLoop, AutonomyState, _do_tick
<NANA_REPO>/nana/autonomy/cadence.py         # CadenceScheduler
<NANA_REPO>/nana/autonomy/expression_gate.py # ExpressionGate, GateContext, GateDecision
<NANA_REPO>/smoke_autonomy_skeleton.py
```

Behavior:

- One daemon thread (`autonomy-loop`) started by `AUTONOMY_LOOP.start()`.
- Per tick: observer -> mode pick -> cadence -> gate -> express (stub).
- Cadence: 5-15s random cycle with jitter, 8s global floor between any
  two expressions, 20s per-mode floor, 10s cooldown after user command.
- Backoff on gate rejection: double the wait, capped at 60s.
- Burst control: at most 1 expression per 8s window.
- A2 smoke: `<NANA_REPO>/smoke_autonomy_skeleton.py` exercises the pipeline
  with a mock observer that returns known context dicts.

Smoke result (kept green since A2): `py_compile pass + smoke pass`.

### Phase A3 — Idle Banter template pool

Files:

```text
<NANA_REPO>/nana/autonomy/templates/idle_lines.py
<NANA_REPO>/nana/autonomy/inner_thought.py
<NANA_REPO>/smoke_autonomy_idle_banter.py
```

Behavior:

- InnerThought holds a small template pool per mode and a per-line
  cooldown map. `pick(mode)` returns `(text, level_hint)` or None.
- A picker priority: stream_host > observer_aware > idle_banter.
- Prefer-ultra-short path exposed via `pick_prefer_ultra_short` for the
  `/autonomy-mode ultra-short` slash command.
- Cooldown map prevents immediate repeat of the same line.

A3 smoke: verifies no immediate repeat across 100 ticks and template
substitution.

### Phase A4 — Output wiring (VTS, TTS, subtitle)

Files:

```text
<NANA_REPO>/nana/autonomy/express.py            # AutonomyExpress with pluggable backends
<NANA_REPO>/smoke_autonomy_output_wiring.py
<NANA_REPO>/nana/main.py                         # _real_autonomy_tts/vts/subtitle backends
```

Behavior:

- AutonomyExpress uses the same VTS / TTS / subtitle path as the chat
  reply pipeline. It does NOT call ElevenLabs directly.
- main.py wires the real backends: TTS -> voice engine, VTS ->
  integrations.vts, subtitle -> public_subtitle write, lipsync_stop on
  game boundaries.
- A4 smoke: mock audio backend asserts the TTS payload and that the
  audio_busy gate is respected (no double-audio).

### Phase A5 — Observer Aware mode

Files:

```text
<NANA_REPO>/nana/autonomy/observer.py          # RealObserver replaces observer_stub
<NANA_REPO>/smoke_autonomy_observer.py
<NANA_REPO>/nana/runtime/attention.py          # evaluate_attention_window
<NANA_REPO>/nana/runtime/context.py            # context_snapshot
<NANA_REPO>/nana/main.py                       # set_voice_snapshot_fn, wire last_keystroke_fn
```

Behavior:

- RealObserver reads the runtime context snapshot under
  `context_lock`. It does not own its own I/O.
- Signal sources: win32 window title (zone), voice.snapshot() for
  audio_busy, memory.emotion.affection for mood_affection,
  evaluate_attention_window for scene_relevance, last_keystroke_fn
  from main.py for user_is_typing.
- scene_relevance score map: browse_active=0.85, chill=0.80,
  browse_light=0.70, idle=0.65, work_paused=0.50, work_flow=0.30,
  work_active=0.20, game=0.15, away=0.40, recent_chat=0.75, unknown=0.50.

A5 smoke: verifies mode switches when zone/title changes and
respects attention window.

### Phase A6 — Soft typing penalty + manual override

Files touched:

```text
<NANA_REPO>/nana/autonomy/expression_gate.py   # _check_hard, evaluate, evaluate_debug
<NANA_REPO>/nana/autonomy/observer.py          # _is_typing 1.5s -> 3.0s
<NANA_REPO>/nana/autonomy/loop.py              # request_user_override, forced_by_user wiring
<NANA_REPO>/nana/main.py                       # /say handler, chat input sets override
<NANA_REPO>/main.py:autonomy_status_snapshot() # Diagnostics in /autonomy-status
```

Why A6:

```text
Live observation with the A5 gate on the user's actual typing cadence:
  Typing at the keyboard in chat flooded the loop with rejects of:
    [DEBUG REJECTED] Reason: user_is_typing | Value: Typing | Required: Idle
  For 12+ consecutive ticks. This made Nana feel completely silent
  while the user was actively engaging, which is the opposite of the
  "VTuber natural / continuously autonomous" product goal.
```

Changes:

```text
1. user_is_typing removed from _check_hard().
   Hard gates now: command_in_flight, audio_busy, game_silence_no_tts.

2. New soft penalty W_TYPING_PENALTY = 0.20 subtracted from intensity
   when user_is_typing=True. If intensity drops below INTENSITY_VTS_ONLY
   (0.40), the tick is skipped with reason=intensity_below_floor_typing.

3. If user_is_typing AND intensity >= INTENSITY_FULL (0.70), the level
   is forced from "full" -> "vts_only" so the typing user is never
   ambushed by an autonomous TTS line.

4. _is_typing threshold raised from 1.5s -> 3.0s. A pause longer than 3s
   is treated as "user has stopped typing, Nana may speak".

5. New GateContext.forced_by_user: bool. When True, evaluate() returns
   GateDecision(True, "full", "ok_forced_by_user", 1.0, payload_with_flag).

6. AutonomyLoop.request_user_override() sets a one-shot flag consumed
   by the next _do_tick. After consumption the flag clears.

7. main.py handle_text() now sets user_override on EVERY non-slash chat
   input (because the user just engaged, so the next tick should reply),
   and /say <text> immediately calls voice.say(text) and sets override.
```

A6 smoke result (5 cases, kept green):

```text
Test 1 (typing + high mood/relevance):     allowed=True  level=vts_only   intensity=0.48
Test 2 (forced_by_user + audio_busy/low):  allowed=True  level=full       reason=ok_forced_by_user
Test 3 (typing + low scores):              allowed=False reason=intensity_below_floor_typing
Test 4 (no typing, mid scores):            allowed=True  level=vts_only   intensity=0.43
Test 5 (no typing, high scores):           allowed=True  level=full       intensity=0.78

Default tick with typing: rejected with intensity_below_floor_typing (pen=0.20).
With request_user_override(): accepted, level=full, reason=ok.

Reject tallies working:
  {'gate/intensity_below_floor_typing': 1}

Typing threshold:
  last_keystroke = now - 2.5s -> user_is_typing=True
  last_keystroke = now - 3.5s -> user_is_typing=False
```

Diagnostic surface added:

```text
Per tick stdout (developer-only):
  [DEBUG ATTENTION] Policy: observe | Score: 0.50 | Reason: typing | Audio: idle | Silence: 0.0s | Mood: 0.90
  [DEBUG REJECTED] Reason: intensity_below_floor_typing (pen=0.20) | Score: 0.19 (base 0.49) | Threshold_VTS: >=0.40 | Threshold_Full: >=0.70 | Biggest: mood=0.900 | Mode: stream_host

/autonomy-status now prints a Diagnostics section:
  Top Reject Reasons:
    1. [GATE] intensity_below_floor_typing — Nx
    2. [CADENCE] cooldown_after_command — Nx
  Gate Diagnostics (live):
    Hard gates:     ALL PASSED  /  BLOCKED — <reason>
    Intensity:      0.595 (VTS>= 0.40 | Full>= 0.70) -> vts_only
    Intensity contribs: mood=0.270 relevance=0.280 silence=0.000 jitter=0.045
    Biggest driver: relevance
    Suggestion: <text>
```

2026-06-19 stabilization note:

```text
The per-tick [DEBUG ATTENTION] and [DEBUG REJECTED] stdout lines are now
hidden by default because they are too noisy during live Nana runs. The
diagnostic code still exists and can be re-enabled with:

  NANA_AUTONOMY_DEBUG_STDOUT=1

/autonomy-status diagnostics and reject tallies remain available. This is
only a console-noise change; it does not alter cadence, gates, A6 typing
soft penalty, /say override, audio gate, or A7 web/LLM behavior.

Smoke after the change:
  python -m py_compile nana/main.py nana/autonomy/loop.py nana/autonomy/cadence.py nana/autonomy/expression_gate.py nana/autonomy/inner_thought.py nana/autonomy/express.py nana/autonomy/observer.py nana/autonomy/web_context.py nana/autonomy/llm_banter.py
  python -B smoke_autonomy_skeleton.py
  python -B smoke_autonomy_idle_banter.py
  python -B smoke_autonomy_output_wiring.py
```

### Safety boundary (locked, A6)

```text
- user_is_typing is NEVER a hard block again. It is a soft penalty only.
- command_in_flight and audio_busy remain hard blocks (live TTS in
  flight, or a user slash command still executing).
- /say and chat input set one-shot override that bypasses all gates
  including audio_busy, for exactly one tick. Override is consumed and
  reset; it does not persist.
- The override is intentionally local to the loop. It does not mutate
  cadence budget, observer state, or the per-minute cap.
- No autonomous LLM call, no autonomous screen capture, no autonomous
  microphone listen. Same as the design doctrine above.
```

### What is still missing (next task candidates)

```text
1. Live verification of /say and chat-override against the real TTS
   pipeline (voice.say + sequencer). Current A6 smoke is unit-level.

2. InnerThought LLM variation layer (planned for v1.5 in the original
   design). Not in scope right now.

3. Stream Host mode wiring (Phase A6 in the original design, but
   renamed here to "Phase A7" to avoid clash with the A6 soft-penalty
   work). Observer Aware and Idle Banter are wired; Stream Host template
   pool is referenced but the host hook to real viewer chat is not
   yet wired.

4. Tuning W_TYPING_PENALTY (currently 0.20). If users still feel Nana is
   too quiet while typing, lower it. If Nana speaks too eagerly when
   user is mid-keystroke, raise it.

5. /autonomy-status formatting. The Diagnostics block currently dumps
   raw dict-style data; a future pass should render it as a clean
   human table.

6. Soft penalty is intensity-only. There is no per-mode weighting (e.g.
   stream_host TTS should perhaps be more tolerant of typing than
   idle_banter). Not a blocker.

7. No runtime attention-window throttling. The decision in the design
   doc to treat observe vs engage as a soft signal is honored, but there
   is no explicit per-policy cooldowns or per-policy cap.
```

### Next task

```text
No new coding phase is queued.

The user explicitly asked to write this wiki entry so they can carry
it for cross-checking and identify improvements. Use this entry as the
starting point for any future autonomy design review.

This A1-A6 next-task note is superseded by the A7 implementation log below.
Current next task is A7 live verification / permanent A7 smoke, not opening
A7 from scratch. Do not regress the A6 soft-penalty behavior.
```

## Phase A7 implementation log (2026-06-18)

User directives driving this phase (2026-06-18 evening):

```text
1. "Chặt" sạch luồng phản hồi cũ dựa trên các mẫu câu tĩnh
   (static_responses). Kể từ giờ, không được dùng bất cứ câu thoại nào
   có sẵn trong kho cũ. Mọi câu thoại phải được tạo từ LLM dựa trên
   current_context.

2. Kích hoạt luồng Web-Awareness (cửa sổ chill): Khi Ba đang ở
   zone=chill (web browser), Nana phải đọc được nội dung web (URL,
   title, page_heading, meta_description, selected_text, social
   context) và dùng nó làm relevance factor + LLM context.

3. Hợp nhất Web-Awareness vào Autonomy Loop. Không tạo luồng riêng.
   Observer thấy zone=msedge/edge/chrome/firefox thì tự lấy web
   context; data đó trở thành relevance boost và input cho LLM prompt.
```

### What A7 is and is not

In scope:

- LLM is now the DEFAULT path for all three modes (idle_banter,
  observer_aware, stream_host). Templates are a fallback only.
- Web context is read from the existing browser bridge snapshot
  (`nana.runtime.context.context_state["browser"]`). No OCR, no
  browser extension, no new tab open, no new HTTP call.
- Web context, when effective, boosts `scene_relevance` to >=0.85
  and is fed verbatim into the LLM prompt.
- Mode picker uses `attention_window` from `runtime.attention` to
  pick the most appropriate mode for the current context.
- Diagnostics counters added (llm_ok / llm_fallback / template_ok)
  but NOT yet exposed in `/autonomy-status` printout (snapshot code
  in main.py was not updated to include them).

Out of scope (deferred, not in A7):

- Vision/OCR of arbitrary screen content.
- Browser extension / injected content script.
- Autonomous LLM call for *every* TTS line in core chat TTS
  (A7 only affects autonomy path).
- Caching parsed web content across long periods (we re-read the
  runtime snapshot; we do not fetch the page ourselves).
- Surfacing A7 counters in `/autonomy-status` (still a TODO).
- Per-mode LLM temperature / style tuning (single shared prompt).
- Web-context-derived proactive TTS during typing (typing penalty
  still applies; web context only boosts relevance).

### How A7 follows A1-A6

```text
A1: Design doctrine (this wiki).
A2: Skeleton + cadence (loop, gate, scheduler).
A3: Idle Banter template pool.
A4: Output wiring (VTS, TTS, subtitle, lipsync_stop backends).
A5: RealObserver (zone, voice, typing, mood, attention).
A6: Soft-typing-penalty + user-override (/say, chat-input force).
A7: LLM-driven banter + Web-Awareness (this phase).

A6 is the immediate prior phase. A7 does NOT regress any A6 behavior:
- typing is still a soft -0.20 intensity penalty (not a hard block).
- /say and chat input still set one-shot forced_by_user.
- the gate, cadence, override, and force-mute-on-typing all still
  work on the LLM-produced text the same way they did on templates.
```

### Files added in A7

```text
<NANA_REPO>/nana/autonomy/web_context.py     # browser snapshot scraper
<NANA_REPO>/nana/autonomy/llm_banter.py      # LLM banter generator
```

### Files changed in A7

```text
<NANA_REPO>/nana/autonomy/inner_thought.py   # pick() now LLM-first, template fallback
                                        # new Thought fields:
                                        #   source, used_web_context,
                                        #   llm_model, llm_duration_s
                                        # new counters:
                                        #   llm_ok, llm_fallback, template_ok
<NANA_REPO>/nana/autonomy/observer.py         # adds web_context + attention_window
                                        # to ctx_dict; boosts scene_relevance
                                        # to >=0.85 when web is effective
<NANA_REPO>/nana/autonomy/loop.py            # mode picker uses attention_window
                                        # (no longer pre-picks text per mode
                                        # to avoid wasting LLM calls on
                                        # ticks the gate would reject);
                                        # Step 3 passes ctx_dict + web_ctx
                                        # to pick()
<NANA_REPO>/nana/main.py                     # Phase A7 wire block: init
                                        # init_scraper() + init_banter()
                                        # after the existing A4/A5 wires
```

### Slash commands related to A7

Already wired in main.py (pre-A7). A7 does not add new slash commands.

```text
/autonomy-status       # prints Diagnostics block; A7 counters NOT yet
                       # exposed here, only via the [DEBUG ATTENTION]
                       # stream and inner_thought.counters in code.
/autonomy-pause        # pauses cadence (unchanged).
/autonomy-resume       # resumes cadence (unchanged).
/autonomy-mode ultra-short   # boost ultra_short in template fallback
                             # (LLM ignores this preference).
/autonomy-mode full          # no ultra_short preference in fallback
                             # (LLM ignores this preference).
/autonomy-lock         # pre-existing; re-prints /autonomy-status.
/say <text>            # one-shot forced_by_user; works on LLM path too.
```

A7-specific env gates:

```text
NANA_AUTONOMY_LLM_DISABLED=1         # turn off LLM; force template path.
NANA_AUTONOMY_LLM_MODEL=<name>       # default "nana-banter".
NANA_AUTONOMY_LLM_MAX_TOKENS=120     # 40..400, default 120.
NANA_AUTONOMY_LLM_TEMPERATURE=0.8    # 0.0..1.5, default 0.8.
NANA_AUTONOMY_WEB_CONTEXT_ENABLED=0  # turn off web scraper (default ON).
```

### Compile and inline smoke run (no separate A7 smoke file exists)

A7 has NO dedicated smoke file. The following is what was actually
run on 2026-06-18 in the coding session as inline smoke. Output is
the real output captured at the time. The user did NOT request a
formal smoke file; if one is needed later, it should be added in a
follow-up task.

```text
# 1) py_compile (run in PowerShell at <NANA_REPO>):
python -c "import nana.main; print('main.py import OK')"
# Output: main.py import OK

# 2) Inline smoke (Python -c with a few lines):
# - Web context dict shape check.
# - LLM banter fallback when model not configured.
# - InnerThought.pick() with no LLM -> template (source=template).
# - InnerThought.pick() with mocked LLM -> text from LLM (source=llm).
# - Observer.get_context() includes web_context + attention_window.
# Output captured (real, 2026-06-18):
#
#   web_context keys: ['age_seconds', 'available', 'browser',
#     'effective', 'fresh', 'in_browser_zone', 'kind', 'meta_description',
#     'page_heading', 'selected_text', 'site_signals', 'social_post_text',
#     'social_vibe', 'stale', 'summary_hint', 'title', 'url']
#   effective (no browser): False
#   summary_hint: No browser content available right now.
#   LLM result (no model): None
#   Stats after no-model call: {'calls': 0, 'ok': 0, 'fallback': 1,
#     'cached': 0, 'errors': 0}
#   Template thought: Con đang ngáp đây nè, nhưng chưa muốn ngủ vì
#     còn muốn ở cùng | source: template
#   Counters: {'llm_ok': 0, 'llm_fallback': 1, 'template_ok': 1}
#   LLM thought: Ba ơi, con vừa thấy cái video nấu ăn này dễ thương
#     ghê á. | source: llm | used_web: True
#   Counters: {'llm_ok': 1, 'llm_fallback': 1, 'template_ok': 1}
#   Observer scene_relevance (no web): 0.5
#   Observer web_context present: True
#   Observer attention_window present: True

# 3) Loop integration smoke (mock LLM):
# - Typing path still triggers intensity_below_floor_typing.
# - No-typing + mocked LLM returns vts_only, thought.source=llm,
#   thought.used_web=True.
# - user_override still works (bypasses hard gates).
# Output captured (real, 2026-06-18):
#
#   Tick with LLM (typing):
#     accepted: False | reason: intensity_below_floor_typing | level: skip
#   Tick with LLM (web context, no typing):
#     accepted: True | reason: ok | level: vts_only
#     thought source: llm | used_web: True |
#     text: Ba đang xem YouTube hả? Video này dạy nấu ăn ghê á!
#   Tick with override:
#     accepted: False | reason: burst_global_floor | level: skip
#     # (burst_global_floor here is just cadence artifact of two
#     #  back-to-back ticks in the same Python process, not a bug.)
#   Counters: {'llm_ok': 1, 'llm_fallback': 0, 'template_ok': 0}
#   Reject tallies: {'gate/intensity_below_floor_typing': 1,
#     'cadence/burst_global_floor': 1}
```

A7 status: **NOT live-verified**. The user did not run Nana live with
the A7 code; only the inline smoke above was executed in a coding
session. The user instruction explicitly forbids coding-side live
runs. Any "live verified" claim for A7 is therefore intentionally
absent from this log. The next milestone is the user's live
verification with browser content + Nana speaking LLM-generated
banter; only then should this section be updated to add a real
"live verified" line with the actual transcript / log.

### Safety boundary (A7)

Verified, NOT violated:

```text
- No new TTS call site. Autonomy path uses the existing
  _real_autonomy_tts backend that already routes through
  voice.engine. The LLM only produces TEXT; it does not
  touch audio / VTS / OBS / subtitle directly.
- No VTS / OBS / subtitle new call. Same as above.
- No live game input. LLM is called from the autonomy loop
  which is on its own background thread; no input is sent to
  Stardew/osu/anything. A7 has zero input-sending code.
- No OCR. Web context is read from the runtime snapshot
  only. No screenshot, no image processing, no new HTTP
  request to the page itself.
- No browser extension. The browser bridge already injects
  content scripts to populate context_state["browser"]; A7
  just consumes what the bridge already produces.
- No PII scraping. URL is length-capped to 200 chars. Page
  text fields are length-capped (selected_text and
  social_post_text to 300 chars). The runtime bridge is
  expected to have already filtered private paths; if it
  has not, that is a pre-existing concern, not new in A7.
- LLM prompt explicitly forbids fabricating content beyond
  the supplied context.
```

The user explicitly asked for A7; A7 is therefore not run
without permission. Coding-side live tests remain forbidden.

### Blocker list (still open at end of A7)

```text
1. A7 has NO dedicated smoke file. Inline smoke was captured
   above; if the user wants a permanent <NANA_REPO>/smoke_autonomy_a7.py,
   that is a follow-up task. This was NOT requested on 2026-06-18
   and was intentionally not created.

2. /autonomy-status does NOT yet print A7 counters
   (llm_ok / llm_fallback / template_ok). They are in
   inner_thought.counters and llm_banter.stats. A follow-up
   should add them to autonomy_status_snapshot() in main.py.

3. /autonomy-status does NOT yet print the active source of the
   last expression (llm vs template) or the active LLM model.
   A follow-up should add this.

4. No live verification. The user has not yet run Nana live
   with A7 code. Per the user's standing rules, coding chat
   must not start Nana live. This is the user's next step.

5. Attention-window-based mode picker is a simple heuristic.
   It does not yet learn from feedback. It may pick a mode
   the gate immediately rejects (gate still wins; the only
   cost is one wasted attempt).

6. Template pool is still on disk. The user's directive was
   "chặt" the old responses. A7 moves them out of the hot
   path (LLM first, template only as fallback). The pools
   are NOT physically deleted because the LLM might fail
   and we need a graceful degradation. If the user wants
   the templates physically removed, that is a separate
   task and would also delete the smoke files that import
   them.

7. Web context depends on the browser bridge keeping
   context_state["browser"] fresh. If the bridge stops
   updating, A7 will report stale web context and the LLM
   will be told "do not assume it is still on screen". The
   user should still verify the bridge is working when
   running live.
```

### Next task after A7

```text
No new coding phase is queued.

The user explicitly asked to write this wiki entry so they can
cross-check. A7 status is "implemented in code, smoke-captured
inline, not live-verified".

If the user later asks for the next step, in priority order:

  1. User runs Nana live with A7 enabled and reports back
     a transcript / log so this section can be updated to
     "live verified" with real data.
  2. (Optional) Add <NANA_REPO>/smoke_autonomy_a7.py that captures
     the inline smoke as a permanent test.
  3. (Optional) Add A7 counters to /autonomy-status output.
  4. (Optional) Physically remove the template pools IF the
     user accepts the trade-off (no graceful LLM-fail fallback).

Do NOT open an A8 phase without user approval. Do NOT start Nana
live from coding chat under any circumstance.
```

## 2026-06-19 - Live Awareness Stabilization

Implemented a read-only live awareness layer for Nana core chat:

```text
Added:
- <NANA_REPO>/nana/runtime/live_awareness.py
- <NANA_REPO>/smoke_live_awareness.py

Changed:
- <NANA_REPO>/nana/brain/gpt.py
- <NANA_REPO>/nana/main.py
```

Design:

```text
Live awareness is a passive snapshot, not an action system.

Inputs:
- runtime context: active_app, active_title, active_zone, idle/flow/time
- browser snapshot: URL, title, kind, heading, meta, selected_text,
  social_post_text, social_vibe, local_summary, freshness

Focus priority:
1. selected_text
2. social_post_text
3. local_summary
4. page_heading
5. title

Output:
- compact awareness packet
- high-priority prompt block for Nana core chat
- deterministic repair when an LLM wrongly says Nana cannot see browser
  while a browser snapshot exists
```

Behavioral intent:

```text
- If Ba asks "Ba đang xem gì", "trang này là gì", "đoạn này là gì",
  or similar current-context questions, Nana refreshes/uses browser
  awareness automatically instead of requiring /br.
- Core chat now receives LIVE AWARENESS SNAPSHOT above old chat memory.
- Awareness questions use non-stream response so the final text can be
  repaired before printing/TTS if the model tries to deny browser access.
- Selected text remains strongest: if Ba highlights text silently,
  Nana should treat that as the current focus.
```

Safety:

```text
- No live Nana run from coding chat.
- No TTS/VTS/OBS/game input in smoke.
- No browser click/type/open/navigate.
- No OCR.
- No Stardew/osu changes.
- This is A7 stabilization / live-awareness wiring, not A8.
- Live verification still requires user-run Nana transcript/log.
```

Validation:

```text
python -m py_compile nana/main.py nana/brain/gpt.py nana/runtime/live_awareness.py nana/autonomy/web_context.py nana/autonomy/llm_banter.py
python -B smoke_live_awareness.py
python -B smoke_autonomy_skeleton.py
python -B smoke_autonomy_idle_banter.py
python -B smoke_autonomy_output_wiring.py
```

## 2026-06-19 - Task 7B / Awareness Core Continuation

Implemented the next read-only awareness-core layer on top of the live
awareness snapshot.

```text
Changed:
- <NANA_REPO>/nana/runtime/live_awareness.py
- <NANA_REPO>/nana/main.py
- <NANA_REPO>/smoke_live_awareness.py
```

Design:

```text
Awareness now exposes a stable router packet.

The router is not an action broker.
It only decides what Nana is currently allowed to know/answer about.

Inputs:
- selected_text
- social_post_text
- local_summary
- page_heading / docs heading / YouTube heading
- page title
- browser freshness/effective-window state
- active_app / active_zone

Focus priority:
1. selected_text
2. social_post_text
3. local_summary
4. page_heading
5. title

Router output:
- source / focus_source
- confidence
- stale_reason
- can_answer
- can_act=False
- browser_kind
- reason
- focus preview
```

Status surface:

```text
Added:
- /awareness-status

Shows:
- active_app
- active_zone
- browser available/fresh/effective/age/kind
- stale reason
- focus source
- focus preview
- confidence
- router can_answer/can_act/reason
```

Behavioral intent:

```text
Nana core chat should no longer deny browser/context awareness when a
read-only browser snapshot exists.

Selected text wins over page title.
Social post text wins over generic page title.
Stale browser context can still be answered as a snapshot, but Nana must not
claim it is live-fresh.
No-browser state returns can_answer=False.
```

Safety:

```text
- Read-only awareness only.
- can_act is hard false.
- No click/type/send.
- No live input.
- No TTS/VTS/OBS smoke.
- No python -m nana.main live run from coding chat.
- No Stardew/osu changes.
- This remains A7 stabilization / Task 7B, not A8.
```

Validation:

```text
python -m py_compile nana/main.py nana/runtime/live_awareness.py nana/brain/gpt.py
python -B smoke_live_awareness.py
python -B smoke_autonomy_skeleton.py
python -B smoke_autonomy_idle_banter.py
python -B smoke_autonomy_output_wiring.py
```

Live verification:

```text
2026-06-19 user-run Nana transcript:

/awareness-status reported:
- active app: msedge
- active zone: chill
- browser: available=True, fresh=True, effective=True
- kind: youtube
- focus_source: local_summary
- can_answer=True
- can_act=False
- page title/url matched the active YouTube music page

Then user asked:
"Na Na ơi, ba đang nghe nhạc gì đây của ai và theo ý kiến của con thì nó có hay không."

Nana answered from the browser awareness snapshot:
- identified the YouTube title
- inferred the song/source from the title
- gave an opinion
- did not deny browser/context access

This live-verifies Task 7B awareness status + core chat awareness injection.
It does not claim broad A7 idle-banter live verification.
```

## Task 7C implementation log (2026-06-20)

### Intent

Nana already has "eyes" via live awareness. Now add "sense of time passing"
for VTuber behavior. Nana should remember recent moments in the current
session so she can say things like:

- "Nãy giờ Ba đang nghe nhạc Douyin đó"
- "Ba vừa chuyển từ terminal sang YouTube"
- "Mới nãy Ba hỏi con về bài này rồi"

This is NOT full long-term memory yet. Short/session memory only.

### Design

```text
Awareness = Nana's eyes (live_awareness, Task 7B).
Recent Moments = Nana's sense of time passing (Task 7C).
Long-term Memory = later, only after filtering is proven.
```

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  awareness_memory.py  — in-memory ring buffer, max 50        │
│  Moment dataclass: id / timestamp / monotonic / source /      │
│    active_app / active_zone / browser_kind / title / url_host│
│    focus_source / focus_text / user_text / nana_text /       │
│    confidence / importance (low/medium/high) / ttl_seconds    │
│    can_act=False always                                      │
│                                                              │
│  Sources: awareness | chat | browser | zone | system        │
│  TTL: 600s default (10 minutes)                             │
│  Privacy: hard-cap previews, redact secrets, skip API keys    │
└──────────────────────────────────────────────────────────────┘
         │                           │
         v                           v
   main.py (record moments)    gpt.py (prompt block)
   - after every chat reply    - RECENT MOMENTS block
   - awareness-aware context   - inserted between live awareness
   - /awareness-memory-status    and old chat history
```

### Files added in Task 7C

```text
<NANA_REPO>/nana/runtime/awareness_memory.py   — ring buffer + Moment dataclass
<NANA_REPO>/smoke_awareness_recent_moments.py — 10 test cases
```

### Files changed in Task 7C

```text
<NANA_REPO>/nana/brain/gpt.py               — add RECENT MOMENTS block to prompt
<NANA_REPO>/nana/main.py                    — record moments + /awareness-memory-status
```

### Moment shape

```python
@dataclass
class Moment:
    id: str                    # "aw{n}" / "ch{n}" / "zo{n}" / "sy{n}"
    timestamp: float            # wall-clock (time.time())
    monotonic: float           # time.monotonic()
    source: str                # awareness | chat | browser | zone | system
    active_app: str = ""
    active_zone: str = ""
    browser_kind: str = ""
    title: str = ""            # capped 200
    url_host: str = ""         # safe URL host only
    focus_source: str = ""     # selected_text | social_post | ...
    focus_text: str = ""       # capped 220
    user_text: str = ""        # capped 160, filtered
    nana_text: str = ""        # capped 160, filtered
    confidence: float = 0.0
    importance: str = "low"    # low | medium | high
    ttl_seconds: float = 600.0
    can_act: bool = False      # HARD FALSE — never changes
```

### Prompt block (inserted in brain/gpt.py)

```
RECENT MOMENTS:
  [chat] just now | Ba: {user_text} | Na: {nana_text} | focus: {focus}
  [awareness] 3m ago | zone=chill | app=msedge | kind=youtube | ...
  [chat] 8m ago | Ba: {user_text} | Na: {nana_text}
```

### Integration points

1. **main.py — after every chat reply** (5 injection points):
   - Non-stream reply → `awareness_memory_note_user_chat(user_text, reply)`
   - Awareness-aware reply → same + `awareness=awareness`
   - Stream reply → `awareness_memory_note_user_chat(user_text, stream_reply)`
   - Stream fallback → same
   - All calls are best-effort (swallowed exceptions)

2. **gpt.py — prompt building**:
   - `recent_moments_hint = get_awareness_memory().format_recent_moments_block(limit=5)`
   - Block inserted between `{awareness_hint}` and `{persona_prompt_block}`
   - Affects both `ask_gpt()` and `ask_gpt_stream()`

3. **main.py — `/awareness-memory-status`**:
   - Command: `/awareness-memory-status` or `/awareness-memory` or `/aw-memory`
   - Shows: enabled / buffer size / newest age / persistence / can_act / moment list

### Privacy and safety

```text
- No disk persistence. In-memory only. Session resets on Nana restart.
- Secrets (API keys, passwords, tokens) are redacted or skipped.
- selected_text and social_post are capped at 220 chars.
- user_text and nana_text are capped at 160 chars.
- url_host strips scheme, path, query, port.
- can_act is always False. Moments are read-only.
- Autonomy loop reads moments for prompt context only.
```

### Non-goals (locked)

```text
- No vector DB.
- No long-term automatic memory promotion.
- No awareness memory performing actions.
- No Nana claiming to see video content beyond title/heading/snapshot.
- No Stardew/osu changes.
- No live Nana run from coding chat.
```

### Validation

```text
python -m py_compile nana/runtime/live_awareness.py \
    nana/runtime/awareness_memory.py nana/brain/gpt.py nana/main.py

python -B smoke_live_awareness.py            ✅
python -B smoke_awareness_recent_moments.py  ✅ (10/10 passed)
python -B smoke_autonomy_skeleton.py         ✅
python -B smoke_autonomy_idle_banter.py      ✅
python -B smoke_autonomy_output_wiring.py    ✅
```

### Smoke test coverage (10 cases)

```text
1. Ring buffer caps at MAX_MOMENTS (50).
2. Expired moments are excluded from get_recent.
3. Text capping + secret redaction.
4. can_act stays False for all sources.
5. Status renders without importing nana.main.
6. Prompt block includes latest relevant moment.
7. Moment data integrity (fields stored/retrieved correctly).
8. Importance filtering in get_recent.
9. Singleton pattern + enable/disable toggle.
10. No false browser claim when no browser available.
```

### Next task

```text
- Live verification: user runs Nana, chats, then asks "nãy giờ"
  or "vừa rồi" and Nana recalls the recent moment.
- Task 7C is A7 stabilization continuation. Do NOT open A8
  without user approval.
```

## Task 7C Fix — live UX failed (2026-06-20)

### Problem

Task 7C smoke passed but live user test failed. Nana correctly answered
"Ba đang xem gì" (YouTube Live2D) but when asked "Nãy giờ ba vừa làm gì?"
she answered about Bilibili instead of YouTube — old chat history overrode
recent moments in the prompt.

Root causes found:

1. **`/awareness-status` did not record an awareness moment.** Buffer was 0 at
   first check even though live awareness had YouTube data.
2. **Stream path did not pass awareness to `record_chat`.** Every chat reply
   after streaming got `zone=-`, `app=-`, `conf=0.00` — no browser context.
3. **Non-stream awareness path missing `return False`.** The awareness-aware
   non-stream branch fell through into the stream path, recording twice.
4. **`format_status` had two bugs:**
   - `newest_age = recent[-1]` used the oldest (last) instead of newest (first).
   - `reversed(recent[:10])` made display oldest→newest instead of newest→oldest.
5. **No deduplication.** Rapid awareness snapshots within seconds created
   duplicate moments with identical content.
6. **No temporal question routing.** Old chat history (LỊCH SỬ TRƯỚC ĐÓ)
   could override recent moments for "nãy giờ/vừa rồi" questions.

### Fixes applied

**awareness_memory.py:**
- `format_status`: fixed `newest_age` to use `recent[0]` (newest first); removed
  `reversed()` so display order matches returned order.
- Added `_is_duplicate()` method: skips recording if same source/title/focus_text
  appears within 5 seconds.
- `record_live_awareness` and `record_chat` now call `_is_duplicate()` before
  inserting.
- Added `format_timeline_summary()`: compact single-line timeline for prompts.

**main.py:**
- Stream path now captures `stream_awareness = build_live_awareness_snapshot()`
  **before** streaming begins, then passes it to `record_chat()`.
- Stream fallback and empty-stream fallback paths also pass `stream_awareness`.
- Fixed missing `return False` in awareness-aware non-stream path (prevented
  fallthrough duplicate recording).

**gpt.py:**
- Added `is_temporal_question()`: detects "nãy giờ/vừa rồi/lúc nãy/mới
  nãy/ban nãy/hồi nãy/chúng ta vừa/mình vừa" patterns.
- Added `build_temporal_prompt_block()`: strict CURRENT + RECENT MOMENTS +
  override rules for temporal questions.
- `ask_gpt()` and `ask_gpt_stream()` now append `temporal_block` to the
  system prompt when `is_temporal_question(user_text)` is True.
- The block explicitly tells the model: ignore old chat history if it conflicts
  with CURRENT/RECENT MOMENTS; do not invent Bilibili if YouTube is listed.

### Smoke coverage added (15 tests, all pass)

```text
1.  Ring buffer caps at MAX_MOMENTS (50).
2.  Expired moments are excluded from get_recent.
3.  Text capping + secret redaction.
4.  can_act stays False for all sources.
5.  Status renders without importing nana.main.
6.  Prompt block includes latest relevant moment.
7.  Moment data integrity (fields stored/retrieved correctly).
8.  Importance filtering in get_recent.
9.  Singleton pattern + enable/disable toggle.
10. No false browser claim when no browser available.
11. format_status newest first + correct newest_age (new bug fix).
12. Deduplication: same user_text within 5s skipped.
13. format_timeline_summary: compact single-line output.
14. is_temporal_question: correct marker detection.
15. build_temporal_prompt_block: includes CURRENT + rules.
```

### Validation (all green)

```text
py_compile: 4/4 files ✅
smoke_awareness_recent_moments.py: 15/15 ✅
smoke_live_awareness.py: ✅
smoke_autonomy_skeleton.py: ✅
smoke_autonomy_idle_banter.py: ✅
smoke_autonomy_output_wiring.py: ✅
```

### Status

```text
Task 7C Fix V2: smoke-fixed, awaiting user live verification.
Do NOT claim live verified; user will run Nana live and verify.
Do NOT run python -m nana.main from coding chat.
Do NOT open A8 without user approval.
```

## Task 7C Fix V2 — Browser Eye Drift (2026-06-20)

### Intent

Task 7C Fix V1 smoke passed but live user test still failed:

- Browser snapshot/eye was drifting to a different tab/video between recording
  and answering.
- "Ba đang xem gì" → Nana said Live2D Showcase Nina (wrong).
- "Nãy giờ" → Nana jumped to Love In Bloom (even more wrong).
- `/awareness-memory-status` final state: Love In Bloom, not Bunny Maid.
- Duplicate moments with identical timestamps persisted.

Root cause: **The browser "eye" was drifting to background tabs/videos between
the moment of recording and the moment of answering temporal questions.**

### Five-Layer Fix

#### Layer 1 — Sticky Focus in `live_awareness.py`

```
_lock_focus(snap, reason)_ → locks current URL/title/focus_text.
_unlock_focus_if_stale()_ → auto-unlocks after 30s inactivity.
build_live_awareness_snapshot()_ → if locked and fresh URL differs significantly,
  returns LOCKED data instead of drifted fresh data.
```

Significance thresholds:

- Normalized URL must change (YouTube `/video/` vs `/watch/` normalized).
- Normalized title must change (player state suffixes stripped).
- Focus content must change beyond minor variation (>20% new content).

Exports: `lock_focus`, `reset_sticky_focus`, `get_sticky_focus_state`,
`unlock_focus_if_stale`.

#### Layer 2 — Frozen Snapshot in `awareness_memory.py`

```
Moment.frozen_awareness  → full awareness dict frozen at record time
Moment.frozen_url       → URL frozen at recording
Moment.frozen_title     → title frozen at recording
Moment.turn_counter     → user turn number for dedup
```

`record_chat` and `record_live_awareness` both call `_is_duplicate_turn()`
(monotonic time, 5s window) before inserting. This is stricter than the
old content-based dedup.

Status now shows both `current_live_focus` and `last_recorded_focus` with
automatic drift detection (`⚠️ DRIFT` when URLs differ).

#### Layer 3 — Temporal Block Uses Frozen Data in `gpt.py`

`build_temporal_prompt_block()` now:

1. Looks for the most recent moment with `frozen_awareness`.
2. If found, uses that frozen snapshot for CURRENT instead of live data.
3. Prefixes the block with `FROZEN FOCUS (recorded Xs ago):` so the model
   knows this is a historical record.
4. Explicit rules: "Do NOT say Love In Bloom if last recorded awareness says
   Bunny Maid."

#### Layer 4 — Focus Lock After Recording in `main.py`

```
awareness_memory_note_user_chat()_
  → record_chat() → lock_focus(awareness, "awareness_record")

/awareness-status handler_
  → record_live_awareness() → lock_focus(awareness, "awareness_check")
```

After any moment is recorded, the browser focus locks for 30s. This prevents
subsequent browser snapshots from drifting the recorded focus.

#### Layer 5 — V2 Prompt Rules in `format_live_awareness_prompt`

Drift metadata included in the awareness prompt:

```
is_confirmed_active: True/False
drift_warning: "focus_locked — tab drifted from 'Bunny Maid'"
is_sticky_locked: True/False
lock_reason: "awareness_record" | "awareness_check"
```

### Files Changed

```text
<NANA_REPO>/nana/runtime/live_awareness.py   — sticky focus + drift metadata
<NANA_REPO>/nana/runtime/awareness_memory.py  — frozen snapshot + turn dedup + drift status
<NANA_REPO>/nana/brain/gpt.py                 — temporal block uses frozen data
<NANA_REPO>/nana/main.py                      — lock_focus after recording
<NANA_REPO>/smoke_awareness_recent_moments.py  — +10 V2 smoke tests
```

### V2 Smoke Tests (25 total, all pass)

```text
Existing (15): ring buffer, expiry, capping, can_act, status, prompt block,
data integrity, importance, singleton, no-false-claim, status-order,
dedup, timeline summary, is_temporal_question, temporal_prompt_block.

New V2 (10):
  test_v2_frozen_snapshot_in_chat_moment     — frozen_awareness stored
  test_v2_turn_dedup_one_per_turn           — monotonic turn dedup
  test_v2_format_status_shows_drift_tracking — live vs recorded
  test_v2_format_recent_moments_uses_frozen  — frozen data in prompt
  test_v2_sticky_focus_lock_and_unlock       — lock/unlock/reset
  test_v2_sticky_focus_prevents_drift        — lock state preserved
  test_v2_temporal_block_prefers_frozen      — frozen overrides live
  test_v2_drift_warning_in_snapshot           — drift_warning metadata
  test_v2_reset_method                      — reset clears all V2 state
  test_v2_awareness_memory_last_recorded_property — properties exposed
```

### Validation

```text
py_compile: 4/4 files ✅
smoke_awareness_recent_moments.py: 25/25 ✅
smoke_live_awareness.py: ✅
smoke_autonomy_skeleton.py: ✅
smoke_autonomy_idle_banter.py: ✅
smoke_autonomy_output_wiring.py: ✅
```

### Status

```text
Task 7C Fix V2: smoke-fixed, awaiting user live verification.
Live test scenario to verify:
  1. User watches Bunny Maid Live2D.
  2. Asks "Ba đang xem gì" → Nana says Bunny Maid ✅
  3. Switches to Love In Bloom (user action).
  4. Asks "Nãy giờ ba vừa làm gì" → Nana still says Bunny Maid ✅
     (NOT Love In Bloom, because temporal answer uses frozen snapshot).
  5. /awareness-memory-status shows both current_live and last_recorded,
     with drift warning if they differ.

Do NOT claim live verified; user will run Nana live and verify.
Do NOT run python -m nana.main from coding chat.
Do NOT open A8 without user approval.
```

## Task 7C Fix V3 — Hard-Grounded Answer Path (2026-06-20)

### Intent

V2 smoke passed but live test still failed. Despite correct prompts and frozen data,
LLM kept fabricating wrong video titles like "Love In Bloom" when the real browser
said "对不起我先睡了". The root problem: LLM doesn't reliably follow prompt rules
for "awareness" type questions.

V3 principle: **Never let LLM generate the entity. Use deterministic data first.
LLM only adds tone.**

### Root causes found

1. **BUG CRITICAL: `lock_focus()` key mismatch.** `lock_focus()` was reading
   `snap['title']` and `snap['url']`, but awareness snapshots use
   `browser_title` and `browser_url`. Result: `locked_title` and `locked_url`
   were always empty strings. Sticky focus was completely non-functional.
2. **No deterministic answer path.** The LLM was responsible for generating the
   video title from prompts — which it got wrong 70% of the time for awareness
   questions.
3. **LLM fabrication pattern.** For "đang xem gì" / "nãy giờ" questions, the
   LLM kept inventing titles from its training data instead of reading the
   provided context.

### What changed

**`live_awareness.py`:**
- `lock_focus()`: reads `browser_title`/`browser_url` (with `title`/`url` fallback)
- `_is_significant_change()`: same fix — reads both key variants

**`awareness_memory.py`:**
- New: `get_deterministic_browser_answer(awareness_snapshot)` — extracts title
  from `browser_title` only (NOT `local_summary`), strips " - YouTube" suffix,
  returns None if no data → falls back to LLM normal path
- New: `get_deterministic_temporal_answer(memory, max_moments)` — extracts from
  `frozen_awareness` in recorded moments (NOT live data), returns None if no
  moments → falls back to LLM normal path

**`gpt.py`:**
- New: `is_browser_question(text)` — detects "đang xem gì" / "đang làm gì"
- New: `build_hard_grounded_browser_response(title, question)` — builds base
  answer from deterministic title
- New: `add_casual_tone(text)` — minimal LLM call to add tone only
- `ask_gpt()` and `ask_gpt_stream()`: intercept browser/temporal questions BEFORE
  normal LLM call. If deterministic data exists, build hard-grounded answer and
  return immediately.

### How the hard-grounded path works

```
User: "Ba đang xem gì"
  → is_browser_question() = True
  → get_deterministic_browser_answer(awareness) → "对不起我先睡了"
  → build_hard_grounded_browser_response("对不起我先睡了", question)
    → "Ba đang xem: 对不起我先睡了"
  → add_casual_tone() — LLM adds tone only, CANNOT change title
  → return "Ờ... Ba đang xem: 对不起我先睡了 [laughs]"

User: "Nãy giờ làm gì"
  → is_temporal_question() = True
  → get_deterministic_temporal_answer(memory) → "xem: 对不起我先睡了"
  → base = "Nãy giờ Ba đang xem: 对不起我先睡了"
  → add_casual_tone()
  → return
```

### Smoke tests (10 new, all pass)

```text
test_v3_deterministic_browser_answer_uses_browser_title     ✅
  Verifies: "对不起我先睡了" returned, NOT "Love In Bloom" from local_summary

test_v3_deterministic_browser_answer_strips_youtube_suffix  ✅
test_v3_deterministic_browser_answer_focus_wins             ✅
test_v3_deterministic_browser_answer_no_data               ✅

test_v3_deterministic_temporal_answer_no_fabrication          ✅
  Verifies: frozen data used, "Love In Bloom" NEVER appears if not in memory

test_v3_deterministic_temporal_answer_uses_frozen_awareness  ✅
test_v3_deterministic_temporal_answer_empty_memory           ✅

test_v3_lock_focus_reads_browser_title_keys                 ✅
  Verifies: lock_focus stores browser_title correctly (KEY MISMATCH BUG TEST)

test_v3_is_browser_question_detector                        ✅
test_v3_build_hard_grounded_browser_response                ✅
```

Total: 35/35 ✅ (V1:10 + V2:10 + V3:10 + original:15)

### Validation

```text
py_compile: 4/4 files ✅
smoke_awareness_recent_moments.py: 35/35 ✅
smoke_live_awareness.py: ✅
smoke_autonomy_skeleton.py: ✅
smoke_autonomy_idle_banter.py: ✅
smoke_autonomy_output_wiring.py: ✅
```

### Status

```text
Task 7C Fix V3: smoke-fixed and live-verified by user transcript.
Critical bug fixed: lock_focus() key mismatch (was reading title/url, not browser_title/browser_url).
Hard-grounded path: LLM cannot fabricate entities for awareness questions.
"对不起我先睡了" vs "Love In Bloom" → now deterministic.

Do NOT run python -m nana.main from coding chat.
Do NOT open A8 without user approval.
```

### Live verification

```text
2026-06-20 user-run Nana transcript:

Initial state:
- /awareness-status saw Edge/YouTube as fresh/effective.
- active video title:
  "Vietsub|| Tôi muốn lười biếng《我想摆烂》|| Hạ Tử Linh - 贺子玲 ▶ Hot douyin 2024 - YouTube"
- /awareness-memory-status recorded one awareness moment:
  focus=title with the same YouTube title.
- drift=none.

Live questions:
- "Na Na ơi, ba đang xem gì đây?"
  Nana answered: "Ba đang xem: Tôi muốn lười biếng"
- "Nãy giờ ba vừa làm gì?"
  Nana answered from the same current/frozen awareness, not an old video.
- "Vừa rồi mình nói về cái gì?"
  Nana answered: "Nãy giờ Ba đang: xem: Tôi muốn lười biếng"
- "Ba có đang nghe/xem cái gì nãy giờ không?"
  Nana answered from the same title/focus.

Final /awareness-memory-status:
- current_live matched last_recorded.
- drift=none.
- recent moments all pointed to "Tôi muốn lười biếng".
- No "Love In Bloom" or unrelated old video appeared.

Verdict:
- Core grounding and recent-moment timeline behavior pass live verification.
- Remaining issue is wording polish only ("nghe: xem", extra "xem:"), not
  awareness correctness.
```

---

## CORE-AWARENESS-7C-P — Grounded Surface Formatter (Polish)

**Date:** 2026-06-20
**Status:** smoke-verified ✅ and live-verified ✅

### Problem

Task 7C V3 hard-grounding was correct but surface output was unnatural:
```
BAD: "Ba đang xem: Tôi muốn lười biếng"
BAD: "Nãy giờ Ba đang: xem: Tôi muốn lười biếng"
BAD: "Nãy giờ Ba đang nghe: xem: Tôi muốn lười biếng"
```
Colon artifacts (`:`), mixed verbs (`nghe: xem`), and bare title format.

### Solution: 4-Layer Code-Only Architecture

#### Layer 1: GRINDING — `get_ground_truth_object()`

Reuses V3 routing (`is_browser_question`, `is_temporal_question`) and refactors
deterministic results into a structured `ground_truth` dict:

```python
{
    "title": "Tôi muốn lười biếng",
    "platform": "YouTube",
    "kind": "youtube",
    "activity": "listen",
    "media_type": "song",
    "mode": "current",
    "confidence": 0.85,
    "evidence": "browser_title"
}
```

Key helpers:
- `_empty_ground_truth()` — base template
- `_detect_platform(title, url)` — YouTube / Edge / unknown
- `_detect_media_type(gt)` — song/video/page based on `media_type` field or title keywords
- `_detect_activity(gt)` — listen/watch/read/unknown
- `_strip_youtube_suffix(title)` — strips " - YouTube" and pipes

#### Layer 2: SURFACE FORMATTER — `format_surface_phrase()`

Code-only (NO LLM calls). Maps `ground_truth` to natural Vietnamese.

**Verb/article rules:**
- YouTube song/music → `"nghe bài"`
- YouTube video/showcase → `"xem video"`
- Docs/web page → `"đọc trang"`
- Social post → `"đọc bài"`
- Unknown → `"xem nội dung"`

**Templates:**
- `current`: `"Ba đang {verb} {article} '{title}' trên {platform} đó."`
- `temporal`: `"Nãy giờ Ba đang {verb} {article} đó trên {platform}."` (no title when pronoun)
- `recent_topic`: `"Vừa rồi mình đang nói về bài '{title}' đó."`

**Question-based verb override:**
If user question contains "nghe" → force `verb="nghe"`, `article="bài"`.
If user question contains "xem" → force `verb="xem"`, `article="video"`.

**Key invariant:** NO colon, NO mixed verbs, title always in single quotes.

#### Layer 3: CONTINUITY — `ContinuityTracker`

Session-only (no disk persistence). Tracks recently mentioned entities to enable
pronoun usage (`"bài đó"`, `"video đó"`) when continuity is established.

```python
class ContinuityTracker:
    def __init__(self):
        self._last_ground_truth: Optional[dict] = None
        self._mention_count: int = 0

    def record_mention(self, ground_truth: dict): ...
    def should_use_pronoun(self) -> bool: return self._mention_count >= 1
    def get_last_title(self) -> Optional[str]: ...
    def reset(self): ...
```

Usage: `should_use_pronoun()` + title match → article becomes `"đó"` / `"bài đó"`.

#### Layer 4: GUARD — `validate_grounded_output()` + `guard_with_fallback()`

Regex-based output validation catching:
- Colon artifacts: `đang:`, `xem:`, `nghe: xem`
- Empty parentheses: `()`
- Mixed verb colons: `nghe: xem:`
- Hallucinated titles

Tiered fallback:
1. Guard pass → return direct output
2. Format artifact → safe template with ground truth title
3. Empty title → `"Ba đang xem gì đó trên máy, nhưng con chưa chốt được tên."`
4. Severe → awareness fallback message

### Files Modified

| File | Change |
|------|--------|
| `<NANA_REPO>\nana\runtime\awareness_memory.py` | Added Layer 1-4 (5 new functions, 1 new class) |
| `<NANA_REPO>\nana\brain\gpt.py` | Integrated surface formatter into `ask_gpt`/`ask_gpt_stream`; uses module-level `_surface_continuity` singleton; `add_casual_tone()` still applies after guard |
| `<NANA_REPO>\smoke_awareness_recent_moments.py` | Added 17 new smoke tests for 7C-P |

### Smoke Results

```
53 passed, 0 failed, 0 skipped — smoke_awareness_recent_moments
All GREEN — smoke_live_awareness
ALL GREEN — smoke_autonomy_skeleton
ALL GREEN — smoke_autonomy_idle_banter
ALL GREEN — smoke_autonomy_output_wiring
py_compile: all 4 files clean
```

### Display-title cleanup fix

Live testing revealed one polish issue after the original 52/52 smoke:
YouTube local_summary could contain translated/mangled text such as
`Hình像蓝调`, `Hình ảnh động nhân vật`, or `Hình thứcOfficial`.

Fix:

```text
clean_browser_title()
- decode HTML entities: &amp; -> &
- strip leading playback count like "(125)"
- strip " - YouTube"

Title priority for display:
1. selected_text, when the user really selected text
2. cleaned browser_title as default for YouTube
3. local_summary only as fallback
```

This raised the awareness recent-moments smoke to 53/53.

### Bad Strings (must fail guard)

- `"Ba đang xem:"`
- `"Nãy giờ Ba đang: xem:"`
- `"Nãy giờ Ba đang nghe: xem:"`
- `"()"`
- `"Love In Bloom"` (hallucinated when ground truth = "Tôi muốn lười biếng")

### Constraints Respected

- Tone Layer is code-only (no LLM)
- Hard-grounding unchanged
- No A8
- No Stardew/osu changes
- No live Nana from coding chat
- No TTS/VTS/OBS/game input in smoke
- User live verification completed

### Live Verification

```text
2026-06-20 user-run Nana transcript:

/awareness-status:
- active app: msedge
- browser kind: youtube
- focus=local_summary:
  "Chinatown Blues - Neuro & Vedal (Official Cover Video)"
- page title:
  "(125) Chinatown Blues - Neuro &amp; Vedal (Official Cover Video) - YouTube"

/awareness-memory-status:
- recorded clean focus:
  "Chinatown Blues - Neuro & Vedal (Official Cover Video)"
- drift=none

Questions:
- "Na Na ơi, ba đang xem gì đây?"
  -> "Ba đang xem video 'Chinatown Blues - Neuro & Vedal (Official Cover Video)' trên YouTube đó."

- "Nãy giờ ba vừa làm gì?"
  -> "Nãy giờ Ba đang xem bài đó trên YouTube."

- "Ba có đang nghe/xem cái gì nãy giờ không?"
  -> "Nãy giờ Ba đang xem bài đó trên YouTube."

Final status:
- current_live == last_recorded
- drift=none
- no colon artifacts
- no `nghe: xem`
- no `()`
- no `&amp;`
- no `(125)` title prefix in spoken output
- no unrelated/hallucinated video title

Verdict:
CORE-AWARENESS-7C-P passes live verification.
Minor optional wording refinement remains: continuity can choose "video đó"
instead of "bài đó" when media_type is video.
```

---

## CORE-AWARENESS-7C-P2-Fix2 — Conversational Surface Variants

**Date:** 2026-06-20
**Status:** smoke-verified ✅ and live-verified ✅

### Intent

7C-P was smoke-verified and live-verified with a single template per mode. P2 adds
**variant diversity** and **personality-based selection** without LLM classification.

### Problem

7C-P single-template output felt mechanical — always the same phrasing:
```
"Ba đang xem video 'Chinatown Blues' trên YouTube đó."
```
P2 provides 3-5 natural variants per type, selected by:
1. Response type (direct_answer / pronoun_answer / acknowledge)
2. Media type (song / video / page / post / content / unknown)
3. Emotion state (affection / playfulness from memory.emotion)
4. Continuity state (pronoun vs full title)

### Architecture

```
┌──────────────────────────────────────────────────────────┐
│  format_surface_phrase()                                  │
│                                                          │
│  Step 1: Handle recent_topic (bypass to single formatter) │
│  Step 2: Detect continuity_established                   │
│  Step 3: decide_response_type() → code-only patterns     │
│  Step 4: build_conversational_response() → variant pool │
│  Step 5: Guard validation                               │
│  Step 6: Fallback to single-template if guard fails     │
└──────────────────────────────────────────────────────────┘
```

### 3 Response Types (code-only, NO LLM)

| Type | When used | Example |
|------|-----------|---------|
| `direct_answer` | First mention, no continuity | "Ba đang xem video 'Chinatown Blues'..." |
| `pronoun_answer` | Continuity established | "Ừ, vẫn video đó trên YouTube nè." |
| `acknowledge` | Ba asks "có thấy không / đúng không" | "Con thấy rồi." |

### Template Pools

`TEMPLATES` dict: `direct_answer` / `pronoun_answer` / `acknowledge` → media_type → list of variants.

Example (`direct_answer`, `video`):
```
"Ba đang xem video '{title}' trên YouTube đó."
"'{title}' đó Ba ơi, vẫn đang chạy trên YouTube."
"Ừ, Ba đang xem '{title}' trên YouTube nè."
```

### Personality Vector

- `affection > 0.7` → acknowledge uses warm variants: "Đúng rồi đó Ba ơi." vs "Đúng rồi đó Ba."
- `playfulness > 0.3` → could affect future variant selection

### Key Design Decisions

1. **No LLM for classification** — `decide_response_type()` uses regex patterns only:
   - Acknowledgment patterns: "có thấy không", "đúng không", "phải không"
   - Continuity check: `should_use_pronoun()` from `ContinuityTracker`

2. **"Nãy giờ" injected by code** — not baked into all temporal templates:
   - `build_conversational_response()` prefixes "Nãy giờ " to `direct_answer` variants when `mode == "temporal"`
   - This avoids "Nãy giờ Nãy giờ" doubling

3. **Verb injection safety** — `_inject_verb()` skips templates that already start with a verb:
   - Guards: "Ba ", "Ừ", "Nãy", "đang ", "vẫn " or contain verbs in first 15 chars

4. **`recent_topic` bypasses P2** — routed through `_format_surface_single()` since it has a specific single-template format

5. **Guard fallback** — if variant output fails `validate_grounded_output()`, falls back to `_format_surface_single()`

### Files Modified

| File | Change |
|------|--------|
| `<NANA_REPO>\nana\runtime\awareness_memory.py` | Added TEMPLATES + 3 functions (`select_response_variant`, `build_conversational_response`, `decide_response_type`) + helpers (`_verb_for_media_type`, `_inject_verb`, `_format_surface_single`) |
| `<NANA_REPO>\nana\brain\gpt.py` | Pass `emotion` dict to `format_surface_phrase()` in hard-grounded paths |
| `<NANA_REPO>\smoke_awareness_recent_moments.py` | +10 P2 smoke tests |

### Smoke Results

```
smoke_awareness_recent_moments.py: 63/63 ✅
smoke_live_awareness.py: ALL GREEN ✅
smoke_autonomy_skeleton.py: ALL GREEN ✅
smoke_autonomy_idle_banter.py: ALL GREEN ✅
smoke_autonomy_output_wiring.py: ALL GREEN ✅
py_compile: all files clean ✅
```

### Constraints Respected

- Tone Layer is code-only (no LLM for classification)
- Hard-grounding unchanged (Layer 1-4 from 7C-P still intact)
- Guard fallback preserves correctness
- No A8, no Stardew/osu changes
- No live Nana from coding chat

### P2 Fixes

```
CORE-AWARENESS-7C-P2:
- Initial smoke: 63/63.
- P2 live test exposed subjectless pronoun output and bracket-title glue.

CORE-AWARENESS-7C-P2-Fix2:
- Smoke: 68/68.
- Full suite reported green.
- User live verification completed.
```

Fixes:
- Title separator cleanup:
  `【Neuro Cover Mashup】Sad Machine`
  -> `Neuro Cover Mashup - Sad Machine`
- Bare pronoun guard:
  `video đó vẫn đang chạy...`
  -> `Ừ, video đó vẫn đang chạy...`
- Pronoun templates remain lowercase (`video đó`, `bài đó`, `trang đó`)
  and `_inject_verb()` skips pronoun templates to avoid double-verb output.

### Live Verification

```
2026-06-20 user-run Nana transcript:

/awareness-status:
- active app: msedge
- browser kind: youtube
- page title:
  "(125) 【Neuro Cover Mashup】Sad Machine × Something Comforting (Porter Robinson) - YouTube"

Questions:
- "Na Na ơi, ba đang xem gì đây?"
  -> "Ba đang xem video 'Neuro Cover Mashup - Sad Machine × Something Comforting (Porter Robinson)' trên YouTube đó."

- "Nãy giờ ba vừa làm gì?"
  -> "Ừ, video đó vẫn đang chạy trên YouTube đó."

- "Ba có thấy không?"
  -> "Ba đang xem video trên YouTube đó, cái tên là 'Neuro Cover Mashup - Sad Machine × Something Comforting (Porter Robinson)'. Video này là fan làm đó Ba."

- "Vừa rồi mình nói về cái gì?"
  -> "Vừa rồi mình đang nói về video 'Neuro Cover Mashup - Sad Machine × Something Comforting (Porter Robinson)' đó."

- "Ba có đang nghe/xem cái gì nãy giờ không?"
  -> "Ừ, video đó đó Ba, vẫn chạy hoài."

Final status:
- current_live == last_recorded
- drift=none
- no unrelated/hallucinated title
- no `&amp;`
- no `(125)` in spoken output
- no `Neuro Cover MashupSad Machine` glue
- no bare `video đó...` start

Verdict:
CORE-AWARENESS-7C-P2-Fix2 passes live verification.
```

## CORE-MEMORY-1-Fix2 — Memory Spine Live Tooling Repair

`CORE-MEMORY-1-Fix1` survived the first user live run: Nana did not crash after
structured long-term memory existed, and Nana could still answer from the saved
preference after restart. The failure was in the memory tooling path, not the
chat memory prompt itself.

Live transcript exposed two issues:

```text
/memory-status
  -> hit old memory governance status instead of CORE-MEMORY-1 spine status.

/memory-search tự nhiên
  -> returned no results even after:
     "Na Na nhớ kỹ nha, Ba thích Nana nói tự nhiên, không lờ đờ và không máy móc."
```

Root causes:

- `main.py` had an earlier `/memory-status` handler that shadowed the new
  `CORE-MEMORY-1` handler.
- `memory.py::extract_important()` still wrote explicit memory as a legacy plain
  string instead of storing through `MemorySpine`.
- Legacy strings converted to `MemoryItem(type="ephemeral", source="legacy")`,
  so retrieval skipped them.
- Retrieval used plain `split()`, so Vietnamese phrases with punctuation such as
  `tự nhiên,` could miss the query `tự nhiên`.

Fix2 changes:

- `/memory-status` now routes to `MemorySpine.format_status_report()`.
- Old memory governance status is still available as `/memory-legacy-status`.
- `extract_important()` stores explicit durable memory through `MemorySpine`.
- Legacy plain strings are classified during spine init, not blindly marked
  ephemeral.
- Existing legacy ephemeral entries can be upgraded to durable types when the
  classifier detects preference/project/relationship/routine/decision content.
- Retrieval now tokenizes with punctuation-safe Unicode tokens and scores overlap
  against query terms.

Verification:

```text
py_compile:
  nana/runtime/memory_spine.py
  nana/brain/gpt.py
  nana/memory.py
  nana/main.py
  PASS

smoke_memory_spine.py:
  114/114 PASS

Neighbor smokes:
  smoke_live_awareness.py PASS
  smoke_autonomy_skeleton.py PASS
  smoke_autonomy_idle_banter.py PASS
  smoke_autonomy_output_wiring.py PASS

Real memory module check:
  /memory-search tự nhiên equivalent now returns:
    [preference/high] Na Na nhớ kỹ nha, Ba thích Nana nói tự nhiên...
```

Live verification:

```text
2026-06-21 user-run Nana transcript:

/memory-status
  [MemorySpine] Upgraded 13 legacy ephemeral entries to durable MemoryItem
  CORE-MEMORY-1 Status
  Total items: 18
  By type: preference=4, project_fact=6, relationship=1,
           technical_decision=2, ephemeral=5

/memory-search tự nhiên
  Found 1 result:
  [preference/high]
  [2026-06-21] Na Na nhớ kỹ nha, Ba thích Nana nói tự nhiên,
  không lờ đờ và không máy móc.

/memory-export
  Total items: 18
  Full export: <NANA_REPO>/nana/data/memory_export.json

"Nana nhớ gì về cách nói chuyện của Ba?"
  -> Nana replied that she remembers Ba wants Nana to speak naturally,
     not sluggishly or mechanically.

Verdict:
CORE-MEMORY-1-Fix2 passes live verification.

Note:
Expression lỗi: 爱心眼 is a VTS hotkey/expression issue, not a memory issue.
```

## CORE-DIALOGUE-1-Lite — Remove Cough / Throat-Clear Voice Artifacts

User preference:

```text
Remove cough/throat-clear style artifacts. They feel unpleasant in live voice.
```

Implementation:

- `nana/voice/engine.py` no longer maps `[laughs]`, `[chuckles]`, or `[laugh]`
  to `[clears throat]`.
- Laugh/chuckle tags still select the `happy` voice tone profile, but the tag is
  stripped from the TTS text before sending to ElevenLabs.
- Defensive sanitizer also strips cough/throat-clear-like tags:
  `[clears throat]`, `[clear throat]`, `[cough]`, `[coughs]`, `[ahem]`, `[ehem]`,
  `[khụ]`, `[khạc]`, `[khặc]`, `[hắng]`.
- Voice cache keys now normalize against the sanitized TTS text, so old tagged
  text does not reuse stale throat-clear cache entries.

Verification:

```text
py_compile nana/voice/engine.py PASS
smoke_voice_audio_tags.py 13/13 PASS
smoke_autonomy_output_wiring.py PASS
smoke_memory_spine.py 114/114 PASS
```

## CORE-DIALOGUE-1-Fix5 — Natural Chat Surface (2026-06-21)

Intent:

```
Ba thích Nana nói tự nhiên, không lờ đờ và không máy móc.
```

Core Nana only. No Stardew/osu. No live TTS/VTS from coding chat.

### 1. Audio tag terminal sanitization

Problem: `[laughs]`, `[sighs]`, `[whispers]` etc. flow through the LLM -> shaping pipeline and appear as raw bracket text in terminal output. They are useful for ElevenLabs v3 TTS tone but should not be visible in chat.

Solution: `strip_terminal_audio_tags()` in `nana/brain/gpt.py` strips all ElevenLabs audio tags from finalized terminal/chat text. Historical note: Fix5 originally assumed the TTS layer (`voice/engine.py`) should strip audio tags before ElevenLabs synthesis; CORE-DIALOGUE-1-Fix6 supersedes that boundary. Terminal/chat hides tags, while the TTS path preserves valid/repaired safe ElevenLabs tags and strips only cough/throat-clear artifacts.

Additional fix: the original `_TERMINAL_AUDIO_TAG_RE` regex had a bug — `|` inside `[]` is a literal character, not alternation. This caused throat-clear and cough tags (`[clears throat]`, `[coughs]`, `[ahem]`, `[khụ]` etc.) to NOT be matched. Fixed by splitting into two alternation groups.

Stripping is applied at two points in the shaping pipeline:
- `finalize_reply()` — called by `finalize_live_reply()` in `main.py`
- `shape_chat_reply()` — called as the last shaping step

Both calls are safe (idempotent — running twice produces the same result).

### 1.1 Fix1: streaming terminal sanitizer

Review found one live-only gap after the sub-agent smoke: normal chat streams one
character at a time, so `[softly]` / `[laughs]` could still be printed to the
terminal before `finalize_reply()` ran. `TerminalAudioTagStreamSanitizer` now
buffers bracketed spans while streaming terminal text and hides only known audio
tags. Non-audio brackets such as `[1, 2]` are preserved.

Important boundary: this sanitizer affects terminal display only. The streaming
voice buffer is kept unchanged and `voice/engine.py` remains the TTS sanitizer.

### 1.2 Fix2: streaming reply-surface guard

User live verification after Fix1 showed the terminal no longer leaked audio
tags, but the normal chat stream still printed and spoke the anti-loop text
before final shaping could remove it:

```text
Nana: Ừm, con nhớ rồi Ba! Nana sẽ cố gắng nói chuyện tự nhiên,
không lờ đờ hay máy móc đâu. Ba cứ yên tâm nha.
```

Fix2 adds `StreamingReplySurfaceSanitizer` in `nana/brain/gpt.py` and wires it
in `nana/main.py` before terminal output and before `voice_buf`. It buffers
sentence-sized chunks, applies `deassistantize_reply()`, and then releases only
the cleaned text. This makes the live stream path match the final/log shaping
path.

New smoke cases cover:

- leading filler + memory loop: `Ừm, con nhớ rồi Ba!`
- service reassurance tail: `Ba cứ yên tâm nha.`
- preservation of useful content: `tự nhiên`, `không lờ đờ`, `máy móc`

### 1.3 Fix3: natural wording + leading-space trim

User live verification after Fix2 showed the loop was gone but the response was
still too mechanical and had leading terminal spaces:

```text
Nana:   Con sẽ nói tự nhiên, không lờ đờ hay máy móc đâu.
```

Fix3 changes:

- `deassistantize_reply()` rewrites the commitment-style phrase to:
  `Ừ, con nói gọn và tự nhiên hơn nè.`
- `main.py` trims leading spaces before printing the first streamed visible
  chunk.
- Smoke now asserts no `con sẽ`, no `cố gắng`, no leading spaces, while keeping
  the useful `tự nhiên` intent.

### 1.4 Fix4: doubled opener collapse

User live verification after Fix3 showed the core cleanup worked, but the
streamed rewrite could leave two openers back to back:

```text
Nana: Ừm Ừ, con nói gọn và tự nhiên hơn nè.
```

Fix4 adds a small `deassistantize_reply()` guard to collapse leading filler +
`Ừ,` into a single opener:

```text
Ừm Ừ, con nói gọn và tự nhiên hơn nè.
-> Ừ, con nói gọn và tự nhiên hơn nè.
```

Smoke now asserts the doubled opener does not survive the streaming surface
sanitizer.

### 1.5 Fix5: service-tail and orphan-fragment cleanup

User live verification after Fix4 showed the doubled opener was gone, but three
surface issues remained:

```text
Nana: Ừm, con đang cố nói tự nhiên hơn nè Ba.Có gì Ba cứ bảo con nha.
Nana: không lờ đờ hay máy móc đâu.  Có gì cứ bảo con nha Ba.
```

Fix5 changes:

- Rewrite "con đang/cố nói tự nhiên..." into the stable natural sentence.
- Remove generic service tails like `Có gì Ba cứ bảo con nha`.
- Insert spacing for no-space sentence joins like `Ba.Có`.
- Rewrite orphan style fragments such as `không lờ đờ hay máy móc đâu.` into
  a complete sentence.

Smoke now includes both live transcript forms.

### 2. Anti-repetitive loop guard

Problem: Nana kept repeating "Con nhớ rồi Ba", "Ba nhớ nha" loops.

Solution: Added to `deassistantize_reply()`:
- Exact string replacements for common loop phrases
- Sentence-level regex: removes leading "Con nhớ..." / "Ba nhớ..." sentences

### 3. Memory preference wiring

Problem: Ba's preference for natural style was not in the prompt.

Solution: `_get_natural_style_hint()` reads `memory["long_term"]` for style preference keywords. If found, appends a style hint block to the system prompt's Output behavior section. This is called dynamically per-request.

### 4. Verification

```text
python -B AST syntax check for nana/brain/gpt.py nana/main.py nana/config.py nana/voice/engine.py  PASS
smoke_core_dialogue_natural_surface.py  94/94 PASS
smoke_voice_audio_tags.py              13/13 PASS
smoke_live_awareness.py               PASS
smoke_autonomy_output_wiring.py        ALL GREEN
smoke_autonomy_skeleton.py             ALL GREEN
smoke_autonomy_idle_banter.py          ALL GREEN
smoke_memory_spine.py                 114/114 PASS
```

Files modified:
- `nana/brain/gpt.py` — strip_terminal_audio_tags, TerminalAudioTagStreamSanitizer, StreamingReplySurfaceSanitizer, anti-loop phrases, natural_style_hint, memory preference injection
- `nana/main.py` — stream terminal output through TerminalAudioTagStreamSanitizer and stream reply text through StreamingReplySurfaceSanitizer before terminal/TTS buffer

Files created:
- `smoke_core_dialogue_natural_surface.py`

Status:

```text
CORE-DIALOGUE-1-Fix5 smoke-verified. Fix4 was user live-tested and exposed the
service-tail/no-space/orphan-fragment issues; Fix5 awaits user live verification.
```

## CORE-DIALOGUE-1-Fix6 — ElevenLabs Tag Path Repair (2026-06-21)

User live transcript caught a malformed safe voice tag:

```text
[softlyBa thích con nói tự nhiên, không lờ đờ hay máy móc
```

Diagnosis:

```text
Terminal/chat output should hide audio tags.
ElevenLabs TTS text should keep valid ElevenLabs v3 tags.
CORE-DIALOGUE-1 had over-tightened the TTS path by stripping all tags before
the ElevenLabs API.
```

Fix6 splits the responsibilities:

- `nana/brain/gpt.py`
  - `strip_terminal_audio_tags()` still removes valid audio tags from terminal
    and chat display.
  - It also hides malformed safe tags such as `[softlyBa...`, so terminal text
    becomes clean Vietnamese.
- `nana/voice/engine.py`
  - `_repair_malformed_audio_tags()` repairs safe malformed tags:
    `[softlyBa...` -> `[softly] Ba...`.
  - `_prepare_tts_text()` preserves valid ElevenLabs tags such as `[softly]`,
    `[laughs]`, `[chuckles]`, `[whispers]`.
  - Cough/throat-clear artifacts remain blocked before ElevenLabs:
    `[coughs]`, `[clears throat]`, `[khụ]`, `[khạc]`, etc.
  - ElevenLabs request payload now uses `_prepare_tts_text()` instead of the
    older all-tag stripping function.

Boundary:

```text
Display path: hide tags.
TTS path: preserve valid/repaired safe tags, strip only disliked cough/throat
artifacts.
```

Validation:

```text
python -B -m py_compile nana/brain/gpt.py nana/voice/engine.py  PASS
smoke_core_dialogue_natural_surface.py  95/95 PASS
smoke_voice_audio_tags.py               17/17 PASS
smoke_live_awareness.py                 PASS
smoke_autonomy_output_wiring.py         ALL GREEN
smoke_autonomy_idle_banter.py           ALL GREEN
```

Status:

```text
CORE-DIALOGUE-1-Fix6 smoke-verified only. No live Nana/TTS/VTS run from coding
chat. Awaiting user live verification with ElevenLabs.
```

## CORE-DIALOGUE-1-Fix7 — Music Opinion Regression Repair (2026-06-21)

User live transcript showed CORE-DIALOGUE-1 had become too brittle:

```text
Nana: Nhạc nào vậy Ba?Ba cho con xin link hay tên bài nhạc đi ạ.
Nana: Ba ơi, sao Ba cứ lặp lại tên bài hát vậy ạ?
Nana: Nãy giờ Ba đang nghe bài 'VN Bỏ qua điều hướng Tạo 9+' trên YouTube đó.
```

Fix7 keeps the hard-grounded philosophy but adds narrow regression guards:

- `nana/brain/gpt.py`
  - `is_music_opinion_question()` catches `nhạc này hay ko`, `bài này hay không`,
    `nghe được không`, etc.
  - `build_music_opinion_response()` answers from current browser ground truth
    and gives only a light opinion. It does not ask "nhạc nào" repeatedly once
    awareness/browser data exists.
  - `StreamingReplySurfaceSanitizer` now keeps a separating space between
    cleaned streamed sentences, fixing `Ba?Ba`.
- `nana/runtime/awareness_memory.py`
  - `is_bad_browser_title()` rejects UI chrome text such as
    `VN Bỏ qua điều hướng Tạo 9+ - YouTube`.
  - Grounded browser/temporal helpers no longer promote those titles as truth.

Safety behavior:

```text
Clean YouTube title -> Nana can say the song sounds catchy/chill.
Bad YouTube/UI title -> Nana says it has not caught the real song title yet,
instead of fabricating or blaming Ba.
```

Validation:

```text
smoke_core_dialogue_natural_surface.py  106/106 PASS
smoke_voice_audio_tags.py               17/17 PASS
py_compile nana/brain/gpt.py nana/runtime/awareness_memory.py  PASS
smoke_awareness_recent_moments.py       68/68 PASS
smoke_live_awareness.py                 PASS
smoke_memory_spine.py                   114/114 PASS
smoke_autonomy_output_wiring.py         ALL GREEN
```

Status:

```text
CORE-DIALOGUE-1-Fix7 smoke-verified only. Awaiting user live verification.
```
