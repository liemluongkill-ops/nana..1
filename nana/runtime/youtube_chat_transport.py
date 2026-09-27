"""Explicit read-only YouTube Live Chat transports for Stream V1 ingress.

The module is inert on import.  A caller must explicitly construct a client
and invoke a method. The diagnostic probe and default-off CUM1 can consume its
responses. It never calls LLM, TTS, playback, OBS, VTS, Discord, public output,
or Nana's main runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping
import os

import requests

from nana.runtime.youtube_chat_ingress import IngestResult, YouTubeChatIngress


YOUTUBE_API_ROOT = "https://www.googleapis.com/youtube/v3"
YOUTUBE_GRPC_TARGET = "dns:///youtube.googleapis.com:443"
YOUTUBE_STREAM_METHOD = "/youtube.api.v3.V3DataLiveChatMessageService/StreamList"
PHASE = "STREAM-YT-TRANSPORT-1"


class YouTubeChatTransportError(RuntimeError):
    """Safe, non-secret transport error."""


@dataclass(frozen=True)
class YouTubeCredentials:
    """Exactly one API key or OAuth bearer token, kept out of diagnostics."""

    api_key: str | None = None
    oauth_token: str | None = None

    def validate(self) -> None:
        has_key = bool(str(self.api_key or "").strip())
        has_oauth = bool(str(self.oauth_token or "").strip())
        if has_key == has_oauth:
            raise YouTubeChatTransportError("provide exactly one API key or OAuth token")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "YouTubeCredentials":
        env = os.environ if environ is None else environ
        return cls(
            api_key=_first_env_value(env, "NANA_YOUTUBE_API_KEY", "YOUTUBE_API_KEY"),
            oauth_token=_first_env_value(env, "NANA_YOUTUBE_OAUTH_TOKEN", "YOUTUBE_OAUTH_TOKEN"),
        )

    def rest_headers(self) -> dict[str, str]:
        self.validate()
        if self.oauth_token:
            return {"Authorization": f"Bearer {self.oauth_token}"}
        return {"X-Goog-Api-Key": str(self.api_key)}

    def grpc_metadata(self) -> tuple[tuple[str, str], ...]:
        self.validate()
        if self.oauth_token:
            return (("authorization", f"Bearer {self.oauth_token}"),)
        return (("x-goog-api-key", str(self.api_key)),)


@dataclass(frozen=True)
class LiveChatProbeConfig:
    video_id: str
    transport: str = "stream"
    duration_seconds: float = 60.0
    max_responses: int = 0
    request_timeout_seconds: float = 15.0
    reconnect_limit: int = 3
    reconnect_backoff_seconds: float = 1.0

    def validate(self) -> None:
        if not self.video_id.strip():
            raise YouTubeChatTransportError("video_id is required")
        if self.transport not in {"stream", "rest"}:
            raise YouTubeChatTransportError("transport must be stream or rest")
        if self.duration_seconds <= 0:
            raise YouTubeChatTransportError("duration_seconds must be positive")
        if self.max_responses < 0:
            raise YouTubeChatTransportError("max_responses cannot be negative")
        if self.request_timeout_seconds <= 0:
            raise YouTubeChatTransportError("request_timeout_seconds must be positive")
        if self.reconnect_limit < 0:
            raise YouTubeChatTransportError("reconnect_limit cannot be negative")


def _first_env_value(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return None


class YouTubeRestChatTransport:
    """REST transport used as an explicit fallback and deterministic test seam."""

    def __init__(
        self,
        credentials: YouTubeCredentials,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 15.0,
        api_root: str = YOUTUBE_API_ROOT,
    ) -> None:
        credentials.validate()
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.api_root = api_root.rstrip("/")

    def resolve_live_chat_id(self, video_id: str) -> str:
        payload = self._get(
            "/videos",
            params={"part": "liveStreamingDetails", "id": video_id.strip()},
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise YouTubeChatTransportError("no broadcast found for video_id")
        details = items[0].get("liveStreamingDetails") if isinstance(items[0], dict) else None
        live_chat_id = details.get("activeLiveChatId") if isinstance(details, dict) else None
        if not str(live_chat_id or "").strip():
            raise YouTubeChatTransportError("broadcast has no active live chat")
        return str(live_chat_id).strip()

    def list_messages(self, live_chat_id: str, *, page_token: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "part": "snippet,authorDetails",
            "liveChatId": live_chat_id,
            "maxResults": 200,
        }
        if page_token:
            params["pageToken"] = page_token
        return self._get("/liveChat/messages", params=params)

    def _get(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.api_root}{path}",
                params=params,
                headers=self.credentials.rest_headers(),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise YouTubeChatTransportError("YouTube REST request failed") from exc
        except ValueError as exc:
            raise YouTubeChatTransportError("YouTube REST returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise YouTubeChatTransportError("YouTube REST returned a non-object response")
        if "error" in payload:
            raise YouTubeChatTransportError("YouTube REST returned an API error")
        return payload


class YouTubeStreamListTransport:
    """gRPC server-streaming transport for the official ``streamList`` method."""

    def __init__(
        self,
        credentials: YouTubeCredentials,
        *,
        channel_target: str = YOUTUBE_GRPC_TARGET,
        channel_factory: Callable[..., Any] | None = None,
    ) -> None:
        credentials.validate()
        self.credentials = credentials
        self.channel_target = channel_target
        self.channel_factory = channel_factory

    def iter_responses(
        self,
        live_chat_id: str,
        *,
        page_token: str | None = None,
    ) -> Iterator[Any]:
        try:
            import grpc
            from nana.runtime.youtube_stream_list_proto import (
                LiveChatMessageListRequest,
                LiveChatMessageListResponse,
            )
        except ImportError as exc:
            raise YouTubeChatTransportError("grpcio is required for streamList") from exc

        request = LiveChatMessageListRequest(
            live_chat_id=live_chat_id,
            part=["snippet", "authorDetails"],
        )
        if page_token:
            request.page_token = page_token
        channel_factory = self.channel_factory or grpc.secure_channel
        channel = channel_factory(
            self.channel_target,
            grpc.ssl_channel_credentials(),
        )
        call = channel.unary_stream(
            YOUTUBE_STREAM_METHOD,
            request_serializer=LiveChatMessageListRequest.SerializeToString,
            response_deserializer=LiveChatMessageListResponse.FromString,
        )
        try:
            for response in call(request, metadata=self.credentials.grpc_metadata()):
                yield response
        except grpc.RpcError as exc:
            raise YouTubeChatTransportError("YouTube streamList request failed") from exc
        finally:
            channel.close()


def ingest_response_into_probe(
    ingress: YouTubeChatIngress,
    response: Any,
    *,
    received_at: float | None = None,
    bootstrap: bool = False,
    live_chat_id: str | None = None,
) -> IngestResult:
    """Small seam shared by live runners and offline transport tests."""

    return ingress.ingest_response(
        response,
        received_at=received_at,
        bootstrap=bootstrap,
        live_chat_id=live_chat_id,
    )


__all__ = [
    "LiveChatProbeConfig",
    "PHASE",
    "YouTubeChatTransportError",
    "YouTubeCredentials",
    "YouTubeRestChatTransport",
    "YouTubeStreamListTransport",
    "ingest_response_into_probe",
]
