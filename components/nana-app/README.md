# NanaApp Public Source

Private Web Chat client and a separate OBS visual entry, exported with the
Core protocol and Node tests. This directory contains source only. It does
not bundle Unity players, purchased avatar assets, screenshots or recordings.

## Build and Tests

Requires Node.js compatible with Vite 8 (20.19+ or 22.12+).

```powershell
npm ci
npm test
npm run build
```

The build succeeds without avatar binaries so the client and protocol can be
reviewed. A complete rendered avatar requires separately licensed Unity builds:

- Main view: `public/Build/unity-fidelity.*` and `public/StreamingAssets/`.
- OBS view: `public/obs-avatar/Build/unity-obs.*` and its StreamingAssets folder.

These asset directories are ignored by Git. Missing bundles produce the
existing avatar-load error; the source export is not a bundled 3D application.
Both development and production OBS assets use `/obs-avatar`.

## Core-Owned Startup

From the repository root, finish the Python setup described in RUNTIME.md.
After building NanaApp, opt in through your local `.env`:

```dotenv
NANA_WEB_AUTO_OPEN_ENABLED=1
NANA_PRIVATE_WEB_CHAT_ENABLED=1
```

Start `python -m nana` from the repository root. Core locates this component
relative to its package, starts the preview at 127.0.0.1:5174 and owns its
process lease. The private WebSocket on 8767 admits only the validated local
client/session. The standalone Vite dev server does not provide that lease.
Do not weaken the ownership gate to attach a browser from an unrelated server.

Core is the model/voice owner. This browser does not store provider API keys.
Public OBS signals and the private chat channel stay separate. Public visual
timing still has bounded acceptance limits described in the architecture notes.
