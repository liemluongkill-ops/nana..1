# Nana Singing Voice Research

Last checked: 2026-08-13 (workstream priority update; research facts unchanged)

Status: **PRIMARY WORKSTREAM / research only / PC-only / no ESP32 action**.

This document records the first technology decision for giving Nana a singing
capability while preserving her existing voice character. The ESP32/Presence
scope remains `ESP32-OWNER-HOLD-1` (`WAITING_OWNER` / `DEFERRED`) and is not
opened by this research.

## Current Workstream Boundary

This is the active owner focus while Presence is intentionally paused. The
branches are independent: Singing owns its own plans, voice profiles, model
adapters, artifacts, and evaluation reports; Presence owns hardware, firmware,
transport, and physical acceptance. Neither branch may silently change the
other branch's status or queue.

Priority means scheduling, not blanket execution permission. The current
research gates remain explicit: no singing model is installed, no provider/API
call is implied, no audio is routed to ESP32, and autonomous singing remains
disabled until Ba approves a concrete experiment.

## Goal

Nana should be able to sing with a recognizable version of her current timbre,
while normal conversation keeps using the existing path:

```text
Nana reply -> ElevenLabs TTS -> decoded PCM16/16 kHz -> Presence node
```

Singing needs a different path because ordinary TTS controls spoken prosody;
it does not reliably provide a chosen melody, sustained notes, vibrato, or
musical timing.

The proposed singing path is:

```text
lyrics + melody / guide vocal
        -> singing source
        -> singing voice conversion using Nana reference voice
        -> WAV master on PC
        -> optional playback adapter later
```

The singing path must stay separate from the conversation TTS queue. A failed
song render must never block or replace Nana's ordinary replies.

## Architectural Correction

The first proposal was a useful **Seed-VC prototype**, but it was too narrow
to be called Nana's singing system. Seed-VC takes an existing performance and
changes its timbre; it does not decide melody, rhythm, lyric-to-note alignment,
breathing, dynamics, or expression.

The durable boundary is a capability with replaceable backends:

```text
Nana Core
  -> Song Intent
  -> Musical Plan
  -> Singing Capability
       -> Seed-VC backend
       -> DiffSinger backend
       -> Eleven Music backend
       -> RVC backend
  -> Singing Artifact
  -> Final Mix / playback adapter
```

Core owns the intent and the capability contract. It should not import model
internals, CUDA code, checkpoint paths, or backend-specific preprocessing.
The backend registry may still report availability, capabilities, and quality
profiles; “Core does not know the backend” means no domain coupling, not that
the runtime is forbidden from selecting an available adapter.

### Contract Sketch

```text
SongRequest
  request_id
  lyrics + language
  musical_plan_id
  style + expression
  voice_profile_id
  output_policy: pc_only | approved_playback

MusicalPlan (first-class, versioned artifact)
  plan_id + version
  key, bpm, meter
  melody/notes or guide_audio
  lyric-to-note timing and structure
  phrasing, dynamics, expression, provenance
  status: draft | reviewed | approved | superseded

SingingArtifact (immutable render)
  artifact_id
  audio master
  sample rate / channels / duration
  backend id + version
  voice profile id + version
  voice identity contract id + version
  musical plan id
  source request id
  provenance and evaluation report
  identity gate: pending | pass | reject
  status: rendered | evaluated | accepted | rejected
```

`MusicalPlan` is deliberately separate from `SongRequest`: Nana can revise a
melody or arrangement and create `MusicalPlan v2` without pretending that the
song request itself changed. A plan can be reviewed, rejected, or superseded
before any vocal backend runs.

`SingingArtifact` is append-only and immutable. A failed or disliked render is
never edited in place; a retry creates a new artifact with a new backend/config
provenance and points back to the same request and plan (or to a newer plan).
This preserves an evidence chain such as:

```text
SongRequest -> MusicalPlan v3 -> VoiceProfile v2 -> SeedVC v1.2
            -> SingingArtifact #184 -> Evaluation
```

The contract must allow a guide vocal today and a notes-driven request later.
That keeps the Seed-VC experiment useful without pretending it already solves
autonomous singing.

### Voice Identity Contract

`Voice Identity Contract` is the stable continuity boundary between Nana's
core identity and replaceable voice assets. It is not a model and it is not a
single checkpoint file. It defines the canonical speaking anchor, the allowed
singing relationship to that anchor, and the acceptance rules every singing
backend must satisfy.

```text
Nana Identity
  persona + memory + behavioral continuity
        |
        +-- Voice Identity Contract (stable, owner-approved)
              +-- canonical speaking voice anchor
              +-- singing voice compatibility constraints
              +-- identity-consistency evaluation policy
              +-- accepted/rejected profile lineage
                    |
                    +-- Nana Voice Profile v1, v2, ...
```

The contract remains stable while a `VoiceProfile` can be replaced,
superseded, or rejected. A profile is an implementation asset; it cannot
redefine Nana's voice, persona, or continuity. Every profile records which
contract version it claims to satisfy.

For every singing candidate, compare a canonical Nana speaking sample and the
singing render under the same evaluation protocol. The question is binary:
`same Nana?`. `PASS` permits the artifact to continue to the ordinary quality
gates; `REJECT` marks identity drift as a hard failure even when pitch, lyrics,
or production quality are good. A backend never becomes the definition of
Nana's voice.

### Nana Voice Profile

`Nana Voice Profile` is a versioned capability asset, not Nana's core identity
and not a loose `reference.wav`. It should eventually contain:

- profile/version identifier and provenance/consent record;
- clean speaking references;
- clean singing references when available;
- preferred singing register and safe pitch range;
- language/pronunciation notes;
- artifact limits and backend compatibility metadata.

Core asks for a profile by identifier. Backends consume the profile through a
small adapter interface, so replacing a model does not rewrite Nana's persona,
memory, or conversation state.

The profile remains a capability asset, never the source of Nana's identity.
Changing `VoiceProfile v1` to `VoiceProfile v2` may change a render's acoustic
color, but it must not change Core identity, memory, permissions, or persona.

### Availability And Permission

Capability availability is not permission to execute it. The capability
registry exposes separate lifecycle fields:

```text
available   -> backend/model can be found
configured  -> isolated environment and profile are valid
allowed     -> execution policy permits a run
approved    -> Ba approved this specific experiment/request
running     -> a render is in progress
completed   -> artifact was produced
failed      -> render failed with evidence
```

The current research checkpoint is:

```text
available=false   # no singing model has been installed yet
configured=false
allowed=false
approved=false
identity_compatible=false   # no profile has passed the Voice Identity Contract
running=false
```

After an isolated backend is installed and verified, `available` and
`configured` may become true, but `allowed` and `approved` remain false until
Ba explicitly opens and approves the experiment. No autonomous singing is
permitted during research.

`identity_compatible` is an evaluation result, not a permission shortcut. It
can become true only after a candidate passes the Voice Identity Contract's
speaking-versus-singing A/B gate. A backend may be available and configured
while still failing identity compatibility.

### Evaluation Boundary

“Sounds nice” is insufficient. A singing render is evaluated independently on:

1. Voice Identity Contract speaking-versus-singing A/B consistency (hard gate);
2. pitch and beat/key alignment;
3. lyric intelligibility;
4. naturalness, phrasing, breath, and expression;
5. artifacts such as metallic tone, buzz, pitch jumps, or source leakage;
6. consistency across repeated renders;
7. render time, VRAM, output format, and failure behavior.

For Nana, identity consistency is a release gate, not a cosmetic score. A
great song that sounds like a different singer is a singing-capability failure
and must be rejected, regardless of its other scores.

## Options Compared

### 1. Eleven Music

Eleven Music is a text-to-music service. The official documentation describes
vocals or instrumental output, multilingual generation, section/lyrics
editing, audio reference, a 3-second to 5-minute generation range, WAV/MP3
output, and an API.

It is the fastest way to make a complete song, but it is not the strongest
choice when the requirement is **the same Nana voice**. An audio reference
guides overall sound, production style, instrumentation, tempo, and mood; it
does not promise an exact custom-voice timbre conversion. The reference upload
is also screened under the provider's copyright rules. Account and music terms
still apply independently of personal use.

**Use:** fast song sketches and backing tracks.

**Do not make it Nana's identity layer.**

### 2. Seed-VC singing voice conversion

Seed-VC is the strongest first candidate for this project. Its released models
support zero-shot singing voice conversion from a short reference voice, and
the project documents an offline 44.1 kHz singing model with F0 conditioning.
The input supplies the melody and performance; the reference supplies the
target timbre.

Relevant controls include F0 conditioning, diffusion steps, pitch shift, and
crossfade/context settings. The project also has a real-time VC mode, but the
first Nana experiment should be offline so quality can be judged without
latency pressure.

**Use:** convert a clean guide vocal or humming performance into Nana's voice.

**Tradeoff:** GPL-3.0 code and separately downloaded model weights must be
reviewed before redistribution. Keep it in an isolated environment and keep
voice references local.

### 3. RVC

RVC is a mature, widely used voice-conversion toolkit. Its code is MIT-licensed
and it can train a speaker model from a relatively small clean dataset. It is
a good fallback if zero-shot Seed-VC does not preserve Nana's timbre well
enough.

It generally requires more setup and model/data decisions, and a quick model
can produce metallic consonants, pitch drift, or source leakage. It is not the
first experiment because the user goal is to learn the technology before
committing to a training dataset.

### 4. DiffSinger

DiffSinger is a singing voice synthesis system driven by musical controls such
as notes and lyrics. It is the long-term option when Nana should sing from text
and a score without a human guide vocal.

It is not the quickest route to the current Nana timbre: a convincing custom
voice normally requires a properly prepared singing dataset, phoneme/score
alignment, and a dedicated model. Treat it as a later controlled-synthesis
branch, not the first smoke test.

### 5. GPT-SoVITS

GPT-SoVITS is strong for few-shot TTS and voice conversion, but it is
conversation/TTS-oriented rather than singing-first. It may be useful for a
future spoken-voice fallback, but it is not the primary singing candidate.

## Decision

Use a **two-stage PC-only experiment inside the future capability boundary**:

1. Generate or obtain a short clean guide vocal with the desired lyrics and
   melody. A hum or Ba's own singing can be used for a private test; no
   copyrighted lyrics need to be supplied by Nana.
2. Run Seed-VC's singing model with F0 conditioning and a clean Nana reference
   clip. Compare the result against the reference for timbre, pitch, vowel
   clarity, and artifacts.

This validates the first `SeedVCBackend`; it does not close the broader
`SingingCapability` contract.

Keep Eleven Music as an optional comparison branch for complete-song generation
and arrangement. Do not replace the current ElevenLabs speech voice or route
music through the ESP32 until the PC WAV result is accepted.

## Voice And Beat Compatibility

### Will it still sound like Nana?

Seed-VC is a credible way to preserve Nana's timbre because the guide vocal
provides the musical performance while a short clean Nana reference provides
the target speaker characteristics. It is not a guarantee of perfect identity:
the result depends strongly on reference cleanliness, the guide singer's
pronunciation, pitch range, breath, and the model's ability to handle the
language. Expect a recognizable Nana-like color first, then measure whether it
is good enough before training anything.

The most reliable reference is a dry, isolated Nana vocal with no beat, reverb,
or speaker bleed. Use several short references only after the single-reference
smoke test is understood.

### Will it match the beat's key?

Yes, if the guide melody is already in the beat's key. Seed-VC's singing model
uses F0 conditioning, so it can transfer the guide performance's notes and
timing while changing timbre. It does **not** analyze a full beat and compose a
melody automatically.

The practical signal path is:

```text
beat WAV
  -> estimate BPM/key (external analysis)
  -> create a guide melody in that key
  -> record/hum/sing the guide over the beat
  -> separate guide vocal from accompaniment if needed
  -> Seed-VC SVC with F0 conditioning + Nana reference
  -> mix converted vocal back with the beat
```

For a guide that is off-key, add a pitch correction/key-shift step before
conversion. Do not blindly apply a global semitone shift: first identify the
beat key and the guide key, then shift by the measured interval. A beat with
changing key or intentional expressive notes needs manual or note-level
correction.

### What is and is not automatic

| Need | Seed-VC alone | Extra layer |
|---|---|---|
| Keep Nana-like timbre | Yes, probabilistically | Clean reference and A/B listening |
| Follow a sung melody | Yes, through guide F0 | Stable guide performance |
| Discover beat BPM/key | No | Beat/key analysis tool |
| Compose melody from lyrics | No | MIDI/score or singing-synthesis model |
| Remove beat from a guide vocal | No | Vocal separation tool |
| Produce a finished mix | No | DAW/FFmpeg/mixer stage |

Eleven Music can generate a complete vocal song and use an audio reference to
guide style, tempo, mood, and instrumentation, but it should be treated as a
separate song-generation comparison. It should not be assumed to reproduce
Nana's exact custom timbre.

## First Smoke Matrix

No live run has been started for this research checkpoint. When the owner
opens the singing task, test offline files only:

| Case | Input | Purpose |
|---|---|---|
| S1 | 10-30 s clean Nana reference speech | Timbre anchor sanity check |
| S2 | 10-20 s humming or guide vocal | Melody transfer without lyric complexity |
| S3 | 20-40 s clean sung phrase | Consonants, vowels, pitch and vibrato |
| S4 | Same phrase with light accompaniment | Source-leakage and separation check |

Record these metrics for every render:

- perceived Nana-timbre similarity;
- pitch stability and note transitions;
- lyric intelligibility;
- metallic/buzzy artifacts and breath continuity;
- render time, GPU memory, and output sample rate;
- whether accompaniment leaks into the converted vocal.

The acceptance target is not “sounds like a generic AI singer.” It is “sounds
like Nana singing, with stable notes and no distracting artifacts.”

## Hardware and Environment Notes

The PC reports an NVIDIA GeForce RTX 2080. `nvidia-smi` was not available in
the current shell, so exact usable VRAM is not yet verified. The existing Nana
Python environment does not currently have PyTorch, librosa, or Gradio
installed; it contains the lightweight conversation/audio stack only.

Do not install a singing stack into `<NANA_REPO>\nana` yet. Use a separate virtual
environment or a separate checkout so model dependencies cannot change the
working TTS/Presence runtime.

For the first test, keep the master at 44.1 kHz WAV. The current Presence
transport is PCM16 mono at 16 kHz, which is suitable for speech but will discard
musical bandwidth. If Nana eventually sings through the robot speaker, that
will be a separate media-quality decision rather than an automatic reuse of
the speech path.

## Privacy and Rights Boundary

Use only a Nana voice reference that Ba owns or is authorized to use. Keep
reference clips and generated stems local by default. Personal use does not
automatically change the terms of a hosted provider, model, or copyrighted
recording; the project should use owner-provided lyrics/audio for tests and
avoid publishing anything without checking the applicable terms.

## Sources

- [Eleven Music capabilities](https://elevenlabs.io/docs/overview/capabilities/music)
- [Eleven Music API quickstart](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/music)
- [Seed-VC repository and singing conversion documentation](https://github.com/Plachtaa/seed-vc)
- [RVC repository](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI)
- [DiffSinger repository](https://github.com/openvpi/DiffSinger)
- [GPT-SoVITS repository](https://github.com/RVC-Boss/GPT-SoVITS)

## Proposed Capability Roadmap

```text
PHASE 0  contract + MusicalPlan + immutable artifact + Voice Identity Contract
PHASE 1  isolated Seed-VC offline experiment
PHASE 2  Nana Voice Profile v1 and reference curation
PHASE 3  SingingCapability + backend registry
PHASE 4  identity/pitch/lyrics/artifact evaluation suite
PHASE 5  accompaniment, key/BPM analysis, and final mix
PHASE 6  Song Intent -> Musical Plan proposal and human review
PHASE 7  human-approved singing execution
PHASE 8  DiffSinger research for lyrics + notes -> singing
PHASE 9  autonomous singing, only after controlled singing is accepted
```

## Current Next Step

Research is complete for this checkpoint. Do not download models, install
dependencies, call a hosted music API, or touch ESP32 until Ba explicitly
opens the singing experiment. The next safe action is to define the Phase-0
contract, then create an isolated Seed-VC workspace only after that approval.
For the beat-compatible trial, use an owner-provided beat and a short
humming/guide vocal already written in that beat's key; keep the first render
vocal-only before mixing.
