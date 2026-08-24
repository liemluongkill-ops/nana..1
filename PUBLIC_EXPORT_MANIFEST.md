# Public Export Manifest

Snapshot date: 2026-08-24

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
