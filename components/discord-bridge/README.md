# Nana Discord Bridge

External Discord transport for Nana.

This bridge is intentionally outside the `nana` Python package. It is not Nana's brain,
persona, memory, or voice policy. It only:

1. Receives Discord messages.
2. Writes a clean request JSON for Nana.
3. Waits for Nana to write a reply JSON.
4. Mirrors reply text to Discord.
5. Plays Nana-rendered audio files into Discord voice.

Discord requests must stay render-only on Nana's side:

```text
source=discord
audio_target=discord_voice
local_playback=false
```

Do not route Discord requests through `voice.say()`. That path is local speaker
playback. Nana should render audio with `VoiceEngine.tts_to_audio_paths(...)`
and return `audio_paths` to this bridge.

## Chat vs Voice Boundary

Discord messages are text input by default:

```text
input_surface=discord_text
```

If the message carries attachments, the bridge marks it as media input:

```text
input_surface=discord_media
```

Discord voice is only the output surface when the bridge can resolve a voice
channel from `routes.json`, `DISCORD_ALLOWED_VOICE_CHANNEL_ID`, or the caller's
current voice channel:

```text
output_surface=discord_voice
```

If no voice channel is available, the request still goes to Nana, but route
metadata becomes:

```text
output_surface=discord_text_only
can_play_voice=false
```

The Nana-side worker should keep `local_playback=false` for every Discord
request. If it renders audio, it should return `audio_paths` and let this bridge
play them in Discord.

## Multi-Server Stage Routing

`routes.json` lets this bridge work as a portable Discord stage router. Nana
stays the main brain; this file only tells the transport which Discord rooms
mean chat, media, and voice output.

Default file:

```text
components/discord-bridge/routes.json
```

Shape:

```json
{
  "guilds": {
    "SERVER_ID": {
      "name": "server name",
      "chat_channel_ids": ["TEXT_CHAT_CHANNEL_ID"],
      "media_channel_ids": ["IMAGE_OR_MEDIA_CHANNEL_ID"],
      "default_voice_channel_id": "VOICE_CHANNEL_ID",
      "voice_by_text_channel": {
        "TEXT_CHAT_CHANNEL_ID": "VOICE_CHANNEL_ID",
        "IMAGE_OR_MEDIA_CHANNEL_ID": "VOICE_CHANNEL_ID"
      }
    }
  }
}
```

The router sends Nana metadata like:

```text
input_role=chat | media | unconfigured_text | dm
input_surface=discord_text | discord_media
output_surface=discord_voice | discord_text_only
voice_source=routes_json | configured_voice_channel | caller_current_voice | none
```

If a Discord message has attachments, the request is marked
`input_surface=discord_media` and includes attachment metadata. The bridge does
not download or store the file; it only passes Discord's attachment URL and
basic type/size fields to Nana.

Use this in Discord to see the current IDs and loaded route config:

```text
!nana routes
```

If `routes.json` is empty, the bridge still works in test mode by using the
caller current voice channel when available.

## Setup

Copy `.env.example` to `.env`, then fill:

```env
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_GUILD_ID=...
DISCORD_ALLOWED_TEXT_CHANNEL_ID=...
DISCORD_ALLOWED_VOICE_CHANNEL_ID=...
DISCORD_OUTBOX_ENABLED=1
DISCORD_OUTBOX_GUILD_ID=...
DISCORD_OUTBOX_CHANNEL_ID=...
DISCORD_OUTBOX_CHANNEL_NAME=chung
NANA_DISCORD_ROUTES_PATH=<repo>/components/discord-bridge/routes.json
```

If the outbox channel ID is stale or the bot cannot resolve it, the bridge now
falls back to the configured outbox channel name within the known guild. For the
common `#chung` stage, `DISCORD_OUTBOX_CHANNEL_NAME=chung` is the safest backup.

Install dependencies:

```powershell
python -m pip install -r components\discord-bridge\requirements.txt
```

FFmpeg must be available:

```powershell
ffmpeg -version
```

## Run

```powershell
Set-Location components\discord-bridge
python run_bot.py
```

In Discord:

```text
!nana status
!nana routes
!nana Nana ơi, nghe Ba không?
!nana ngat
!nana voice on
!nana stop voice
```

Voice safety commands:

```text
!nana ngat       # disconnects from voice and disables future Discord voice output
!nana voice on   # enables Discord voice output again
!nana stop voice # stops current audio but keeps voice output enabled
```

## Request Contract

The bridge writes one request per file:

```text
nana/data/external_bridge/requests/<request_id>.json
```

Example:

```json
{
  "request_id": "discord-...",
  "source": "discord",
  "event_type": "message",
  "text": "Nana ơi",
  "guild_id": 123,
  "channel_id": 456,
  "voice_channel_id": 789,
  "author_id": 111,
  "author_name": "Ba",
  "audio_target": "discord_voice",
  "local_playback": false,
  "metadata": {
    "route": {
      "input_surface": "discord_text",
      "input_role": "chat",
      "output_surface": "discord_voice",
      "chat_channel_id": 456,
      "chat_channel_name": "chung",
      "voice_channel_id": 789,
      "voice_channel_name": "Chung",
      "voice_source": "caller_current_voice",
      "can_play_voice": true,
      "local_playback": false
    },
    "attachments": []
  }
}
```

## Reply Contract

Nana writes:

```text
nana/data/external_bridge/replies/<request_id>.json
```

Example:

```json
{
  "request_id": "discord-...",
  "ok": true,
  "reply_text": "Con nghe nè Ba.",
  "speak": true,
  "audio_target": "discord_voice",
  "local_playback": false,
  "audio_paths": [
    "<repo>/nana/data/voice_cache/abc123.mp3"
  ]
}
```

If `speak=true`, `audio_target=discord_voice`, and `audio_paths` exist, this
bridge joins the target voice channel and plays the audio with FFmpeg.
