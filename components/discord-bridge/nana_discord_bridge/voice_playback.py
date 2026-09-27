"""Discord voice playback for Nana-rendered audio files."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterable, Optional, Union

import discord


VoiceTarget = Union[discord.VoiceChannel, discord.StageChannel]


class DiscordVoicePlayback:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def play_paths(
        self,
        *,
        guild: discord.Guild,
        voice_channel: VoiceTarget,
        audio_paths: Iterable[str],
    ) -> None:
        paths = [Path(path) for path in audio_paths if path]
        existing = [path for path in paths if path.exists() and path.stat().st_size > 0]
        if not existing:
            return

        async with self._lock:
            voice_client = self._voice_client_for(guild)
            if voice_client is None or not voice_client.is_connected():
                voice_client = await voice_channel.connect()
            elif voice_client.channel.id != voice_channel.id:
                await voice_client.move_to(voice_channel)

            for path in existing:
                await self._play_one(voice_client, path)

    async def stop_and_disconnect(self, guild: discord.Guild) -> bool:
        async with self._lock:
            voice_client = self._voice_client_for(guild)
            if voice_client is None or not voice_client.is_connected():
                return False
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
            await voice_client.disconnect(force=True)
            return True

    async def stop_playback(self, guild: discord.Guild) -> bool:
        async with self._lock:
            voice_client = self._voice_client_for(guild)
            if voice_client is None or not voice_client.is_connected():
                return False
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
                return True
            return False

    @staticmethod
    def _voice_client_for(guild: discord.Guild) -> Optional[discord.VoiceClient]:
        for client in guild.voice_clients:
            if client.guild.id == guild.id:
                return client
        return None

    @staticmethod
    async def _play_one(voice_client: discord.VoiceClient, path: Path) -> None:
        while voice_client.is_playing() or voice_client.is_paused():
            await asyncio.sleep(0.05)

        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        error_holder = {"error": None}

        def _after(error: Optional[Exception]) -> None:
            error_holder["error"] = error
            loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegPCMAudio(str(path))
        voice_client.play(source, after=_after)
        await done.wait()
        if error_holder["error"] is not None:
            raise RuntimeError(f"Discord voice playback failed: {error_holder['error']}")
