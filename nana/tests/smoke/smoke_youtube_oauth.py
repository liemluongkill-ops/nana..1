"""Offline smoke for the CUM3 installed-app OAuth token owner."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Credentials:
    def __init__(
        self,
        *,
        token: str,
        valid: bool,
        expired: bool,
        refresh_token: str | None,
    ) -> None:
        self.token = token
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refresh_calls = 0

    def refresh(self, request) -> None:
        assert request == "google-request"
        self.refresh_calls += 1
        self.token = "refreshed-access-secret"
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        return json.dumps({
            "token": self.token,
            "refresh_token": self.refresh_token,
            "client_id": "client-id",
            "client_secret": "client-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
        })


class _Flow:
    def __init__(self, credentials) -> None:
        self.credentials = credentials
        self.calls: list[dict] = []

    def run_local_server(self, **kwargs):
        self.calls.append(kwargs)
        return self.credentials


def test_missing_token_cache_fails_closed_without_loading_credentials() -> None:
    from nana.runtime.youtube_oauth import YouTubeOAuthTokenProvider

    with tempfile.TemporaryDirectory(prefix="nana-youtube-oauth-") as temp:
        root = Path(temp)
        calls = []
        provider = YouTubeOAuthTokenProvider(
            client_secrets_path=root / "missing-client.json",
            token_path=root / "missing-token.json",
            credentials_loader=lambda *_args, **_kwargs: calls.append("loaded"),
        )
        assert provider() is None
        status = provider.status().to_dict()

    assert calls == []
    assert status["token_cache_present"] is False
    assert status["client_secrets_present"] is False
    assert status["usable"] is False
    assert status["reason_code"] == "token_cache_missing"
    assert "token" not in status and "refresh_token" not in status


def test_valid_cached_credentials_return_token_but_status_hides_secrets() -> None:
    from nana.runtime.youtube_oauth import YouTubeOAuthTokenProvider

    credentials = _Credentials(
        token="cached-access-secret",
        valid=True,
        expired=False,
        refresh_token="cached-refresh-secret",
    )
    with tempfile.TemporaryDirectory(prefix="nana-youtube-oauth-") as temp:
        root = Path(temp)
        token_path = root / "token.json"
        token_path.write_text("{}", encoding="utf-8")
        provider = YouTubeOAuthTokenProvider(
            token_path=token_path,
            credentials_loader=lambda path, scopes: credentials,
        )
        assert provider() == "cached-access-secret"
        status = provider.status().to_dict()

    rendered = json.dumps(status)
    assert status["usable"] is True
    assert status["refresh_available"] is True
    assert status["reason_code"] == "cached_token_valid"
    assert "cached-access-secret" not in rendered
    assert "cached-refresh-secret" not in rendered


def test_expired_credentials_refresh_and_atomically_replace_temp_cache() -> None:
    from nana.runtime.youtube_oauth import YouTubeOAuthTokenProvider

    credentials = _Credentials(
        token="expired-access-secret",
        valid=False,
        expired=True,
        refresh_token="refresh-secret",
    )
    with tempfile.TemporaryDirectory(prefix="nana-youtube-oauth-") as temp:
        root = Path(temp)
        token_path = root / "token.json"
        token_path.write_text('{"old": true}', encoding="utf-8")
        provider = YouTubeOAuthTokenProvider(
            token_path=token_path,
            credentials_loader=lambda path, scopes: credentials,
            request_factory=lambda: "google-request",
        )
        assert provider() == "refreshed-access-secret"
        saved = json.loads(token_path.read_text(encoding="utf-8"))
        leftovers = list(root.glob("*.tmp"))
        status = provider.status().to_dict()

    assert credentials.refresh_calls == 1
    assert saved["token"] == "refreshed-access-secret"
    assert saved["refresh_token"] == "refresh-secret"
    assert leftovers == []
    assert status["usable"] is True
    assert status["reason_code"] == "token_refreshed"


def test_authorize_is_explicit_and_persists_without_returning_token() -> None:
    from nana.runtime.youtube_oauth import YOUTUBE_WRITE_SCOPE, YouTubeOAuthTokenProvider

    credentials = _Credentials(
        token="authorized-access-secret",
        valid=True,
        expired=False,
        refresh_token="authorized-refresh-secret",
    )
    flow = _Flow(credentials)
    factory_calls = []

    def flow_factory(path, scopes):
        factory_calls.append({"path": path, "scopes": scopes})
        return flow

    with tempfile.TemporaryDirectory(prefix="nana-youtube-oauth-") as temp:
        root = Path(temp)
        client_path = root / "client.json"
        token_path = root / "token.json"
        client_path.write_text('{"installed": {}}', encoding="utf-8")
        provider = YouTubeOAuthTokenProvider(
            client_secrets_path=client_path,
            token_path=token_path,
            flow_factory=flow_factory,
        )
        status = provider.authorize(open_browser=False).to_dict()
        saved = json.loads(token_path.read_text(encoding="utf-8"))

    assert factory_calls == [{"path": str(client_path), "scopes": [YOUTUBE_WRITE_SCOPE]}]
    assert flow.calls == [{
        "port": 0,
        "access_type": "offline",
        "prompt": "consent",
        "open_browser": False,
    }]
    assert status["usable"] is True
    assert status["reason_code"] == "authorization_complete"
    assert saved["refresh_token"] == "authorized-refresh-secret"
    assert "authorized-access-secret" not in json.dumps(status)


def test_cli_status_validates_real_cached_credentials_without_printing_token() -> None:
    from nana.runtime.youtube_oauth import YOUTUBE_WRITE_SCOPE, main

    with tempfile.TemporaryDirectory(prefix="nana-youtube-oauth-") as temp:
        token_path = Path(temp) / "authorized-user.json"
        token_path.write_text(json.dumps({
            "token": "real-loader-access-secret",
            "refresh_token": "real-loader-refresh-secret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-id.apps.googleusercontent.com",
            "client_secret": "client-secret",
            "scopes": [YOUTUBE_WRITE_SCOPE],
            "expiry": "2999-01-01T00:00:00Z",
        }), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--token-path", str(token_path)])
        rendered = output.getvalue().strip()
        status = json.loads(rendered)

    assert exit_code == 0
    assert status["usable"] is True
    assert status["reason_code"] == "cached_token_valid"
    assert "real-loader-access-secret" not in rendered
    assert "real-loader-refresh-secret" not in rendered


def main() -> None:
    tests = (
        test_missing_token_cache_fails_closed_without_loading_credentials,
        test_valid_cached_credentials_return_token_but_status_hides_secrets,
        test_expired_credentials_refresh_and_atomically_replace_temp_cache,
        test_authorize_is_explicit_and_persists_without_returning_token,
        test_cli_status_validates_real_cached_credentials_without_printing_token,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_youtube_oauth: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
