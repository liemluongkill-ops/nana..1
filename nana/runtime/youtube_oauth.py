"""Installed-app OAuth owner for CUM3 YouTube write access.

Authorization is always an explicit CLI/operator action. Normal send paths only
load or refresh an existing authorized-user cache outside the repository. No
status or exception returned by this module contains access or refresh tokens.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable, Sequence


YOUTUBE_WRITE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
PHASE = "STREAM-V1-CUM3-YOUTUBE-OAUTH"


def _default_token_path() -> Path:
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Nana" / "youtube_oauth_authorized_user.json"


def _configured_path(name: str) -> Path | None:
    raw = str(os.getenv(name) or "").strip()
    return Path(raw) if raw else None


def _load_google_credentials(path: str, scopes: list[str]):
    from google.oauth2.credentials import Credentials

    return Credentials.from_authorized_user_file(path, scopes)


def _google_request():
    from google.auth.transport.requests import Request

    return Request()


def _google_flow(path: str, scopes: list[str]):
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_secrets_file(path, scopes)


@dataclass(frozen=True)
class YouTubeOAuthStatus:
    client_secrets_present: bool
    token_cache_present: bool
    usable: bool
    refresh_available: bool
    reason_code: str
    scope: str = YOUTUBE_WRITE_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class YouTubeOAuthTokenProvider:
    """Load, refresh, or explicitly authorize one YouTube write grant."""

    def __init__(
        self,
        *,
        client_secrets_path: str | os.PathLike[str] | None = None,
        token_path: str | os.PathLike[str] | None = None,
        credentials_loader: Callable[[str, list[str]], Any] | None = None,
        request_factory: Callable[[], Any] | None = None,
        flow_factory: Callable[[str, list[str]], Any] | None = None,
    ) -> None:
        configured_client = _configured_path("NANA_YOUTUBE_OAUTH_CLIENT_SECRETS_PATH")
        configured_token = _configured_path("NANA_YOUTUBE_OAUTH_TOKEN_PATH")
        self.client_secrets_path = (
            Path(client_secrets_path) if client_secrets_path is not None else configured_client
        )
        self.token_path = Path(token_path) if token_path is not None else (configured_token or _default_token_path())
        self.credentials_loader = credentials_loader or _load_google_credentials
        self.request_factory = request_factory or _google_request
        self.flow_factory = flow_factory or _google_flow
        self._lock = threading.RLock()
        self._last_status: YouTubeOAuthStatus | None = None

    def _status(
        self,
        reason_code: str,
        *,
        usable: bool = False,
        refresh_available: bool = False,
    ) -> YouTubeOAuthStatus:
        status = YouTubeOAuthStatus(
            client_secrets_present=bool(
                self.client_secrets_path is not None and self.client_secrets_path.is_file()
            ),
            token_cache_present=self.token_path.is_file(),
            usable=usable,
            refresh_available=refresh_available,
            reason_code=reason_code,
        )
        self._last_status = status
        return status

    def status(self) -> YouTubeOAuthStatus:
        with self._lock:
            if self._last_status is not None:
                return self._last_status
            reason = "token_cache_present" if self.token_path.is_file() else "token_cache_missing"
            return self._status(reason)

    def __call__(self) -> str | None:
        with self._lock:
            if not self.token_path.is_file():
                self._status("token_cache_missing")
                return None
            try:
                credentials = self.credentials_loader(
                    str(self.token_path),
                    [YOUTUBE_WRITE_SCOPE],
                )
            except Exception:
                self._status("token_cache_invalid")
                return None

            refresh_available = bool(getattr(credentials, "refresh_token", None))
            if bool(getattr(credentials, "expired", False)):
                if not refresh_available:
                    self._status("refresh_token_missing")
                    return None
                try:
                    credentials.refresh(self.request_factory())
                    self._write_credentials(credentials)
                except Exception:
                    self._status("token_refresh_failed", refresh_available=True)
                    return None
                token = str(getattr(credentials, "token", "") or "").strip()
                valid = bool(getattr(credentials, "valid", False)) and bool(token)
                self._status(
                    "token_refreshed" if valid else "token_refresh_failed",
                    usable=valid,
                    refresh_available=refresh_available,
                )
                return token if valid else None

            token = str(getattr(credentials, "token", "") or "").strip()
            valid = bool(getattr(credentials, "valid", False)) and bool(token)
            self._status(
                "cached_token_valid" if valid else "cached_token_invalid",
                usable=valid,
                refresh_available=refresh_available,
            )
            return token if valid else None

    def authorize(self, *, open_browser: bool = True) -> YouTubeOAuthStatus:
        """Run the system-browser consent flow only on an explicit call."""

        with self._lock:
            if self.client_secrets_path is None or not self.client_secrets_path.is_file():
                return self._status("client_secrets_missing")
            try:
                flow = self.flow_factory(
                    str(self.client_secrets_path),
                    [YOUTUBE_WRITE_SCOPE],
                )
                credentials = flow.run_local_server(
                    port=0,
                    access_type="offline",
                    prompt="consent",
                    open_browser=open_browser,
                )
            except Exception:
                return self._status("authorization_failed")
            token = str(getattr(credentials, "token", "") or "").strip()
            refresh_available = bool(getattr(credentials, "refresh_token", None))
            if not token:
                return self._status("access_token_missing", refresh_available=refresh_available)
            if not refresh_available:
                return self._status("refresh_token_missing")
            try:
                self._write_credentials(credentials)
            except Exception:
                return self._status("token_cache_write_failed", refresh_available=True)
            return self._status(
                "authorization_complete",
                usable=True,
                refresh_available=True,
            )

    def _write_credentials(self, credentials: Any) -> None:
        payload = credentials.to_json()
        if not isinstance(payload, str) or not payload.strip():
            raise ValueError("empty credential payload")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.token_path.with_name(
            f".{self.token_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temp_path.write_text(payload, encoding="utf-8")
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, self.token_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


_DEFAULT_PROVIDER: YouTubeOAuthTokenProvider | None = None
_DEFAULT_PROVIDER_LOCK = threading.Lock()


def get_youtube_oauth_provider() -> YouTubeOAuthTokenProvider:
    global _DEFAULT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        with _DEFAULT_PROVIDER_LOCK:
            if _DEFAULT_PROVIDER is None:
                _DEFAULT_PROVIDER = YouTubeOAuthTokenProvider()
    return _DEFAULT_PROVIDER


def get_youtube_oauth_token() -> str | None:
    return get_youtube_oauth_provider()()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Nana's local YouTube write OAuth grant.")
    parser.add_argument("--authorize", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--client-secrets")
    parser.add_argument("--token-path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    provider = YouTubeOAuthTokenProvider(
        client_secrets_path=args.client_secrets,
        token_path=args.token_path,
    )
    if args.authorize:
        status = provider.authorize(open_browser=not args.no_browser)
    else:
        provider()
        status = provider.status()
    print(json.dumps({"phase": PHASE, **status.to_dict()}))
    return 0 if status.usable else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PHASE",
    "YOUTUBE_WRITE_SCOPE",
    "YouTubeOAuthStatus",
    "YouTubeOAuthTokenProvider",
    "get_youtube_oauth_provider",
    "get_youtube_oauth_token",
    "main",
]
