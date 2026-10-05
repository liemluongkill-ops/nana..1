# Nana Public Runtime and Architecture Review

This repository contains a sanitized, credential-free Nana runtime source
snapshot together with its public architecture material. Nana is a
companion-first AI system with optional voice, avatar, presence, communication,
streaming, and game capabilities.

Current public snapshot: **2026-10-06**. The Python runtime, offline tests,
NanaBridge source, Discord bridge, ESP32-S3 Presence firmware, avatar web host,
Unity controller scripts and NanaApp web chat are available for inspection and local setup.
Private data and licensed assets are not included.

## Purpose

- Make Nana's architecture and implementation available for technical review.
- Provide a text-first baseline with optional outputs disabled in the bootstrap template.
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
notepad .env
python scripts\smoke_public_runtime.py
python -m nana
```

Bootstrap creates `.env` from the template. Set your own provider credentials
locally before starting chat. Never commit that file. See `RUNTIME.md` for component setup, safety
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
- `components/nana-app/`: private chat client, OBS view, protocol and Node tests.
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

## October Source Update

- Context Runtime contracts, compiler, budget, readiness, transport receipts and guarded tests.
- Private Web Chat transport, session ownership, voice receipts and avatar signals.
- OpenAI direct private route, session switching and the A4 excess-argument fix.
- NanaApp client and portable Core-to-Node integration tests.

The public template keeps Context modes on `legacy` and optional services OFF.
Licensed Unity bundles must be supplied separately for a rendered avatar.
Historical architecture evidence is not a claim that this checkout was tested
against live providers, audio devices, OBS or YouTube.
