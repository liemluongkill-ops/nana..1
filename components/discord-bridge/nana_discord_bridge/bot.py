"""Discord bot runtime for Nana's external bridge."""

from __future__ import annotations

import asyncio
import traceback
from typing import Optional

import discord

from .config import BridgeConfig
from .models import NanaDiscordRequest
from .nana_client import NanaBridgeTimeout, NanaFileQueueClient
from .outbox import DiscordOutboxEvent, iter_outbox_files, move_outbox_file
from .routing import build_route
from .safety import VoiceTarget, extract_prompt, is_allowed_message
from .voice_playback import DiscordVoicePlayback


class NanaDiscordBot(discord.Client):
    def __init__(self, config: BridgeConfig):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.config = config
        self.nana = NanaFileQueueClient(config)
        self.playback = DiscordVoicePlayback()
        self.voice_enabled = True
        self._outbox_task: Optional[asyncio.Task] = None

    async def setup_hook(self) -> None:
        if self.config.outbox_enabled:
            self.config.outbox_dir.mkdir(parents=True, exist_ok=True)
            self._outbox_task = asyncio.create_task(self._outbox_loop())

    async def on_ready(self) -> None:
        print(f"Discord bridge ready as {self.user} ({self.user.id if self.user else 'unknown'})")
        print(f"Trigger prefix: {self.config.trigger_prefix}")
        print(f"Request dir: {self.config.request_dir}")
        print(f"Reply dir: {self.config.reply_dir}")
        print(
            "Outbox: "
            f"enabled={self.config.outbox_enabled} | dir={self.config.outbox_dir} | "
            f"target_channel_id={self.config.outbox_channel_id or 'none'} | "
            f"target_channel_name={self.config.outbox_channel_name or 'none'} | "
            f"allowed_text_channel_id={self.config.allowed_text_channel_id or 'none'}"
        )
        print(f"Debug messages: {self.config.debug_messages}")
        if self.config.outbox_enabled:
            print(f"Outbox visible text channels: {self._visible_text_channels_summary()}")

    async def on_message(self, message: discord.Message) -> None:
        if self.config.debug_messages:
            print(
                "[message] "
                f"guild={getattr(message.guild, 'id', 'dm')} "
                f"channel={getattr(message.channel, 'id', 'unknown')} "
                f"author={message.author} bot={message.author.bot} "
                f"content={(message.content or '')[:80]!r}"
            )

        if not is_allowed_message(message, self.config):
            if self.config.debug_messages:
                print(
                    "[message] rejected by boundary "
                    f"allowed_guild={self.config.allowed_guild_id or 'any'} "
                    f"allowed_text={self.config.allowed_text_channel_id or 'any'}"
                )
            return

        prompt = extract_prompt(message, self.user, self.config.trigger_prefix)
        if prompt is None:
            if self.config.debug_messages:
                print(f"[message] ignored: no trigger prefix/mention ({self.config.trigger_prefix})")
            return

        if not prompt:
            await message.reply(f"Gọi Nana bằng `{self.config.trigger_prefix} <nội dung>` nha.")
            return

        command = prompt.strip().lower()
        if command in {"status", "bridge status"}:
            await self._send_status(message)
            return
        if command in {"routes", "route", "routing"}:
            await self._send_routes(message)
            return

        if command in {"ngắt", "ngat", "leave", "disconnect", "voice off", "mute", "im", "thoát voice", "thoat voice"}:
            await self._voice_off(message)
            return

        if command in {"voice on", "bật voice", "bat voice", "unmute", "nói", "noi"}:
            self.voice_enabled = True
            await message.reply("Đã bật lại Discord voice output cho Nana.")
            return

        if command in {"stop voice", "voice stop", "dừng voice", "dung voice"}:
            await self._voice_stop(message)
            return

        route, voice_channel = build_route(message, self.config)
        route_metadata = route.metadata()
        attachments = self._attachment_metadata(message)
        stickers = self._sticker_metadata(message)
        if attachments or stickers:
            route_metadata["input_surface"] = "discord_media"
            route_metadata["input_has_media"] = True
            route_metadata["input_has_image"] = any(item["is_image"] for item in attachments)
            route_metadata["input_has_sticker"] = bool(stickers)
        if not self.voice_enabled:
            route_metadata.update(
                {
                    "output_surface": "discord_text_only",
                    "voice_disabled_by_bridge": True,
                    "can_play_voice": False,
                }
            )
        request = NanaDiscordRequest.create(
            text=prompt,
            guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,
            voice_channel_id=route.voice_channel_id if self.voice_enabled else None,
            author_id=message.author.id,
            author_name=str(message.author),
            metadata={
                "message_id": message.id,
                "channel_name": route.chat_channel_name,
                "voice_channel_name": route.voice_channel_name,
                "route": route_metadata,
                "attachments": attachments,
                "stickers": stickers,
                "bridge_note": "local_playback_false_discord_voice_target",
            },
        )
        if not self.voice_enabled:
            request.audio_target = "discord_text_only"

        if self.config.debug_messages:
            print(f"[message] accepted prompt={prompt[:80]!r} request_id={request.request_id}")

        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(self.nana.request_and_wait, request)
            except NanaBridgeTimeout:
                await message.reply("Nana chưa trả lời kịp. Bridge đã gửi request nhưng hết thời gian chờ.")
                return
            except Exception as exc:
                await message.reply(f"Bridge lỗi khi nói với Nana: `{exc}`")
                return

        if not reply.ok:
            await message.reply(f"Nana bridge trả lỗi: `{reply.error or reply.status}`")
            return

        if self.config.mirror_text_reply and reply.reply_text:
            await message.reply(reply.reply_text[:1900])

        if self.voice_enabled and reply.speak and reply.audio_paths and reply.audio_target == "discord_voice":
            await self._play_reply_audio(message, voice_channel, reply.audio_paths)

    async def on_error(self, event_method: str, /, *args, **kwargs) -> None:
        print(f"[discord] handler error in {event_method}")
        traceback.print_exc()

    async def _play_reply_audio(
        self,
        message: discord.Message,
        voice_channel: Optional[VoiceTarget],
        audio_paths: list[str],
    ) -> None:
        if message.guild is None:
            await message.channel.send("Không phát voice trong DM được.")
            return
        if voice_channel is None:
            await message.channel.send("Nana có audio rồi, nhưng chưa biết voice channel để phát.")
            return
        try:
            await self.playback.play_paths(
                guild=message.guild,
                voice_channel=voice_channel,
                audio_paths=audio_paths,
            )
        except Exception as exc:
            await message.channel.send(f"Phát voice Discord lỗi: `{exc}`")

    async def _send_status(self, message: discord.Message) -> None:
        route, _voice_channel = build_route(message, self.config)
        guild_label = f"{message.guild.name} ({message.guild.id})" if message.guild else "DM"
        lines = [
            "Nana Discord Bridge",
            "Boundary:",
            f"  guild={guild_label}",
            f"  chat_input=#{route.chat_channel_name} ({route.chat_channel_id})",
            f"  input_role={route.input_role}",
            f"  voice_output={route.voice_label}"
            + (f" ({route.voice_channel_id})" if route.can_play_voice else ""),
            f"  voice_source={route.voice_source}",
            f"request_dir={self.config.request_dir}",
            f"reply_dir={self.config.reply_dir}",
            f"routes_path={self.config.route_config_path}",
            f"voice_channel={route.voice_label}",
            f"voice_enabled={str(self.voice_enabled).lower()}",
            "local_playback=false for Discord requests",
            "Commands: !nana routes | !nana ngat | !nana voice on | !nana stop voice",
        ]
        await message.reply("```text\n" + "\n".join(lines) + "\n```")

    async def _send_routes(self, message: discord.Message) -> None:
        from .route_config import RouteConfig

        route_config = RouteConfig.load(self.config.route_config_path)
        route, _voice_channel = build_route(message, self.config)
        guild_label = f"{message.guild.name} ({message.guild.id})" if message.guild else "DM"
        lines = [
            f"Routes config: {self.config.route_config_path}",
            f"Current guild={guild_label}",
            f"Current channel=#{route.chat_channel_name} ({route.chat_channel_id}) role={route.input_role}",
            f"Current voice={route.voice_label}"
            + (f" ({route.voice_channel_id})" if route.can_play_voice else ""),
        ]
        if route_config.error:
            lines.append(f"routes.json error={route_config.error}")
        if not route_config.guilds:
            lines.append("No routes.json loaded. Fallback: caller_current_voice.")
        for guild_id, guild_route in route_config.guilds.items():
            lines.extend(
                [
                    f"Guild {guild_id} {guild_route.name}".rstrip(),
                    f"  chat_channels={sorted(guild_route.chat_channel_ids)}",
                    f"  media_channels={sorted(guild_route.media_channel_ids)}",
                    f"  default_voice_channel={guild_route.default_voice_channel_id}",
                    f"  voice_by_text_channel={guild_route.voice_by_text_channel}",
                ]
            )
        await message.reply("```text\n" + "\n".join(lines)[:1800] + "\n```")

    async def _voice_off(self, message: discord.Message) -> None:
        self.voice_enabled = False
        disconnected = False
        if message.guild is not None:
            disconnected = await self.playback.stop_and_disconnect(message.guild)
        state = "đã rời voice" if disconnected else "không có voice connection đang mở"
        await message.reply(
            "Đã ngắt Discord voice output cho Nana. "
            f"{state}. Dùng `!nana voice on` để bật lại."
        )

    async def _voice_stop(self, message: discord.Message) -> None:
        stopped = False
        if message.guild is not None:
            stopped = await self.playback.stop_playback(message.guild)
        await message.reply("Đã dừng audio đang phát." if stopped else "Không có audio nào đang phát.")

    async def _outbox_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._drain_outbox_once()
            except Exception as exc:
                print(f"[outbox] loop error: {exc}")
            await asyncio.sleep(max(0.1, self.config.outbox_poll_seconds))

    async def _drain_outbox_once(self) -> None:
        for path in iter_outbox_files(self.config.outbox_dir, limit=5):
            try:
                event = DiscordOutboxEvent.from_path(path)
                channel = await self._resolve_outbox_channel(event)
                if not hasattr(channel, "send"):
                    raise RuntimeError(f"target {event.channel_id} is not a text channel")
                await channel.send(event.text[:1900])
                move_outbox_file(path, "sent")
                print(f"[outbox] sent event={event.event_id} proposal={event.proposal_id}")
            except Exception as exc:
                move_outbox_file(path, "failed", reason=str(exc))
                print(f"[outbox] failed {path.name}: {exc}")

    async def _resolve_outbox_channel(self, event: DiscordOutboxEvent):
        event_channel_id = event.channel_id
        channel = self.get_channel(event_channel_id)
        if channel is not None:
            return channel
        try:
            channel = await self.fetch_channel(event_channel_id)
            if channel is not None:
                return channel
        except Exception:
            pass

        fallback_ids = [
            self.config.outbox_channel_id,
            self.config.allowed_text_channel_id,
        ]
        for fallback_id in fallback_ids:
            if not fallback_id or fallback_id == event_channel_id:
                continue
            channel = self.get_channel(fallback_id)
            if channel is not None:
                print(f"[outbox] fallback channel used: {fallback_id} (event had {event_channel_id})")
                return channel
            try:
                channel = await self.fetch_channel(fallback_id)
            except Exception:
                channel = None
            if channel is not None:
                print(f"[outbox] fallback channel used: {fallback_id} (event had {event_channel_id})")
                return channel

        fallback_names = [
            event.channel_name,
            self.config.outbox_channel_name,
            "chung",
            "general",
        ]
        for name in fallback_names:
            channel = self._find_text_channel_by_name(name, guild_id=event.guild_id or self.config.outbox_guild_id)
            if channel is not None:
                print(f"[outbox] fallback channel used by name: #{channel.name} ({channel.id})")
                return channel

        raise RuntimeError(
            f"Unknown Channel: {event_channel_id}; visible={self._visible_text_channels_summary()}"
        )

    def _find_text_channel_by_name(self, name: str, *, guild_id: Optional[int] = None):
        clean = (name or "").strip().lstrip("#").lower()
        if not clean:
            return None
        guilds = self.guilds
        if guild_id:
            guild = self.get_guild(guild_id)
            guilds = [guild] if guild is not None else []
        for guild in guilds:
            for channel in getattr(guild, "text_channels", []) or []:
                if getattr(channel, "name", "").lower() == clean:
                    return channel
        return None

    def _visible_text_channels_summary(self, *, limit: int = 12) -> str:
        items = []
        for guild in self.guilds:
            for channel in getattr(guild, "text_channels", []) or []:
                items.append(f"{guild.name}/#{channel.name}={channel.id}")
                if len(items) >= limit:
                    return ", ".join(items)
        return ", ".join(items) if items else "none"

    @staticmethod
    def _attachment_metadata(message: discord.Message) -> list[dict]:
        items = []
        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            filename = attachment.filename or ""
            lower_name = filename.lower()
            is_image = content_type.startswith("image/") or lower_name.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")
            )
            items.append(
                {
                    "id": attachment.id,
                    "filename": filename,
                    "url": attachment.url,
                    "content_type": content_type,
                    "size": attachment.size,
                    "width": attachment.width,
                    "height": attachment.height,
                    "is_image": is_image,
                }
            )
        return items

    @staticmethod
    def _sticker_metadata(message: discord.Message) -> list[dict]:
        items = []
        for sticker in getattr(message, "stickers", []) or []:
            items.append(
                {
                    "id": sticker.id,
                    "name": sticker.name,
                    "url": getattr(sticker, "url", ""),
                    "format": str(getattr(sticker, "format", "")),
                }
            )
        return items


def run() -> None:
    config = BridgeConfig.load()
    config.require_runtime_ready()
    bot = NanaDiscordBot(config)
    bot.run(config.discord_token)
