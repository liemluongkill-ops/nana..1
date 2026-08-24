# Nana Stardew Adapter V2 Spec

**Status:** ACTIVE through STARDEW-V2-8G - dry-run ack, validator, movement-plan preview, and executor-intent preview are live-verified as preview-only/no-input. Python smoke total 366/366 + latest ModEntry static smoke 76/76. Do not start real executor/input without explicit user approval.  
**Created:** 2026-06-19  
**Purpose:** Lock down the contract before any code to prevent another 200-file expansion.

---

## 1. Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  OBSERVER       │────▶│  PLANNER        │────▶│  SMAPI/C# BRIDGE │
│  (read-only)    │     │  (decision)     │     │  (executor)      │
└─────────────────┘     └─────────────────┘     └──────────────────┘
       │                       │                        │
       ▼                       ▼                        ▼
  reads state            outputs JSON              runs in-game
  no input              no movement               pathfinding/action
```

**Rule:** Python Nana is NEVER allowed to send keyboard input or move the mouse.

---

## 2. File Structure

Initial scaffold is 6 files + 1 data directory:

```
<NANA_REPO>/nana/game/stardew/
├── __init__.py           # Entry point, exports only
├── observer.py            # Read-only state reader (Phase 2 ✅)
├── planner.py            # High-level decision maker (Phase 3 ✅)
├── commands.py           # V2 command handlers (added Task 6B)
├── data/
│   └── observer_state.json   # File-based observer source (Phase 2 ✅)
└── bridge/
    ├── __init__.py       # Bridge interface (Phase 4 ✅)
    └── schema.py         # JSON schemas
```

Smoke tests:
```
<NANA_REPO>/smoke_stardew_adapter_v2_skeleton.py         (31 tests)
<NANA_REPO>/smoke_stardew_adapter_v2_command_surface.py  (62 tests)
<NANA_REPO>/smoke_stardew_adapter_v2_observer.py          (41 tests + 7 TTL env tests)
<NANA_REPO>/smoke_stardew_observer_state_writer.py        (writer, 28 tests)
<NANA_REPO>/smoke_stardew_adapter_v2_goal_preview.py      (41 tests)
<NANA_REPO>/smoke_stardew_adapter_v2_live_command_routing.py (26 tests)
<NANA_REPO>/smoke_stardew_adapter_v2_bridge_draft.py      (64 tests)
<NANA_REPO>/smoke_stardew_adapter_v2_bridge_inbox_ack.py  (51 tests)
<NANA_REPO>/smoke_stardew_adapter_v2_bridge_modentry_static.py (33 static tests)
Python total: 366 tests. ModEntry static: 33 tests.
```

**Maximum during initial build:** 6 source files + 1 data directory.

---

## 3. Forbidden Modules / Behaviors

| Forbidden | Reason |
|-----------|--------|
| `pyautogui` | Python direct input |
| `keyboard` module (key sending) | Python direct input |
| `win32api` key simulation | Python direct input |
| `SendInput` / `keybd_event` | Python direct input |
| Pathfinding algorithms in Python | Move to SMAPI/C# |
| Micro-pulse / held-key loops | Move to SMAPI/C# |
| Tile movement executor in Python | Move to SMAPI/C# |
| Inventory slot manipulation in Python | Move to SMAPI/C# |

**Staleness config:** `NANA_STARDEW_OBSERVER_STALE_AFTER_SEC` env var (default: 60, range: 1–300s).
Observer reads this at module-load time. Invalid values fall back to 60. SMAPI bridge or mock writer updates the state file; observer checks `timestamp` age against the threshold.

**Hard rule:** If it moves a character or clicks something in the game, it does NOT belong in Python.

---

## 4. JSON State Schema (Observer → Planner)

Observer reads game state and emits:

```json
{
  "timestamp": 1750350600.123,
  "zone": "Farm",
  "player_tile": {"x": 72, "y": 14},
  "player_health": 100,
  "player_energy": 276,
  "time_of_day": 900,
  "day_of_month": 5,
  "season": "spring",
  "year": 1,
  "weather": "sunny",
  "inventory": [
    {"slot": 0, "item": "Parsnip Seeds", "count": 15},
    {"slot": 1, "item": "Hoe", "count": 1}
  ],
  "tool_equipped": "hoe",
  "nearby_objects": [
    {"tile": {"x": 73, "y": 14}, "type": "hoeable_soil"}
  ]
}
```

**Observer NEVER sends input. It only reads.**

---

## 5. JSON Command Schema (Planner → Bridge)

Planner outputs intent as JSON. Safety gates always return noop with a reason.
Fresh state returns structured read-only plan with context:

```json
{
  "timestamp": 1750350610.456,
  "command": "noop",
  "reason": "observer_ready",
  "observer_available": true,
  "zone": "Farm",
  "player_tile": {"x": 72, "y": 14},
  "energy": 200,
  "time_of_day": 900,
  "date": "Spring 1, Year 1",
  "suggested_next": "await_goal"
}
```

Safety-gate noop examples:

```json
{"timestamp": 1750350610.456, "command": "noop", "reason": "state_unavailable", "observer_available": false}
{"timestamp": 1750350610.456, "command": "noop", "reason": "state_stale", "observer_available": false, "zone": "Farm"}
{"timestamp": 1750350610.456, "command": "noop", "reason": "low_energy", "observer_available": true, "energy": 5}
{"timestamp": 1750350610.456, "command": "noop", "reason": "late_time", "observer_available": true, "time_of_day": 2400}
```

`suggested_next` values (read-only hints only, never executed):
- `await_goal` — normal daylight, healthy energy, in a known zone
- `rest` — energy < 20
- `conserve` — energy < 60
- `wind_down` — time >= 21:00
- `await_zone` — zone unknown

Or for an actual action (Phase 6 future):

```json
{
  "timestamp": 1750350610.456,
  "command": "move_to",
  "map": "Farm",
  "target": {"x": 78, "y": 15},
  "priority": 1,
  "reason": "move to hoeable soil for planting"
}
```

**noop is the default output.** Planner returns noop when:
- Observer state is unavailable
- Observer state is stale (timestamp too old)
- Player energy < safety threshold
- Time is past safe action window
- No valid action available

Supported command types:

| Command | Fields | Executor Responsibility |
|---------|--------|------------------------|
| `noop` | `reason` | No action needed / unsafe / stale |
| `move_to` | `map`, `target{x,y}`, `priority` | Pathfind and walk |
| `interact` | `map`, `target{x,y}`, `action` | Walk to and interact |
| `use_tool` | `tool`, `target{x,y}` | Equip and use tool |
| `water` | `target{x,y}` | Walk to and water |
| `plant` | `seed`, `target{x,y}` | Walk to and plant |
| `harvest` | `target{x,y}` | Walk to and harvest |
| `check_time` | - | Query time state only |
| `check_inventory` | `slot` (optional) | Query inventory only |

**Planner NEVER executes. It only decides and emits JSON.**

---

## 5b. Goal Preview (Task 8B - Read-Only)

**Command:** `/stardew-goal-preview <goal text>`

Parses a freeform goal string and returns a structured preview. Does NOT execute anything. `command` is always `noop`. `can_execute`, `input_allowed`, `executor_connected` are always `false`.

**Intent mapping:**

| Goal text contains | `preview_intent` | Extra fields |
|---|---|---|
| `đi tới X Y` / `move to X Y` / `go to X Y` | `move_to` | `target_tile: {x, y}` |
| `water` / `tưới` / `crops` / `garden` | `water_crops` | — |
| `status` / `inspect` / `check` / `xem` | `inspect_state` | — |
| empty/whitespace | `goal_missing` | — |
| anything else | `unknown_goal` | — |

**Safety gates block preview intent when state is unreliable:**
- `state_unavailable` — no state file
- `state_stale` — state age > staleness threshold
- `low_energy` — energy < 10
- `late_time` — time > 22:00

**Bridge Command Draft (Task 8C — STARDEW-V2-8C):**

Every goal_preview result includes a `bridge_command_draft` dict. This shows what Python would ask the future SMAPI/C# executor to do — but never executes, never writes queue files, never sends input.

```json
{
  "schema_version": "stardew_bridge_command_v1",
  "command": "move_to",
  "map": "Farm",
  "target": {"x": 70, "y": 18},
  "source": "goal_preview",
  "dry_run": true,
  "forwarded": false,
  "can_execute": false,
  "input_allowed": false,
  "reason": "goal_preview_move_to"
}
```

**Intent → Draft reason mapping:**

| `preview_intent` | `draft.command` | `draft.reason` |
|---|---|---|
| `move_to` | `move_to` | `goal_preview_move_to` |
| `water_crops` | `noop` | `goal_preview_water_crops_requires_observer_crop_tiles` |
| `inspect_state` | `noop` | `goal_preview_inspect_state_is_read_only` |
| `unknown_goal` | `noop` | `goal_preview_unknown_goal` |
| `goal_missing` | `noop` | `goal_preview_goal_missing` |
| safety gate | `noop` | matches gate reason |

Safety fields always: `dry_run=true`, `forwarded=false`, `can_execute=false`, `input_allowed=false`.

**Example preview (fresh state + "đi tới 70 18"):**

```json
{
  "timestamp": 1781959800.123,
  "command": "noop",
  "reason": "goal_preview_only",
  "goal": "đi tới 70 18",
  "preview_intent": "move_to",
  "target_tile": {"x": 70, "y": 18},
  "observer_available": true,
  "can_execute": false,
  "input_allowed": false,
  "executor_connected": false,
  "zone": "Farm",
  "player_tile": {"x": 64, "y": 21},
  "energy": 270,
  "time_of_day": 700,
  "date": "Spring 16, Year 1",
  "next_step": "await_smapi_executor",
  "bridge_command_draft": {
    "schema_version": "stardew_bridge_command_v1",
    "command": "move_to",
    "map": "Farm",
    "target": {"x": 70, "y": 18},
    "source": "goal_preview",
    "dry_run": true,
    "forwarded": false,
    "can_execute": false,
    "input_allowed": false,
    "reason": "goal_preview_move_to"
  }
}
```

---

## 6. Safety Gates

### Gate 1: Observer Read-Only
```
IF action == "send_key" OR "mouse_move" OR "click":
    REJECT and log warning
    DO NOT forward to bridge
```

### Gate 2: Bridge Forwarding Guard
```
IF command not in ALLOWED_COMMANDS:
    REJECT with schema validation error
    DO NOT forward unknown commands
```

### Gate 3: Energy Check Before Action
```
IF player_energy < 10:
    SET priority = -1 (defer until rest)
    ADD warning to output JSON
```

### Gate 4: Time Check Before Long Routes
```
IF time_of_day > 2340:
    BLOCK all movement commands
    RETURN defer response with reason "night_time"
```

---

## 6. Dry-Run Inbox/Ack Contract (Task STARDEW-V2-8D)

**Default behaviour — no inbox is written:**
```
/stardew-goal-preview đi tới 70 18
```
Prints preview + bridge draft. No file is written.

**Explicit dry-run — writes inbox:**
```
/stardew-goal-preview đi tới 70 18 --bridge-dry-run
```
Writes `<NANA_REPO>/nana/game/stardew/data/command_inbox.json`. SMAPI reads it and writes `command_ack.json`. **No game execution occurs.**

### 6a. Inbox Message Schema (Python → SMAPI)

Written by Python when `--bridge-dry-run` is given. SMAPI reads and acknowledges.

```json
{
  "schema_version": "stardew_bridge_command_v1",
  "protocol": "dry_run_ack_v1",
  "command_id": "1781966667_dryrun",
  "command": "move_to",
  "map": "Farm",
  "target": {"x": 70, "y": 18},
  "source": "goal_preview",
  "dry_run": true,
  "ack_only": true,
  "forwarded": true,
  "can_execute": false,
  "input_allowed": false,
  "preview_intent": "move_to",
  "goal_text": "đi tới 70 18",
  "reason": "goal_preview_move_to",
  "timestamp": 1781966667
}
```

**Contract invariants (always true):**
- `dry_run: true` — this is a dry-run, not a real command
- `ack_only: true` — SMAPI must acknowledge only, not execute
- `forwarded: true` — inbox was written by Python planner
- `can_execute: false` — execution gate is locked
- `input_allowed: false` — input gate is locked

### 6b. Ack Message Schema (SMAPI → Python)

Written by SMAPI after reading the inbox. Python reads it via `read_ack()`.

```json
{
  "schema_version": "stardew_bridge_ack_v1",
  "protocol": "dry_run_ack_v1",
  "command_id": "1781966667_dryrun",
  "status": "acknowledged_dry_run",
  "executed": false,
  "input_allowed": false,
  "reason": "dry_run_ack_only",
  "timestamp": 1781966668
}
```

**Contract invariants (always true):**
- `executed: false` — SMAPI must not execute the command
- `input_allowed: false` — SMAPI must not send input
- `status: acknowledged_dry_run` — SMAPI confirmed receipt

### 6c. File Paths

| File | Direction | Written by |
|------|-----------|-----------|
| `command_inbox.json` | Python → SMAPI | `write_inbox()` in `bridge/__init__.py` |
| `command_ack.json` | SMAPI → Python | `ReadCommandInboxAndWriteAck()` in `ModEntry.cs` |

Both files are under `<NANA_REPO>/nana/game/stardew/data/`.

### 6d. ModEntry.cs Stub

`ReadCommandInboxAndWriteAck()` is wired into the game tick loop.
- Reads `command_inbox.json` if it exists
- Verifies `dry_run == true` and `ack_only == true`
- Writes `command_ack.json`
- **No movement, no pathfinding, no game state mutation**
- dotnet SDK not on this machine — must rebuild on machine with .NET 6 SDK

### 6e. Ack Reader Diagnostics (Task STARDEW-V2-8D.2)

8D.2 does not add execution. It adds diagnostics so the user can see whether
SMAPI is reading the inbox and writing the ack.

Startup logs expected after rebuilding/copying `NanaBridge.dll`:

```text
[NanaBridge] 8D.2 Bridge ack reader active
[NanaBridge] 8D.2 command inbox path: <NANA_REPO>/nana/game/stardew/data/command_inbox.json
[NanaBridge] 8D.2 command ack path: <NANA_REPO>/nana/game/stardew/data/command_ack.json
```

Per-command logs:

```text
[NanaBridge] 8D.2 Inbox found - dry_run=<...> ack_only=<...> command_id=<...> command=<...>
[NanaBridge] 8D.2 Ack written: command=<...> id=<...>
```

Error diagnostics:
- malformed inbox JSON logs a parse error and content preview
- unexpected failures log exception type/message
- tick loop must not crash

Observer state now also exposes:

```json
{
  "bridge_ack_reader_active": true,
  "command_inbox_path": "<NANA_REPO>/nana/game/stardew/data/command_inbox.json",
  "command_ack_path": "<NANA_REPO>/nana/game/stardew/data/command_ack.json",
  "last_ack_status": "acknowledged_dry_run",
  "last_ack_error": null
}
```

Status: live-verified 2026-06-21 as dry-run ack diagnostics; no movement/no input.

---

## 7. Acceptance Criteria for Skeleton

Before adding any new module, all criteria must pass:

- [ ] `observer.py` imports WITHOUT touching `keyboard`, `pyautogui`, `win32api`
- [ ] `planner.py` outputs valid JSON matching schema; goal_preview() returns structured preview
- [ ] `bridge/__init__.py` has write_inbox() and read_ack() for dry-run inbox/ack contract
- [ ] `schema.py` contains schemas for command, observer state, inbox, and ack
- [ ] No file in the scaffold exceeds 350 lines (Task 8D: bridge grew)
- [ ] `py_compile` passes for all scaffold files
- [ ] A single smoke test can import all modules without error
- [ ] No new smoke file without a corresponding feature change

---

## 8. Research Repos: Reference Only

| Repo | What to Study | What to NOT Copy |
|------|---------------|------------------|
| `stardew-mcp-main/` | Command queue pattern, WebSocket API shape, C# executor interface | Do not copy C# code into Python |
| `stardew-valley-water-bot-main/` | Watering logic flow, crop awareness | Do not copy movement code |
| `Farmtronics-main/` | Robot API concepts only | Too large to port, reference only |
| `stardew-valley-bot-framework-main/` | Abstraction ideas | Not for direct use |

**Rule:** Research repos are for understanding architecture. Any code that goes into `<NANA_REPO>/nana/game/stardew/` must be written fresh to this spec.

---

## 9. No Expansion Without Spec Update

**Any addition beyond the initial 6 files MUST update this spec first.**

To add a new module:
1. Write the spec section for it
2. Get explicit approval
3. Implement only after spec is updated

This spec IS the lock. Update it before you grow.

---

## 10. Phases (When to Expand)

| Phase | Scope | Files | Status |
|-------|-------|-------|--------|
| Phase 0 | This spec | 1 doc | ✅ Done |
| Phase 1 | Scaffold skeleton | 6 files | ✅ Done |
| Phase 2 | Observer file-based read | +1 file | ✅ Done (Task 7A) |
| Phase 2b | Mock state writer (dev tool) | 1 file | ✅ Done (Task 7B) |
| Phase 3 | Planner stub | 1 file | ✅ Done (Task 6B) |
| Phase 4 | Bridge interface stub | 1 file | ✅ Done (Task 6B) |
| Phase 5 | SMAPI writer (game -> file) | +N | ✅ Done (Task 7C-A)
| Phase 5b | Read-only bridge health status | existing files | ✅ Done (Task 7C-B) |
| Phase 5c | Dry-run inbox/ack contract | existing files + ModEntry | ✅ Done (Task 8D/8D.1) |
| Phase 5d | Ack reader diagnostics | ModEntry + observer fields | ✅ Done, awaiting user live test (Task 8D.2) |
| Phase 6 | SMAPI connection (executor) | +N (future) | Pending |

**Do not skip phases. Do not add Phase 5 features before Phase 1-4 are proven.**

---

## 5a. NanaBridge SMAPI Writer (Phase 5)

**Location:** `<NANA_REPO>/NanaBridge/ModEntry.cs` (1600+ lines, already installed at `$(StardewGamePath)\Mods\NanaBridge`)

**What it does:**
- Read-only telemetry from Stardew Valley via SMAPI.
- Already exports `state.json` (heavy/full) and `state_light.json` (light/frequent) to the mod's `data/` directory.
- **New (Task 7C-A):** Added `ExportObserverState()` which writes a Python-adapter-compatible JSON to `<NANA_REPO>/nana/game/stardew/data/observer_state.json`.

**Observer-compatible format written by NanaBridge:**

```json
{
  "timestamp": 1781934702.375,
  "zone": "Farm",
  "player_tile": {"x": 64, "y": 20},
  "player_energy": 200,
  "time_of_day": 630,
  "day": 16,
  "season": "spring",
  "year": 1
}
```

**Config env vars:**
| Variable | Default | Meaning |
|----------|---------|---------|
| `NANA_OBSERVER_STATE_PATH` | `<NANA_REPO>/nana/game/stardew/data/observer_state.json` | Output path |
| `NANA_OBSERVER_EXPORT_TICKS` | `60` | Ticks between writes (~1s at 60fps) |
| `NANA_BRIDGE_LIGHT_EXPORT_TICKS` | `12` | Light snapshot interval (existing) |

**Safety:** No keyboard, mouse, input, movement, or pathfinding APIs used. `allowsInput: false` and `bridgeCanPressKeys: false` in all exports.

**Task 7C-B bridge health:** `/stardew-bridge-status` now reports the health of
the read-only observer bridge, not an executor connection. `Connected: true`
means the observer file is fresh and readable. It does **not** mean Python can
execute game actions.

Expected bridge status fields:

```text
Connected: true/false
Source: SMAPI observer_state.json
Executor: read_only_observer
Executor connected: false
Input allowed: false
Can execute commands: false
Observer state file: <NANA_REPO>/nana/game/stardew/data/observer_state.json
Observer available: true/false
State stale: true/false
Last write age: <seconds>
Stale after: <seconds>
Zone: <map>
```

**Build:** Requires .NET 6 SDK. Run `dotnet build` from `<NANA_REPO>/NanaBridge/`. Copy output `NanaBridge.dll` + `manifest.json` to `Stardew Valley/Mods/NanaBridge/`.

---

## 11. Exit Criteria (When V2 is "Done Enough")

V2 Stardew adapter is complete when:
- [x] Observer can read basic farm state from file (Phase 2 ✅ - Task 7A)
- [x] Planner can emit valid command JSON (Phase 3 ✅ - Task 6B)
- [x] Bridge interface exists (Phase 4 ✅ - Task 6B)
- [x] Nana core does not crash on import
- [x] Zero Python keyboard/mouse input code in the adapter
- [x] Smoke tests pass: 169 tests total across 4 smoke scripts
- [x] Observer staleness threshold configurable via NANA_STARDEW_OBSERVER_STALE_AFTER_SEC (default 60s, 1-300s clamp)
- [x] `/stardew-bridge-status` reports read-only SMAPI observer health without enabling execution

**Dev tool for testing:** `<NANA_REPO>/stardew_observer_state_writer.py`
```bash
python <NANA_REPO>/stardew_observer_state_writer.py --once  # write fresh state
python <NANA_REPO>/stardew_observer_state_writer.py --zone Mine --energy 50  # custom state
```

**Observer source path:** `<NANA_REPO>/nana/game/stardew/data/observer_state.json`

**SMAPI source:** `<NANA_REPO>/NanaBridge/` — NanaBridge writes observer-compatible JSON at runtime when game is loaded. Build with .NET 6 SDK, copy to `Stardew Valley/Mods/NanaBridge/`.

**Not yet included:** screen capture, pathfinding, in-game action execution. These belong to Phase 6 / future work.
