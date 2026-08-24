# Nana Runtime Rewire State

<!-- STEP 6 STAGING-ONLY LIVE TEST; NOT PROMOTED -->

Last checked: 2026-08-12 (wiki reconciliation; runtime facts current to this snapshot)

Purpose: prevent future chats from reading the old `main.py` gravity as the
current architecture.

## Current Truth

Nana core has been cut into the new runtime path.

Current user-facing runtime entry:

```text
python -u <NANA_REPO>\nana\__main__.py
```

Current core hot path:

```text
<NANA_REPO>\nana\__main__.py
-> nana.cli.app
-> nana.cli.handle_text
-> nana.core/*
-> nana.runtime/*
-> nana.brain/*
-> nana.memory / nana.runtime.memory_spine
-> nana.voice / nana.integrations / nana.social as needed
```

`<NANA_REPO>\nana\main.py` still exists and is still large, but it is no longer the
new-runtime command hot path. Do not use its size as proof that the rewire
failed.

## 2026-08-12 Reconciliation Snapshot

```text
handle_text.py=177 lines
registry total=4499; live=1397; archived=107; reserved=2995
runtime_loaded_count=46
smoke_command_registry_truth.py=12/12 PASS
smoke_imports.py=2 known analyzer warnings; direct imports PASS
nana.main_loaded=False; nana.main_cut_loaded=False
game_loaded_count=0; phase_loaded_count=0
```

The two analyzer warnings are known false positives around
`bytearray/memoryview` in `decode_media_frame` and `SystemExit` in the
read-only Session-core tool. They are not runtime import failures. Presence
remains `ESP32-OWNER-HOLD-1 = WAITING_OWNER / DEFERRED`; the game capability
freeze remains active.

2026-07-09 correction: the rewire away from `main.py` is real, and the command
boundary has now been split enough that `<NANA_REPO>\nana\cli\handle_text.py` is no
longer the main bottleneck. Treat it as a thin dispatcher facade; remaining
debt lives in lazy command impl buckets and legacy phase/archive history.

Current import probe:

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

2026-06-27 code probe:

```text
import nana.cli.handle_text -> <NANA_REPO>\nana\cli\handle_text.py
has handle_text: True
nana.main loaded: False
```

Stage 9D/9E/9F code paths are now wired through this new runtime path:

```text
<NANA_REPO>\nana\cli\handle_text.py
<NANA_REPO>\nana\commands\help.py
<NANA_REPO>\nana\core\status.py
<NANA_REPO>\nana\runtime\public_stage_identity.py
<NANA_REPO>\nana\runtime\lane_leak_audit.py
<NANA_REPO>\nana\runtime\memory_consolidation_preview.py
<NANA_REPO>\nana\runtime\context_budget_audit.py
```

2026-07-05 code sync found later public Core stages still wired through the
same runtime path:

```text
<NANA_REPO>\nana\runtime\core_self.py
<NANA_REPO>\nana\runtime\public_voice_style.py
<NANA_REPO>\nana\runtime\public_conversation_director.py
<NANA_REPO>\nana\runtime\public_thread_memory.py
<NANA_REPO>\nana\runtime\public_running_jokes.py
<NANA_REPO>\nana\runtime\public_fallback_recovery.py
<NANA_REPO>\nana\runtime\public_memory_filter.py
<NANA_REPO>\nana\cli\handle_text.py
<NANA_REPO>\nana\commands\help.py
<NANA_REPO>\nana\core\status.py
<NANA_REPO>\nana\runtime\public_stage_identity.py
```

These do not make `<NANA_REPO>\nana\main.py` the hot path again.

2026-07-09 stream/output readiness modules are also wired through the same
runtime path:

```text
<NANA_REPO>\nana\runtime\stream_ready_status.py
<NANA_REPO>\nana\runtime\public_avatar_reaction.py
<NANA_REPO>\nana\runtime\voice_delivery.py
<NANA_REPO>\nana\cli\voice_commands.py
<NANA_REPO>\nana\core\status_stage.py
<NANA_REPO>\nana\core\status_voice.py
<NANA_REPO>\nana\runtime\public_stage_identity.py
<NANA_REPO>\nana\commands\help.py
```

These add read-only readiness, avatar cue, and voice packet planning. They do
not make live stream mode, VTS, or TTS active by themselves.

## Before / Now

Before:

- Core CLI/help/status could drag old `nana.main` or `main_cut.py`.
- `/help` advertised old phase/voice telemetry, so new chats could think the
  phase ladder was the main system.
- Stardew and osu history could pull work back into old executor stacks.

Now:

- `CORE-REWIRE-1E` is the current core baseline.
- `nana.cli.handle_text` has no `_fn(...)` lazy bridge to monolithic
  `nana.main`.
- Importing/calling the new CLI handler has been verified with
  `nana.main loaded: False`.
- Retired old phase/voice commands fail closed through
  `nana.cli.legacy_archive`.
- `/help` shows compact new-runtime commands.
- Live user runs use `python -u <NANA_REPO>\nana\__main__.py`.
- `handle_text.py` is now a thin dispatcher facade.
- `commands/registry.py` and `commands/registry_data.py` are now layered
  facades instead of a single giant flat command list.
- Current cleanup target is now narrower: lazy impl bucket size, reserved
  registry aliases, and old phase/archive command history.

## File Meanings

```text
<NANA_REPO>\nana\__main__.py
  Current runtime entry wrapper.

<NANA_REPO>\nana\cli\app.py
  CLI app/session bootstrap.

<NANA_REPO>\nana\cli\handle_text.py
  Current slash/chat command dispatcher for core runtime. It is real and now a
  thin router facade. Do not add heavy domain logic here; put it in a domain
  router or lazy impl module.

<NANA_REPO>\nana\commands\registry.py
  Compatibility facade for command suggestions/normalization.

<NANA_REPO>\nana\commands\registry_data.py
  Layered assembler for registry buckets. It preserves the old public API while
  separating live, archived, reserved, adapter, and regression-case data.

<NANA_REPO>\nana\core\
  Extracted command/status/chat/diagnostic facades that used to sit in main.

<NANA_REPO>\nana\runtime\
  Runtime state, awareness, memory spine, browser state, reconcile, logging.

<NANA_REPO>\nana\main.py
  Legacy/compatibility/remaining parking lot. Some old functions may still live
  here, but new work must not treat it as the preferred architecture.

<NANA_REPO>\nana\main_cut.py
  Intermediate split artifact. Do not build new features against it.

<NANA_REPO>\nana\main.py.cut_backup*
  Removed from the live tree on 2026-07-09 after creating
  <NANA_REPO>\backups\nana_current_20260709_132812. Do not tell new agents to inspect
  these paths as current state.

<NANA_REPO>\nana\phases\
  Mostly old phase history and legacy command surface. Do not treat the phase
  ladder as the current product architecture.
```

## Rules For New Agents

- Read `NANA_WIKI_INDEX.md`, `NANA_CURRENT_STATUS.md`, `MEMORY.md`, and this
  file before making claims about Nana core architecture.
- Do not start from `<NANA_REPO>\nana\main.py` just because it is large.
- Do not restore old `nana.main` imports or `main_cut.py` dependencies.
- If a task touches a command, look first in `nana.cli`, `nana.commands`,
  `nana.core`, and `nana.runtime`.
- If a task touches many command branches, keep `handle_text.py` thin and use
  domain routers/lazy impl modules instead of adding top-level imports.
- If a task finds a real remaining dependency on `main.py`, extract narrowly
  and verify with compile/import/command smoke. Do not do a broad rewrite.
- Historical wiki sections that mention `python -m nana.main` or direct
  `main.py` dispatch are old context unless the current status explicitly says
  otherwise.

## Verification Anchors

Known verified anchors from the current wiki:

- `CORE-REWIRE-1D`: `handle_text.py` no longer imports monolithic `nana.main`;
  missing-global audit for `handle_text()` is `0`.
- `CORE-REWIRE-1E`: `/help` hides retired old phase/voice telemetry and shows
  compact new-runtime commands.
- `STAGE-1` through `STAGE-6`: user live-ran
  `python -u <NANA_REPO>\nana\__main__.py` and verified current status panels without
  legacy `nana.main` behavior.

## Current Caveat

Some remaining cleanup candidates can still mention `main.py`. Example:
`nana.phases.phase6` is not yet a standalone module and some Phase 6 functions
still live in `<NANA_REPO>\nana\main.py`.

That is a narrow cleanup candidate, not evidence that the whole architecture is
still old.

## Command Registry Split Anchor

Historical registry snapshot from the 2026-07-09 cleanup (current counts are in
the 2026-08-12 reconciliation snapshot above):

```text
KNOWN_SLASH_COMMANDS total=4483
live=1381
archived=107
reserved=2995
known_registry=0
unknown=0
chat=0

STARDEW_STATIC_COMMANDS=6
OSU_STATIC_COMMANDS=4
COMMAND_NORMALIZATION_CASES=7
COMMAND_ROUTE_CASES=6
```

Historical verification for that 2026-07-09 snapshot:

```text
py_compile registry modules: pass
REGISTRY_EQ_OK 4483 6 4 7 6
smoke_command_registry_truth.py: 12/12
smoke_imports.py: 0 issues
smoke_core_help_surface.py: 29/29
smoke_phase_lazy_surface.py: 3/3
smoke_stage3_stage_status.py: 2/2
smoke_stage7g_public_stage_identity.py: 35/35
```
