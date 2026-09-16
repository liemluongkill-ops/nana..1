# Nana Public Architecture Review

This repository is a sanitized, public review snapshot of the Nana project.
Nana is a companion-first AI system with optional presence, communication,
streaming, and game capabilities.

Current public snapshot: **2026-09-16**. It reflects accepted architecture and
bounded implementation evidence through Memory v2 Phase 2 and the local 3D
avatar WebGL V1 baseline. It does not expose or mirror Nana's live runtime.

## Purpose

- Make Nana's architecture and design direction available for technical review.
- Let readers download, fork, open issues, and propose pull requests.
- Collect feedback without connecting public contributions to Nana's live runtime.

This repository is **not** Nana's canonical wiki, runtime, memory store, or live
configuration. Changes made here do not flow back into Nana automatically.

## Architecture Reading Order

1. `NANA_ARCHITECTURE_VISION.md` - high-level conceptual model.
2. `NANA_ARCHITECTURE_CORE.md` - major systems and boundaries.
3. `NANA_CURRENT_CODE_TRUTH.md` - implementation-level snapshot.
4. `NANA_3D_AVATAR_STATE.md` - bounded Unity WebGL avatar architecture.
5. Capability documents - autonomy, Discord, Presence, Stardew, singing, and
   bridge design.

## Included

- Architecture and implementation design documents.
- Data-flow and hardware wiring material.
- Capability boundaries and frozen-game design notes.
- Bounded Memory v2 Phase 2 and 3D avatar architecture notes.
- Sanitized NanaBridge source files for technical review.

## Intentionally Excluded

- Personal or runtime memory data.
- Conversation transcripts and relationship history.
- API keys, bot tokens, passwords, signing keys, cookies, and webhooks.
- Live status logs, daily checkpoints, operator tokens, and approval values.
- Private handoff prompts, internal collaboration instructions, and test
  playbooks.
- Databases, build artifacts, backups, binaries, and Git history from the
  private project.
- Personal filesystem paths, local network addresses, and account identifiers.

Placeholders such as `<NANA_REPO>`, `<LOCAL_IP>`, and `<DISCORD_TOKEN>` are
intentional redactions.

## Feedback

Use GitHub Issues for design feedback. Pull requests are proposals only and do
not modify Nana unless the owner separately reviews and imports the idea into
the private project.

## License

No open-source license is granted at this time. The repository is published for
viewing and technical review. Contact the repository owner before reusing or
redistributing project material outside GitHub's normal viewing and forking
features.
