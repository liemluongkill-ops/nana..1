# Nana Runtime

This repository contains a credential-free Nana Core runtime snapshot plus
source-only optional components. Private memory, chat history, logs, captures,
credentials, purchased avatar assets, generated builds, and third-party model
weights are intentionally absent.

## Requirements

- Windows 10 or 11
- Python 3.11 or 3.12
- A microphone and speakers for voice features
- FFmpeg on `PATH` for MP3 voice decoding
- Provider credentials supplied only through a local `.env`

Avatar, Discord, Presence, Stardew, osu, OBS, and YouTube support are optional.
All side-effecting capabilities are disabled by default.

## Quick Start

```powershell
git clone https://github.com/liemluongkill-ops/nana..1.git
Set-Location nana..1

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python scripts\bootstrap_runtime.py
notepad .env
python scripts\smoke_public_runtime.py
python -m nana
```

At minimum, set `OPENAI_API_KEY` in `.env`. The public template selects the
OpenAI chat path and starts with TTS disabled. To enable ElevenLabs, set
`ELEVEN_API_KEY`, `ELEVEN_VOICE_ID`, and `NANA_DEBUG_NO_TTS=0` locally.

Never commit `.env`, OAuth files, memory files, token files, logs, or captures.

## Runtime Layout

```text
nana/                         Python companion runtime
nana/tests/                   Offline and mocked smoke coverage
NanaBridge/                   Stardew SMAPI bridge source
components/discord-bridge/    Optional Discord transport
components/presence-node/     Optional ESP32-S3 firmware and tools
components/avatar-web/        Optional browser host source
components/avatar-unity-scripts/ Unity/WebGL controller source only
scripts/                      Bootstrap, offline smoke, optional asset fetch
```

The current entry point is `python -m nana`. `nana/main.py` is retained for
legacy review but is not the current CLI hot path.

## Optional Vision Model

Presence camera face detection uses the OpenCV Zoo YuNet model. It is not
redistributed here. Fetch and verify it with:

```powershell
python scripts\fetch_optional_assets.py
```

## Optional Components

- `components/discord-bridge`: copy its `.env.example` to a local `.env` and
  install its own requirements.
- `components/presence-node`: build with ESP-IDF 5.5.x. Select the ESP32-S3
  target before the first build. The bundled PCM is a generated synthetic
  fixture, not a private voice recording.

  ```powershell
  Set-Location components\presence-node
  idf.py set-target esp32s3
  idf.py build
  ```
- `components/avatar-web`: run `npm ci` and `npm run dev`. The Unity WebGL
  player/model bundle is not included.
- `components/avatar-unity-scripts`: controller and build scripts only. Supply
  a separately licensed humanoid model and required Unity packages.
- `NanaBridge`: set the MSBuild `StardewGamePath` property to a local game
  installation before building.

## Safety Defaults

- Memory v2 Phase 2 remains default-off.
- Stream CUM0-CUM5 remains default-off.
- Avatar gateway and automatic avatar events remain default-off.
- Game input requires explicit capability gates and operator approval.
- Presence credentials are never stored in source.

## Licensing Note

The repository currently grants no open-source license. The code can be read
and evaluated from GitHub, but redistribution or derivative use requires the
owner to publish an explicit license.
