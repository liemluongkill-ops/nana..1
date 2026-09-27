# Nana Public Runtime and Architecture Review

This repository contains a sanitized, credential-free Nana runtime source
snapshot together with its public architecture material. Nana is a
companion-first AI system with optional voice, avatar, presence, communication,
streaming, and game capabilities.

Current public snapshot: **2026-09-28**. The Python runtime, offline tests,
NanaBridge source, Discord bridge, ESP32-S3 Presence firmware, avatar web host,
and Unity controller scripts are available for inspection and local setup.
Private data and licensed assets are not included.

## Purpose

- Make Nana's architecture and implementation available for technical review.
- Provide a runnable text-first baseline with side effects disabled by default.
- Let readers download, fork, open issues, and propose pull requests.

This repository is not Nana's canonical wiki, memory store, or live
configuration. Changes made here do not flow back into the private deployment
automatically.

## Runtime Quick Start

```powershell
git clone https://github.com/liemluongkill-ops/nana..1.git
Set-Location nana..1
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts\bootstrap_runtime.py
python scripts\smoke_public_runtime.py
python -m nana
```

Copy values from `.env.example` into a local `.env` before making provider
calls. Never commit that file. See `RUNTIME.md` for component setup, safety
defaults, optional assets, and build instructions.

## Architecture Reading Order

1. `NANA_ARCHITECTURE_VISION.md` - high-level conceptual model.
2. `NANA_ARCHITECTURE_CORE.md` - major systems and boundaries.
3. `NANA_CURRENT_CODE_TRUTH.md` - implementation-level snapshot.
4. `NANA_3D_AVATAR_STATE.md` - bounded Unity WebGL avatar architecture.
5. Capability documents - autonomy, Discord, Presence, Stardew, singing, and
   bridge design.

## Included Source

- `nana/`: Core Python runtime and offline/mocked tests.
- `NanaBridge/`: Stardew SMAPI bridge source.
- `components/discord-bridge/`: optional Discord transport.
- `components/presence-node/`: ESP32-S3 firmware and PC tools.
- `components/avatar-web/`: Vite browser host source.
- `components/avatar-unity-scripts/`: Unity controller/build scripts.
- `scripts/`: bootstrap, public smoke, and optional asset fetch helpers.
- Architecture, capability, wiring, and bounded evidence documents.

## Intentionally Excluded

- Personal or runtime memory data.
- Conversation transcripts and relationship history.
- API keys, bot tokens, passwords, signing keys, cookies, and webhooks.
- Live status logs, daily checkpoints, operator tokens, and approval values.
- Private handoff prompts, internal collaboration instructions, and test
  playbooks.
- Databases, build artifacts, backups, generated binaries, and private Git
  history.
- Third-party osu vendor code and model weights.
- Licensed avatar models, textures, materials, animations, and Unity builds.
- Personal filesystem paths, local network addresses, and account identifiers.

Placeholders and blank credential fields are intentional. Code that loads API
keys remains because it is required for local configuration; real key values do
not belong in this repository.

## Feedback

Use GitHub Issues for design feedback. Pull requests are proposals only and do
not modify Nana unless the owner separately reviews and imports the idea into
the private project.

## License

No open-source license is granted at this time. The repository is published for
viewing and technical review. Contact the repository owner before reusing or
redistributing project material outside GitHub's normal viewing and forking
features.
