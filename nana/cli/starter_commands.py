"""Starter proposal, controlled send, and auto-lite command router."""

from __future__ import annotations

from collections.abc import Iterable

from nana.runtime.starter_auto import (
    starter_auto_disable_lines,
    starter_auto_enable_lines,
    starter_auto_preview_lines,
    starter_auto_status_lines,
    starter_auto_tick_lines,
    starter_auto_worker_start_lines,
    starter_auto_worker_stop_lines,
)
from nana.runtime.starter_proposals import (
    generate_starter_proposal,
    get_proposal_store,
    proposal_preview,
    proposal_status,
)
from nana.runtime.starter_send import (
    starter_send_disable_lines,
    starter_send_enable_lines,
    starter_send_lines,
    starter_send_preview_lines,
    starter_send_status_lines,
)


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def _arg_after_space(text: str, default: str = "") -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else default


def handle_starter_command(text: str, text_lower: str | None = None) -> bool:
    """Handle STAGE-8E/8F/8G starter command surfaces."""

    text_lower = text_lower or text.lower()

    if text_lower in {"/starter-proposals", "/starter-proposal-status"}:
        _print_lines(proposal_status())
        return True

    if text_lower in {"/starter-proposal-preview", "/proposal-preview"}:
        _print_lines(proposal_preview())
        return True

    if text_lower in {"/starter-proposal-create", "/proposal-create", "/starter-proposal-generate"}:
        proposal = generate_starter_proposal()
        if proposal is None:
            print("  [8E] No proposal created (stream/policy/starter gate blocked).")
        else:
            print(f"  [8E] Proposal {proposal.id}: PENDING")
            print(f"  Text: {proposal.text[:180]}")
            print("  Next: /starter-proposal-approve <id> (approval still does NOT send)")
        return True

    if text_lower.startswith("/starter-proposal-approve ") or text_lower == "/starter-proposal-approve":
        proposal_id = _arg_after_space(text)
        if not proposal_id:
            print("  Usage: /starter-proposal-approve <id>")
        else:
            store = get_proposal_store()
            ok, reason = store.approve(proposal_id)
            if ok:
                proposal = store.get_proposal(proposal_id)
                print(f"  [8E] Proposal {proposal_id}: APPROVED")
                if proposal:
                    print(f"  Text: {proposal.text[:120]}")
                print("  Note: approved — NOT sent (no auto-send in STAGE-8E)")
            else:
                print(f"  [8E] Approve failed: {reason}")
        return True

    if text_lower in {"/starter-send-status", "/starter-outbox-status"}:
        _print_lines(starter_send_status_lines())
        return True

    if text_lower in {"/starter-send-enable", "/starter-outbox-enable"}:
        _print_lines(starter_send_enable_lines())
        return True

    if text_lower in {"/starter-send-disable", "/starter-outbox-disable"}:
        _print_lines(starter_send_disable_lines())
        return True

    if text_lower.startswith("/starter-proposal-send-preview ") or text_lower == "/starter-proposal-send-preview":
        proposal_id = _arg_after_space(text)
        if not proposal_id:
            print("  Usage: /starter-proposal-send-preview <id>")
        else:
            _print_lines(starter_send_preview_lines(proposal_id))
        return True

    if text_lower.startswith("/starter-proposal-send ") or text_lower == "/starter-proposal-send":
        proposal_id = _arg_after_space(text)
        if not proposal_id:
            print("  Usage: /starter-proposal-send <id>")
        else:
            _print_lines(starter_send_lines(proposal_id))
        return True

    if text_lower.startswith("/starter-proposal-cancel ") or text_lower == "/starter-proposal-cancel":
        proposal_id = _arg_after_space(text)
        if not proposal_id:
            print("  Usage: /starter-proposal-cancel <id>")
        else:
            store = get_proposal_store()
            ok, reason = store.cancel(proposal_id)
            if ok:
                print(f"  [8E] Proposal {proposal_id}: CANCELLED")
            else:
                print(f"  [8E] Cancel failed: {reason}")
        return True

    if text_lower in {"/starter-proposal-clear", "/proposal-clear"}:
        store = get_proposal_store()
        removed = store.clear()
        print(f"  [8E] Cleared {removed} expired/cancelled proposal(s)")
        return True

    if text_lower in {"/starter-auto-status", "/starter-autolite-status"}:
        _print_lines(starter_auto_status_lines())
        return True

    if text_lower in {"/starter-auto-enable", "/starter-autolite-enable"}:
        _print_lines(starter_auto_enable_lines())
        return True

    if text_lower in {"/starter-auto-disable", "/starter-autolite-disable"}:
        _print_lines(starter_auto_disable_lines())
        return True

    if text_lower in {"/starter-auto-preview", "/starter-autolite-preview"}:
        _print_lines(starter_auto_preview_lines())
        return True

    if text_lower in {"/starter-auto-tick", "/starter-autolite-tick"}:
        _print_lines(starter_auto_tick_lines())
        return True

    if text_lower in {"/starter-auto-worker-start", "/starter-autolite-worker-start"}:
        _print_lines(starter_auto_worker_start_lines())
        return True

    if text_lower in {"/starter-auto-worker-stop", "/starter-autolite-worker-stop"}:
        _print_lines(starter_auto_worker_stop_lines())
        return True

    return False
