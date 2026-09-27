"""Client for talking to Nana through a file-queue contract."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from .config import BridgeConfig
from .models import NanaDiscordReply, NanaDiscordRequest


class NanaBridgeTimeout(TimeoutError):
    """Raised when Nana does not produce a reply before the bridge deadline."""


class NanaFileQueueClient:
    """Write Discord requests and wait for Nana reply JSON files.

    The Discord bridge never imports Nana brain/persona/memory. Nana remains
    the source of truth and writes a reply with rendered audio paths when ready.
    """

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.config.request_dir.mkdir(parents=True, exist_ok=True)
        self.config.reply_dir.mkdir(parents=True, exist_ok=True)

    def submit(self, request: NanaDiscordRequest) -> Path:
        final_path = self.config.request_dir / f"{request.request_id}.json"
        self._atomic_write(final_path, request.to_json())
        return final_path

    def wait_for_reply(self, request_id: str) -> NanaDiscordReply:
        deadline = time.monotonic() + self.config.reply_timeout_seconds
        reply_path = self.config.reply_dir / f"{request_id}.json"
        while time.monotonic() < deadline:
            if reply_path.exists() and reply_path.stat().st_size > 0:
                return NanaDiscordReply.from_json_file(reply_path)
            time.sleep(self.config.poll_interval_seconds)
        raise NanaBridgeTimeout(f"Timed out waiting for Nana reply: {request_id}")

    def request_and_wait(self, request: NanaDiscordRequest) -> NanaDiscordReply:
        self.submit(request)
        return self.wait_for_reply(request.request_id)

    @staticmethod
    def _atomic_write(final_path: Path, text: str) -> None:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=final_path.parent,
                suffix=".tmp",
            ) as handle:
                temp_name = handle.name
                handle.write(text)
                handle.write("\n")
            os.replace(temp_name, final_path)
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
