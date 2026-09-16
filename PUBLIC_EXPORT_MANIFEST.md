# Public Export Manifest

Snapshot date: 2026-09-16

## Export Model

```text
Private Nana project
    -> explicit allowlist
    -> path and identifier redaction
    -> secret/privacy scan
    -> fresh public Git history
    -> this repository
```

There is no automatic reverse synchronization from this repository into Nana.

## Published Documents

- `NANA_ARCHITECTURE_VISION.md`
- `NANA_ARCHITECTURE_CORE.md`
- `NANA_3D_AVATAR_STATE.md`
- `NANA_CURRENT_CODE_TRUTH.md`
- `NANA_RUNTIME_REWIRE_STATE.md`
- `NANA_AUTONOMY_DESIGN.md`
- `NANA_DISCORD_INTEGRATION_STATE.md`
- `NANA_PRESENCE_NODE_STATE.md`
- `NANA_PRESENCE_XIAOZHI_ARCHITECTURE_REVIEW.md`
- `NANA_GAME_CAPABILITY_FREEZE_STATE.md`
- `NANA_STARDEW_ADAPTER_V2_SPEC.md`
- `NANA_STARDEW_RESEARCH_INTEGRATION_STATE.md`
- `NANA_SINGING_VOICE_RESEARCH.md`
- `NANA_B6_FINAL_WIRING.md`
- `NANA_B6_WIRING_DIAGRAM.png`
- `NANA_B6_WIRING_DIAGRAM.svg`

## Published Source

- `NanaBridge/ModEntry.cs`
- `NanaBridge/NanaBridge.csproj`
- `NanaBridge/manifest.json`

Generated `bin/` and `obj/` files are excluded.

## Private Categories Excluded

- Durable and session memory.
- Current/live status and historical checkpoints.
- Handoff, collaboration, and agent instruction documents.
- Test playbooks and operational activation commands.
- Secrets, credentials, tokens, private endpoints, and local identifiers.
- Databases, logs, caches, compiled output, and prior Git history.

The public repository must continue to be produced from an allowlist. Do not
replace this process with "copy everything and delete obvious secrets."

## Sanitization Placeholders

The export replaces private operational locations and identifiers with stable
placeholders, including:

- `<NANA_REPO>`
- `<NANA_CANONICAL_WIKI>`
- `<NANA_AVATAR_WEB>`
- `<NANA_ANIMATION_LAB>`
- `<AVATAR_SOURCE>`
- `<STARDEW_GAME>`
- `<RESEARCH_REPOS>`
- `<ESP_IDF>`
- `<USER_HOME>`
- `<LOCAL_IP>`
- `<DEVICE_MAC>`

Loopback addresses may remain because they identify no external host.

## 2026-09-16 Scope

- Added the accepted local 3D avatar WebGL V1 architecture document.
- Refreshed allowlisted architecture and capability documents from canonical
  accepted state, including the bounded Memory v2 Phase 2 boundary.
- Kept live Stream workstream notes, model-route overrides, private evidence,
  memory records, handoffs, daily status, tests, and runtime logs excluded.
- Preserved the previously sanitized NanaBridge source; no private game install
  path was reintroduced.
