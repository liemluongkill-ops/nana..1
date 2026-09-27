# Public Export Manifest

Snapshot date: 2026-09-28

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

## Published Runtime Source

- `nana/`: Python runtime plus offline and mocked test coverage.
- `NanaBridge/`: Stardew SMAPI bridge source.
- `components/discord-bridge/`: Discord file-queue transport.
- `components/presence-node/`: ESP32-S3 firmware and PC-side tools.
- `components/avatar-web/`: browser host source.
- `components/avatar-unity-scripts/`: Unity controller and editor scripts.
- `scripts/`: public bootstrap, smoke, and optional asset retrieval.
- `.env.example`, `requirements.txt`, and `RUNTIME.md`.

`SOURCE_EXPORT_MANIFEST.json` records path, origin label, byte count, and
SHA-256 for each of the 586 files in the runtime export. Architecture documents
and the previously published NanaBridge are tracked separately by Git.

## Private Categories Excluded

- Durable and session memory.
- Current/live status and historical checkpoints.
- Handoff, collaboration, and agent instruction documents.
- Test playbooks and operational activation commands.
- Secrets, credentials, tokens, private endpoints, and local identifiers.
- Databases, logs, caches, compiled output, and private Git history.
- Third-party osu vendor source and downloaded model weights.
- Licensed avatar models, textures, materials, animations, and Unity builds.

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

## 2026-09-28 Verification

- Gitleaks 8.30.1 source scan: 0 findings.
- Python syntax parse: 514 files, 0 failures.
- Public bootstrap and zero-network runtime smoke: PASS.
- Avatar web `npm ci` and production build: PASS, 0 audit vulnerabilities.
- NanaBridge MSBuild: PASS, 0 warnings and 0 errors.
- Presence firmware ESP-IDF 5.5.5 ESP32-S3 build: PASS; no flash performed.
- Selected ownership, lifecycle, privacy, Memory Phase 1/2, Stream CUM0-CUM5,
  and avatar offline regressions: PASS.

No live provider, TTS, Discord, OBS, YouTube, game-input, or hardware action was
performed as part of this public export. A build or offline smoke is not a live
production acceptance.
