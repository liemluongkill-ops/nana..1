# Nana Game Capability Freeze State

Last updated: 2026-07-12

Decision: `OWNER-DIRECTED-GAME-CAPABILITY-FREEZE`

Status: **FROZEN / PRESERVED / NOT ABANDONED**

Requested by: **Ba (owner)**

Current project priority: **Nana Core**

## Decision

Game capability development is frozen so project effort can return to Nana's
Identity, Mind, Memory, Runtime, relationship behavior, and core reliability.

This is a development lock. It is not permission to delete game code, tests,
documents, backups, safety gates, or working capability state.

## Frozen Scope

Stardew Valley:

- Observer, planner, bridge, SMAPI/C# executor, and approval-token work.
- Same-map movement, pathing, animation, obstacle handling, and map transition
  work.
- Farming autonomy, including planting, watering, harvesting, clearing, tool
  use, inventory, shop, and farming action loops.
- NPC avoidance, route expansion, location expansion, tuning, DLL rebuild/copy,
  smoke expansion, and live game testing.

osu:

- Observer/parser/model/vendor integration and game-specific stream behavior.
- Boss, Cursor Dance, calibration, aim, timing, slider/spinner, input, score,
  executor, and live-play work.
- New phases, tuning, research integration, smoke expansion, and live game
  testing.

All other game-specific branches are also out of the active task queue unless
Ba explicitly names and reopens one.

## Freeze Rules

1. Do not schedule game work as the next Nana task.
2. Do not add game features, phases, tuning, tests, builds, deploys, or live
   runs while this decision is active.
3. Do not restore retired Python movement, deleted patches, or old phase
   ladders.
4. Do not reinterpret installed/configured/auto-on as live play or completion.
5. Do not delete preserved game artifacts merely because the branch is frozen.
6. Reopen only after a new explicit owner instruction naming the capability to
   resume. General requests such as "continue Nana" do not unfreeze games.

Core-protection exception: a narrow fail-closed isolation fix may be made if a
frozen adapter prevents Nana Core from importing or starting. Such a fix must
not improve or extend game behavior.

## Runtime Boundary

No runtime code or adapter configuration was changed by this freeze decision.
The existing adapters remain optional and lazy. An explicit environment value
can still report an adapter as enabled or configured; that does not reopen its
development branch and does not prove real input is active.

For a core-only local session, omit old game-enable commands. A strict temporary
CMD profile is:

```bat
set NANA_STARDEW_ADAPTER_ENABLED=0 && set NANA_STARDEW_ADAPTER_AUTO_ZONE_ENABLED=0 && set NANA_OSU_ADAPTER_ENABLED=0 && set NANA_OSU_ADAPTER_AUTO_ZONE_ENABLED=0 && python -u <NANA_REPO>\nana\__main__.py
```

This profile is operational guidance only; it is not a code default change.

## Preserved Stop Points

Stardew technical closeout remains preserved in:

```text
<NANA_REPO>\nana\docs\NANA_STARDEW_MOVEMENT_STATE.md
<NANA_CANONICAL_WIKI>\NANA_STARDEW_MOVEMENT_STATE.md
```

The runtime-tree copy contains the latest 8T.2 technical snapshot. It records
8T.2 as smoke-verified and installed, with the post-fix live transition retest
still pending. The older OneDrive movement document remains a historical
aggregate. Both are frozen; do not merge or advance them during core work.

osu code and historical handoffs remain preserved under
`<NANA_REPO>\nana\game\osu` and the existing wiki history. A configured or `auto_on`
status is not a current live-play claim.

Farming remains unfinished capability work inside Stardew. It is frozen with
the rest of Stardew and must not be presented as implemented.

## Active Direction

While this decision is active, new planning and handoff sections should point
only to Nana Core. Game history may be read for audit, but it must not drive the
next-task queue.
