# Nana Architecture Vision

Last updated: 2026-07-10

Purpose: this is the Level 1 architecture map. Use it before opening module
lists, file paths, phase history, or implementation notes. It exists so new
agents understand what Nana is without mistaking hundreds of implementation
modules for hundreds of product concepts.

## Current Direction

Nana remains a companion-first, streamer-capable system.

Do not pivot Nana away from stream capability. Do not rewrite the architecture
as personal-OS-only. The current decision is to keep the existing direction, but
document it in layers so stream/game/Discord do not look like Nana's core
identity.

```text
Nana Core = identity, mind, memory, runtime, relationship with Ba.
Capabilities = stream, Discord, browser, local tools, game adapters, voice/VTS.
Infrastructure = safety, config, tests, wiki, archive.
```

Stream is still allowed. It is just not the root of Nana. Treat stream as a
public/presence capability that can be optimized, paused, or run thinly without
rewriting the core identity.

## Golden Rules

When uncertain:

1. Read before answering.
2. Probe before assuming.
3. Trust runtime over memory.
4. Keep Identity stable.
5. Prefer narrow architectural changes.
6. Never infer live behavior from archived stages.
7. Preserve companion-first, streamer-capable direction unless Ba explicitly
   changes it.

## Three Documentation Levels

```text
Level 1 - Vision
  What Nana is.
  Read this file.

Level 2 - Architecture
  Which major systems exist and how they relate.
  Read NANA_ARCHITECTURE_CORE.md.

Level 3 - Implementation
  Which files, routers, adapters, bridges, and smokes implement the systems.
  Read NANA_CURRENT_CODE_TRUTH.md and subsystem docs.
```

Do not mix these levels in one mental model. `handle_text.py`, VTS, Stardew,
Discord, and `phases/` are not peers of Nana's identity. They are
implementation or capability details under the larger model.

## Level 1 Blocks

### 1. Identity

What Nana is.

Includes:
- companion
- persona
- relationship with Ba
- public identity
- boundaries of self

Identity answers: "Who is Nana?"

### 2. Mind

How Nana thinks and decides.

Includes:
- brain
- intent
- planner
- reasoning
- LLM layer
- memory as usable context

Mind answers: "What does Nana understand, choose, and say?"

### 3. Runtime

How Nana runs in the local system.

Includes:
- CLI entry
- dispatcher/router
- awareness
- autonomy cadence
- output planner
- diagnostics
- config/env gates

Runtime answers: "How does a user input become Nana behavior?"

### 4. Capability

What Nana can connect to or do through adapters.

Includes:
- Discord transport
- browser/web
- local tools
- stream/presence
- voice/TTS/VTS/OBS output
- game adapters such as Stardew and osu

Capability answers: "What external surfaces can Nana use?"

### 5. Infrastructure

What keeps Nana safe, understandable, and maintainable.

Includes:
- read-only default
- dry-run contracts
- live gates
- operator token
- kill switch
- fail-closed behavior
- smoke tests
- wiki/project memory
- legacy/archive boundaries

Infrastructure answers: "How do we keep Nana from breaking, drifting, or lying
about state?"

## Correct Mental Model

```text
NANA
├─ Identity
├─ Mind
├─ Runtime
├─ Capability
└─ Infrastructure
```

Expanded:

```text
Identity
-> Companion / Persona / Relationship with Ba / Public identity

Mind
-> Brain / Intent / Planner / Reasoning / LLM / Memory context

Runtime
-> CLI / handle_text dispatcher / routers / awareness / autonomy / diagnostics

Capability
-> Discord / Browser / Local tools / Stream / Voice presence / Game adapters

Infrastructure
-> Safety gates / config / smokes / wiki / handoff / legacy archive
```

Implementation files belong under these blocks. They are not the blocks
themselves.

## Current Stream Interpretation

Stream is a capability and presence layer:

```text
Nana can appear on stream.
Nana can use VTS/OBS/subtitles/TTS when enabled.
Nana can read or react to public chat through gates.
Nana's public behavior must not overwrite her core relationship, identity, or
private memory.
```

Therefore:

- Do not delete stream systems just because they are not core identity.
- Do not let stream systems define the whole architecture.
- Keep stream as optional/thin enough that core Nana can still run without it.
- If stream code is optimized later, optimize for low lag, low dependency, and
  graceful fallback.

## Current Implementation Anchor

The code truth currently says:

```text
<NANA_REPO>\nana\__main__.py
-> nana.cli.app.main()
-> nana.cli.handle_text.handle_text()
-> nana.core/*
-> nana.runtime/*
-> optional adapters behind gates
```

This is Level 3 evidence. See `NANA_CURRENT_CODE_TRUTH.md` before making any
claim about the current runtime path.

## Rules For New Agents

- Start from the five Level 1 blocks before reading long module lists.
- Treat stream, Discord, Stardew, osu, browser, and local tools as
  capabilities/adapters unless current code proves otherwise.
- Treat `main.py`, `main_cut.py`, and old phase history as legacy/archive unless
  a fresh code probe proves they are active.
- Do not convert this vision into a rewrite plan by itself. Architecture changes
  still need code-backed evidence and user approval.
- Do not claim Nana has "only five modules"; say Nana has five conceptual
  blocks and many implementation modules.
