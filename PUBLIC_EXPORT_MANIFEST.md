# Public Export Manifest

Snapshot date: 2026-10-06

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
- `components/nana-app/`: private Web Chat client, OBS entry and Node tests.
- `scripts/`: public bootstrap, smoke, and optional asset retrieval.
- `.env.example`, `requirements.txt`, and `RUNTIME.md`.

`SOURCE_EXPORT_MANIFEST.json` records path, origin label, byte count, and
SHA-256 for each published file except the manifest itself. It includes the
sanitized architecture documents and the previously published NanaBridge.

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

Historical checks for the previous snapshot, not a rerun on October source:

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

## 2026-10-06 Scope

- Added Context Runtime contracts/compiler/budget/readiness and guarded verification.
- Added private Web Chat, ownership, private voice receipts and visual signals.
- Updated private OpenAI direct routing through the A4 argument-validation fix.
- Added NanaApp source and adapted Core-to-client fixture paths for this checkout.
- Preserved the prior public portability, synthetic identity and secret-test adaptations.
- Kept provider keys, private data, raw evidence and licensed Unity assets excluded.
- Fixed private-readiness test isolation: synthetic configuration and logger replace
  reliance on a production config bytecode cache. Runtime logic is unchanged by this fix.
- Public `.env.example` explicitly disables optional launch/output services; source
  defaults remain documented separately.

The export was validated using installed dependencies in an isolated test venv.
No live provider, TTS, device, OBS or YouTube acceptance is claimed by publishing it.

## 2026-10-06 Verification

- Public bootstrap and import/disabled-capability smoke: PASS.
- Guarded Context Runtime cumulative gate: 711/711, fail 0, skip 0.
- Selected Memory Phase 2 unit/hot-path pytest group: 314/314.
- Private direct route including A4 and provider usage telemetry: 18/18.
- Startup configuration contract: 24/24.
- Ownership/auth/private protocol pytest group: 40/40.
- Core-to-NanaApp private chat loopback: 3/3; private avatar transport: 3/3.
- Focused launcher, private voice receipts, avatar signals/voice, async stream,
  public visual, public TTS model, registry, capability and lifecycle smokes: PASS.
- NanaApp Node tests: 55/55; npm audit: zero vulnerabilities; Vite build: PASS.
- Licensed Unity assets, live services and unchanged firmware/game components
  were not rebuilt or exercised by this update.

Public-only adaptations retain synthetic identities and credential-test data,
resolve runtime/component paths relative to the checkout, and use ignored local
Unity asset directories. The test-runner correction does not change product
logic, relax the audit guard or enable a real Context provider.
