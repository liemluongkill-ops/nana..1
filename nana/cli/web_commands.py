"""Local NanaApp browser command boundary."""

from __future__ import annotations

from nana.runtime.nana_web_launcher import (
    NanaWebLaunchResult,
    ensure_nana_web_open,
    format_nana_web_result,
)


def open_nana_web(
    *,
    manual: bool = False,
    reopen: bool = False,
) -> NanaWebLaunchResult:
    try:
        result = ensure_nana_web_open(manual=manual, reopen=reopen)
    except Exception as exc:
        result = NanaWebLaunchResult(
            status="launcher_failed",
            web_ready=False,
            server_started=False,
            browser_opened=False,
            reason=f"{type(exc).__name__}: {exc}",
        )
    print(format_nana_web_result(result))
    return result


def handle_web_command(text_lower: str, *, open_web=open_nana_web) -> bool:
    if text_lower != "/web":
        return False
    open_web(manual=True, reopen=True)
    return True
