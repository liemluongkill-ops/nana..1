# Nana Discord Integration State

Last updated: 2026-06-25

## Current Status

- Discord bridge now supports controlled outbox sends for approved Nana starter proposals.
- This is STAGE-8F controlled send, not free auto-send:
  - Nana core writes an outbox event only after `/starter-send-enable` and `/starter-proposal-send <id>`.
  - Discord bridge consumes the outbox only when `DISCORD_OUTBOX_ENABLED=1`.
  - Live-verified by user transcript: approved starter text reached Discord `#chung`.
- Useful bridge env vars:
  - `DISCORD_OUTBOX_ENABLED=1`
  - `DISCORD_OUTBOX_CHANNEL_ID=<actual #chung channel id>`
  - `DISCORD_OUTBOX_CHANNEL_NAME=chung`
  - `DISCORD_ALLOWED_TEXT_CHANNEL_ID=<actual #chung channel id>`
  - `DISCORD_DEBUG_MESSAGES=1` for diagnosis.
- Known live failure mode:
  - If bridge logs `rejected by boundary`, `DISCORD_ALLOWED_TEXT_CHANNEL_ID` is stale/wrong.
  - If outbox logs unknown channel, set `DISCORD_OUTBOX_CHANNEL_ID` to the actual visible `#chung` ID.
  - Use `Outbox visible text channels: server/#channel=<id>` to discover the current text channel.
- Real guild/channel IDs are intentionally not recorded here.
- Discord bridge exists as a separate external transport at `<NANA_REPO>/nana_discord_bridge`.
- It is intentionally outside `<NANA_REPO>/nana` and does not own Nana's brain, persona, memory, or voice policy.
- It has a Discord bot/client based on `discord.Client` from `discord.py`.
- It has a prefix command surface using `!nana` by default.
- It has an `on_message` listener, command router, text reply mirroring, and Discord voice playback for Nana-rendered audio files.
- It writes request JSON files to `<NANA_REPO>/nana/data/external_bridge/requests`.
- It waits for reply JSON files in `<NANA_REPO>/nana/data/external_bridge/replies`.
- It can play returned `audio_paths` into Discord voice using FFmpeg through `discord.FFmpegPCMAudio`.
- It has route metadata for Discord chat/media/voice boundaries and an optional multi-server `routes.json`.
- Nana core now has a smoke-verified Nana-side worker:
  `<NANA_REPO>/nana/runtime/external_bridge.py`.
- The Nana-side worker consumes request JSON files, submits messages into
  `viewer_chat`, writes text-only reply JSON files, and moves processed requests
  to `requests/processed`.
- It does not directly import Nana core, memory, LLM, autonomy, or VoiceEngine. Those remain Nana-side responsibilities.
- Status: **partial**.
  - Base Discord bot/status/voice-toggle command path: live-verified by user Discord transcript before the latest route-aware patch.
  - Current route-aware code (`routes.json`, `!nana routes`, media metadata): smoke-verified only, not live-verified after restart.
  - Nana-side text-only core worker: smoke-verified in STAGE-7C.
  - End-to-end Discord message -> Nana core text reply -> Discord mirror:
    live-verified once by user transcript.
  - Duplicate-spam fix v2: smoke-verified after user observed two request JSON
    files with the same Discord `metadata.message_id` producing two different
    full replies. Nana core now dedupes by stable external event key
    `source:guild_id:channel_id:message_id` before model calls. Duplicate
    external events are silent (`reply_text=""`) while still `ok=true`.
  - Public persona leak guard: smoke-verified. Public Discord prompts now
    withhold private owner memory/history/recent moments/runtime/browser state,
    and the public sanitizer blocks private/system/game leak terms such as
    `Stardew`, `runtime`, `log`, `hệ thống của bạn`, and `nhúng tay`.
  - ElevenLabs audio playback into Discord voice: not implemented/live-verified for the current Nana-side worker.

Implemented commands:

```text
!nana status
!nana routes
!nana route
!nana routing
!nana ngat
!nana ngắt
!nana leave
!nana disconnect
!nana voice off
!nana mute
!nana im
!nana thoát voice
!nana thoat voice
!nana voice on
!nana bật voice
!nana bat voice
!nana unmute
!nana nói
!nana noi
!nana stop voice
!nana voice stop
!nana dừng voice
!nana dung voice
!nana <message>
```

Observed live Discord transcript from user:

```text
!nana status -> bot replied with Nana Discord Bridge boundary/status.
!nana ngat -> accepted as voice disconnect/disable command.
!nana voice on -> bot replied that Discord voice output was enabled again.
!nana status -> bot reported voice_enabled=true.
```

Real guild/channel IDs from the transcript are intentionally not recorded here.

## Architecture

Code path:

```text
<NANA_REPO>/nana_discord_bridge
```

Entry point:

```text
<NANA_REPO>/nana_discord_bridge/run_bot.py
```

Entrypoint behavior:

```text
run_bot.py -> nana_discord_bridge.bot.run()
bot.run() -> BridgeConfig.load() -> NanaDiscordBot(config) -> bot.run(config.discord_token)
```

Main modules:

- `nana_discord_bridge/config.py`
  - Loads `.env` from `<NANA_REPO>/nana_discord_bridge/.env`.
  - Defines `BridgeConfig`.
  - Requires `DISCORD_BOT_TOKEN`.
  - Supports optional guild/text/voice allowlists and queue paths.
- `nana_discord_bridge/bot.py`
  - Owns `NanaDiscordBot(discord.Client)`.
  - Enables `message_content` and `voice_states` intents.
  - Handles `on_ready` and `on_message`.
  - Routes built-in commands before sending normal prompts to Nana.
  - Builds `NanaDiscordRequest`.
  - Mirrors Nana reply text to Discord when enabled.
  - Plays returned voice audio into Discord when enabled.
- `nana_discord_bridge/safety.py`
  - Ignores bot-authored messages.
  - Enforces optional guild/text-channel allowlists.
  - Extracts prompts from prefix or bot mention.
  - Resolves target voice channel from env allowlist or caller current voice.
  - Supports `discord.VoiceChannel` and `discord.StageChannel`.
- `nana_discord_bridge/routing.py`
  - Builds `DiscordRoute`.
  - Produces route metadata: `input_surface`, `input_role`, `output_surface`, `voice_source`, `can_play_voice`, `local_playback=false`.
- `nana_discord_bridge/route_config.py`
  - Loads optional multi-server/channel route config from `routes.json`.
  - Supports `chat_channel_ids`, `media_channel_ids`, `default_voice_channel_id`, and `voice_by_text_channel`.
  - Fails closed into an error field instead of crashing on invalid JSON.
- `nana_discord_bridge/models.py`
  - Defines `NanaDiscordRequest` and `NanaDiscordReply`.
  - Request defaults: `source="discord"`, `event_type="message"`, `audio_target="discord_voice"`, `local_playback=false`.
  - Reply supports `reply_text`, `speak`, `audio_paths`, `audio_target`, and `local_playback`.
- `nana_discord_bridge/nana_client.py`
  - Implements file-queue submit/wait behavior.
  - Writes request JSON atomically.
  - Polls reply JSON until timeout.
- `nana_discord_bridge/voice_playback.py`
  - Connects/moves Discord voice client.
  - Plays existing non-empty audio files with FFmpeg.
  - Supports stop and disconnect commands.

Bridge to Nana core:

```text
Discord message
  -> NanaDiscordRequest JSON in request_dir
  -> Nana-side worker expected to process it
  -> NanaDiscordReply JSON in reply_dir
  -> Discord text mirror and/or Discord voice playback
```

Current Nana-side proof:

```text
Nana worker:
<NANA_REPO>/nana/runtime/external_bridge.py

Runtime startup:
<NANA_REPO>/nana/cli/app.py starts start_external_bridge_worker(...)

Commands:
/discord-core-status
/external-bridge-status
/discord-core-test <name>|<message>
/external-bridge-test <name>|<message>

Smoke:
<NANA_REPO>/smoke_stage7c_external_bridge_core_worker.py -> 5/5 passed.
```

Do not claim Discord voice/audio playback; STAGE-7C writes text-only replies
with `speak=false`, `audio_paths=[]`, and `local_playback=false`. If duplicate
Discord replies reappear, first check for multiple running bridge processes or
duplicate request JSON files with the same `metadata.message_id`.

Voice/TTS boundary:

- Discord bridge must not call Nana's local speaker playback path.
- Nana-side processing should use a render-only path and return `audio_paths`.
- For Discord requests, `local_playback` must remain `false`.
- The bridge plays returned audio only inside Discord voice.
- The bridge itself does not call ElevenLabs, VTS, OBS, Stardew, osu, or any game input path.

## Files Added / Changed

External Discord bridge path:

```text
<NANA_REPO>/nana_discord_bridge
```

Files:

- `<NANA_REPO>/nana_discord_bridge/run_bot.py`
- `<NANA_REPO>/nana_discord_bridge/README.md`
- `<NANA_REPO>/nana_discord_bridge/requirements.txt`
- `<NANA_REPO>/nana_discord_bridge/.env.example`
- `<NANA_REPO>/nana_discord_bridge/.gitignore`
- `<NANA_REPO>/nana_discord_bridge/routes.json`
- `<NANA_REPO>/nana_discord_bridge/routes.example.json`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/__init__.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/bot.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/config.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/models.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/nana_client.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/route_config.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/routing.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/safety.py`
- `<NANA_REPO>/nana_discord_bridge/nana_discord_bridge/voice_playback.py`

Runtime/config files:

- `<NANA_REPO>/nana_discord_bridge/.env` exists locally and is ignored by `.gitignore`.
- The real `.env` was not read or copied into this wiki.
- `<NANA_REPO>/nana/data/external_bridge/requests` was created as the request queue directory.
- `<NANA_REPO>/nana/data/external_bridge/replies` was created as the reply queue directory.

Dedicated test file:

- None yet.

## Commands / Env

Install dependencies:

```powershell
python -m pip install -r <NANA_REPO>\nana_discord_bridge\requirements.txt
```

Required dependency packages from `requirements.txt`:

```text
discord.py>=2.7
PyNaCl>=1.6
python-dotenv>=1.0
requests>=2.34
```

FFmpeg must be available:

```powershell
ffmpeg -version
```

User-provided terminal output verified FFmpeg `8.1.1` was available.

Run the bot:

```powershell
D:
cd \py\nana_discord_bridge
python run_bot.py
```

Environment variables, usually in `.env`:

```env
DISCORD_BOT_TOKEN=<DISCORD_TOKEN>
DISCORD_ALLOWED_GUILD_ID=<GUILD_ID_OR_BLANK>
DISCORD_ALLOWED_TEXT_CHANNEL_ID=<CHANNEL_ID_OR_BLANK>
DISCORD_ALLOWED_VOICE_CHANNEL_ID=<VOICE_CHANNEL_ID_OR_BLANK>
DISCORD_TRIGGER_PREFIX=!nana
DISCORD_AUTO_JOIN_USER_VOICE=1
DISCORD_MIRROR_TEXT_REPLY=1
NANA_REQUEST_DIR=<NANA_REPO>/nana/data/external_bridge/requests
NANA_REPLY_DIR=<NANA_REPO>/nana/data/external_bridge/replies
NANA_DISCORD_ROUTES_PATH=<NANA_REPO>/nana_discord_bridge/routes.json
NANA_REPLY_TIMEOUT_SECONDS=120
NANA_REPLY_POLL_SECONDS=0.25
```

Route config path:

```text
<NANA_REPO>/nana_discord_bridge/routes.json
```

Current `routes.json` state:

```json
{
  "guilds": {}
}
```

Example route shape:

```json
{
  "guilds": {
    "<GUILD_ID>": {
      "name": "server name",
      "chat_channel_ids": [
        "<TEXT_CHANNEL_ID>"
      ],
      "media_channel_ids": [
        "<MEDIA_CHANNEL_ID>"
      ],
      "default_voice_channel_id": "<VOICE_CHANNEL_ID>",
      "voice_by_text_channel": {
        "<TEXT_CHANNEL_ID>": "<VOICE_CHANNEL_ID>",
        "<MEDIA_CHANNEL_ID>": "<VOICE_CHANNEL_ID>"
      }
    }
  }
}
```

Smoke commands run:

```powershell
python -X pycache_prefix=$env:TEMP\nana_discord_pycache -m py_compile run_bot.py nana_discord_bridge\__init__.py nana_discord_bridge\config.py nana_discord_bridge\models.py nana_discord_bridge\nana_client.py nana_discord_bridge\route_config.py nana_discord_bridge\routing.py nana_discord_bridge\safety.py nana_discord_bridge\voice_playback.py nana_discord_bridge\bot.py
```

```powershell
python -B -c "from pathlib import Path; from nana_discord_bridge.route_config import RouteConfig; cfg=RouteConfig.load(Path('routes.json')); print('routes_ok=', not bool(cfg.error)); print('guild_count=', len(cfg.guilds))"
```

```powershell
python -B -c "import nana_discord_bridge.bot, nana_discord_bridge.config, nana_discord_bridge.routing, nana_discord_bridge.route_config; print('import_ok')"
```

Discord live commands used by user:

```text
!nana status
!nana ngat
!nana voice on
!nana status
```

Not live-verified after route patch:

```text
!nana routes
!nana <normal message requiring Nana-side reply>
Discord voice playback from returned ElevenLabs audio
```

## Safety / Boundaries

- Do not commit `.env`.
- Do not paste or log real Discord bot tokens.
- Do not paste or log real client secrets.
- Do not write real token/guild/channel IDs into wiki files; use `<DISCORD_TOKEN>`, `<GUILD_ID>`, and `<CHANNEL_ID>`.
- `.gitignore` contains `.env`, `__pycache__/`, and `*.pyc`.
- The bridge ignores messages from bots.
- The bridge only responds to the configured prefix or bot mention.
- Optional allowlists exist: `DISCORD_ALLOWED_GUILD_ID`, `DISCORD_ALLOWED_TEXT_CHANNEL_ID`, `DISCORD_ALLOWED_VOICE_CHANNEL_ID`.
- Current `routes.json` is empty, so multi-server channel routing is not configured yet.
- Voice output can be disabled from Discord using `!nana ngat` or related aliases.
- Voice output can be re-enabled using `!nana voice on` or related aliases.
- Current code has no cooldown, dedupe, per-user rate limit, or anti-spam queue cap.
- Current code has no slash-command sync; it is prefix/mention based.
- Current code does not call VTS, OBS, Stardew, osu, keyboard/mouse input, or local speaker playback.
- Current code does not download/store Discord attachments. It passes attachment metadata and Discord attachment URLs to Nana.
- Nana-side handling must keep Discord requests render-only: `source=discord`, `audio_target=discord_voice`, `local_playback=false`.
- Do not route Discord requests through local `voice.say()` playback. Use a render-only audio path and return `audio_paths`.

## Verified

Static/code verification:

- Read the active Discord bridge code under `<NANA_REPO>/nana_discord_bridge`.
- Read `README.md`, `.env.example`, `routes.json`, `routes.example.json`, and `requirements.txt`.
- Did not read or print the real `.env`.
- Checked `.gitignore`; `.env` is ignored.
- Earlier documentation pass found no Nana-side queue consumer. This is now
  superseded by STAGE-7C: `<NANA_REPO>/nana/runtime/external_bridge.py` exists and is
  smoke-verified.
- STAGE-7C smoke verified request JSON -> `viewer_chat` -> text-only reply JSON
  with `speak=false`, `audio_paths=[]`, and `local_playback=false`.

Smoke:

```text
python -X pycache_prefix=$env:TEMP\nana_discord_pycache -m py_compile ...
Result: pass, exit code 0.
```

```text
RouteConfig.load(Path('routes.json'))
Result:
routes_ok= True
guild_count= 0
```

```text
import nana_discord_bridge.bot, nana_discord_bridge.config, nana_discord_bridge.routing, nana_discord_bridge.route_config
Result:
import_ok
```

Known compile note:

```text
Plain py_compile once failed with WinError 5 while writing an existing __pycache__ .pyc file.
Rerun with -X pycache_prefix passed. Treat this as a Windows/cache write issue, not a syntax failure.
```

Live Discord:

- User screenshot/transcript showed the bot online in Discord.
- User transcript showed `!nana status` returned a bridge status block.
- User transcript showed `!nana ngat` then `!nana voice on` and a later `!nana status` with `voice_enabled=true`.
- This was live verification of the base bot command surface before the latest route-aware patch.
- `!nana routes` is not live-verified.
- End-to-end Nana core text reply was live-verified once after STAGE-7C.
- Duplicate-silent fix is smoke-verified; one-more live prompt is still useful
  to confirm only one Discord reply appears after restarting stale bridge
  processes.
- Discord voice playback from Nana-rendered ElevenLabs audio is not live-verified.

## Known Gaps / Blockers

- Nana-side request queue consumer is implemented and smoke-verified in
  STAGE-7C, and the text reply path was live-verified once with a real Discord
  message.
- End-to-end path from Discord prompt -> Nana core public persona -> reply JSON
  -> Discord mirror is live-verified once; duplicate-silent fix awaits a
  one-prompt recheck.
- End-to-end path from Nana TTS render -> `audio_paths` -> Discord voice playback is not verified.
- Current route-aware patch has not been restarted/live-tested in Discord.
- `routes.json` is empty; multi-server channel roles are not configured yet.
- Dedicated Nana-side smoke exists:
  `<NANA_REPO>/smoke_stage7c_external_bridge_core_worker.py`.
- Nana core viewer queue has duplicate and per-viewer rate guards; the external
  Discord bot transport still has no separate cooldown/spam supervision.
- No slash commands are registered/synced.
- No reconnect/backoff supervision loop is implemented beyond Discord client's own behavior.
- No persistent voice-enabled state; `voice_enabled` resets to `True` when bot restarts.
- No per-guild independent voice state; `voice_enabled` is one bot-wide boolean.
- No attachment download/vision pipeline exists; only attachment metadata/URLs are forwarded.
- Live reply JSON showed `public_vtuber_persona` for the accepted Discord
  request; duplicate request was marked `duplicate_recent`.
- No live proof yet that Discord messages avoid private owner memory in a real
  Discord run; STAGE-7C smoke verifies the public queue boundary and text
  sanitizer.

## Next Task

1. User rechecks STAGE-7C duplicate-silent fix: ensure only one bridge process
   is running, send one `!nana <message>`, and confirm only one Discord reply.
2. Restart the Discord bot and live-verify `!nana routes` in a private test
   channel.
3. Configure `routes.json` with redacted-safe server/channel mapping, then add a
   focused smoke test for route loading and request metadata.
4. Open a later stage for render-only ElevenLabs audio paths into Discord voice;
   do not claim voice playback for STAGE-7C.
