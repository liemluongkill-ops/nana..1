"""Command routing — normalize, suggest, joined-issue detection, route analysis."""
from difflib import get_close_matches
import re

from nana.commands.registry import (
    KNOWN_SLASH_COMMANDS,
    COMMAND_NORMALIZATION_CASES,
    COMMAND_ROUTE_CASES,
)
from nana.commands.router_manifest import classify_command_truth


def normalize_command_text(text):
    stripped = text.strip()
    if not stripped.startswith("/"):
        return stripped
    stripped = re.sub(r"\s*\(\s*ô?ng\s+test\s*\)\s*$", "", stripped, flags=re.IGNORECASE)

    if stripped.startswith("//"):
        stripped = "/" + stripped.lstrip("/")
    if stripped.startswith("/ "):
        stripped = "/" + stripped[2:].lstrip()

    command_aliases = {
        "//br": "/br",
        "/ br": "/br",
        "//br-deep": "/br-deep",
        "/ br-deep": "/br-deep",
        "//browser": "/browser",
        "/ browser": "/browser",
        "//browser-refresh": "/browser-refresh",
        "/ browser-refresh": "/browser-refresh",
        "//browser-refresh-deep": "/browser-refresh-deep",
        "/ browser-refresh-deep": "/browser-refresh-deep",
        "//status": "/status",
        "/ status": "/status",
        "//help": "/help",
        "/ help": "/help",
        "//commands": "/help",
        "/ commands": "/help",
        "//attention": "/attention",
        "/ attention": "/attention",
        "//attention-status": "/attention",
        "/ attention-status": "/attention",
        "//awareness": "/awareness-status",
        "/ awareness": "/awareness-status",
        "//awareness-status": "/awareness-status",
        "/ awareness-status": "/awareness-status",
        "//recovery": "/recovery",
        "/ recovery": "/recovery",
        "//recovery-status": "/recovery",
        "/ recovery-status": "/recovery",
        "//recovery-clear": "/recovery-clear",
        "/ recovery-clear": "/recovery-clear",
        "//memory-health": "/memory-health",
        "/ memory-health": "/memory-health",
        "//memory-review": "/memory-review",
        "/ memory-review": "/memory-review",
        "//memory-labels": "/memory-labels",
        "/ memory-labels": "/memory-labels",
        "//memory-compact-plan": "/memory-compact-plan",
        "/ memory-compact-plan": "/memory-compact-plan",
        "//memory-compact-preview": "/memory-compact-preview",
        "/ memory-compact-preview": "/memory-compact-preview",
        "//memory-action": "/memory-action",
        "/ memory-action": "/memory-action",
        "//memory-confirm": "/memory-confirm",
        "/ memory-confirm": "/memory-confirm",
        "//memory-cancel": "/memory-cancel",
        "/ memory-cancel": "/memory-cancel",
        "//memory-keep": "/memory-keep",
        "/ memory-keep": "/memory-keep",
        "//memory-unkeep": "/memory-unkeep",
        "/ memory-unkeep": "/memory-unkeep",
        "//memory-drop-preview": "/memory-drop-preview",
        "/ memory-drop-preview": "/memory-drop-preview",
        "//memory-drop": "/memory-drop-preview",
        "/ memory-drop": "/memory-drop-preview",
        "//vibe-status": "/vibe-status",
        "/ vibe-status": "/vibe-status",
        "//persona-status": "/vibe-status",
        "/ persona-status": "/vibe-status",
        "//state-log": "/state-log",
        "/ state-log": "/state-log",
        "//vibe-log": "/state-log",
        "/ vibe-log": "/state-log",
        "//clear-state-log": "/clear-state-log",
        "/ clear-state-log": "/clear-state-log",
        "//clear-vibe-log": "/clear-state-log",
        "/ clear-vibe-log": "/clear-state-log",
        "//reset-vibe": "/reset-vibe",
        "/ reset-vibe": "/reset-vibe",
        "//vibe-reset": "/reset-vibe",
        "/ vibe-reset": "/reset-vibe",
        "//focus-mode": "/focus-mode",
        "/ focus-mode": "/focus-mode",
        "//technical-mode": "/technical-mode",
        "/ technical-mode": "/technical-mode",
        "//tech-mode": "/technical-mode",
        "/ tech-mode": "/technical-mode",
        "//social-mode": "/social-mode",
        "/ social-mode": "/social-mode",
        "//chill-mode": "/chill-mode",
        "/ chill-mode": "/chill-mode",
        "//time": "/time",
        "/ time": "/time",
        "//phase3": "/phase3",
        "/ phase3": "/phase3",
        "//phase4": "/phase4",
        "/ phase4": "/phase4",
        "//queue": "/queue",
        "/ queue": "/queue",
        "//actions": "/actions",
        "/ actions": "/actions",
        "//broker-test": "/broker-test",
        "/ broker-test": "/broker-test",
        "//broker-test-edge": "/broker-test-edge",
        "/ broker-test-edge": "/broker-test-edge",
        "//action-propose": "/action-propose",
        "/ action-propose": "/action-propose",
        "//action-propose-edge": "/action-propose-edge",
        "/ action-propose-edge": "/action-propose-edge",
        "//pending-action": "/pending-action",
        "/ pending-action": "/pending-action",
        "//action-plan": "/action-plan",
        "/ action-plan": "/action-plan",
        "//suggest": "/suggest",
        "/ suggest": "/suggest",
        "//next-step": "/next-step",
        "/ next-step": "/next-step",
        "//privacy-test": "/privacy-test",
        "/ privacy-test": "/privacy-test",
        "//context-preview": "/context-preview",
        "/ context-preview": "/context-preview",
        "//context-priority": "/context-priority",
        "/ context-priority": "/context-priority",
        "//context-budget": "/context-budget",
        "/ context-budget": "/context-budget",
        "//vision-preview": "/vision-preview",
        "/ vision-preview": "/vision-preview",
        "//vision-describe": "/vision-describe",
        "/ vision-describe": "/vision-describe",
        "//vision-cache": "/vision-cache",
        "/ vision-cache": "/vision-cache",
        "//vision-cache-clear": "/vision-cache-clear",
        "/ vision-cache-clear": "/vision-cache-clear",
        "//social-draft-vision": "/social-draft-vision",
        "/ social-draft-vision": "/social-draft-vision",
        "//social-target": "/social-target",
        "/ social-target": "/social-target",
        "//target-status": "/social-target",
        "/ target-status": "/social-target",
        "//route-test": "/route-test",
        "/ route-test": "/route-test",
        "//router-test": "/route-test",
        "/ router-test": "/route-test",
        "//model-route": "/route-test",
        "/ model-route": "/route-test",
        "//llmgate-test": "/llmgate-test",
        "/ llmgate-test": "/llmgate-test",
        "//intent-test": "/intent-test",
        "/ intent-test": "/intent-test",
        "//refine-test": "/refine-test",
        "/ refine-test": "/refine-test",
        "//refine-guard-test": "/refine-guard-test",
        "/ refine-guard-test": "/refine-guard-test",
        "//refine-auto-test": "/refine-auto-test",
        "/ refine-auto-test": "/refine-auto-test",
        "//action-confirm": "/action-confirm",
        "/ action-confirm": "/action-confirm",
        "//action-cancel": "/action-cancel",
        "/ action-cancel": "/action-cancel",
        "//presence": "/presence",
        "/ presence": "/presence",
        "//presence-status": "/presence",
        "/ presence-status": "/presence",
        "//presence-rhythm": "/presence",
        "/ presence-rhythm": "/presence",
        "//presence-on": "/presence-on",
        "/ presence-on": "/presence-on",
        "//presence-off": "/presence-off",
        "/ presence-off": "/presence-off",
        "//presence-reset": "/presence-reset",
        "/ presence-reset": "/presence-reset",
        "//dom": "/dom",
        "/ dom": "/dom",
        "//focus": "/focus",
        "/ focus": "/focus",
        "//fx": "/fx",
        "/ fx": "/fx",
        "//reaction-test": "/reaction-test",
        "/ reaction-test": "/reaction-test",
    }
    lowered = stripped.lower()
    if lowered in command_aliases:
        return command_aliases[lowered]
    if lowered.startswith("//run "):
        return "/" + stripped[2:]
    if lowered.startswith("/ run "):
        return "/run " + stripped[6:].strip()
    return stripped


def suggest_slash_command(text):
    first_token = str(text or "").strip().split(maxsplit=1)[0].lower()
    if not first_token.startswith("/"):
        return None
    matches = get_close_matches(first_token, sorted(KNOWN_SLASH_COMMANDS), n=1, cutoff=0.68)
    return matches[0] if matches else None


def command_joined_issue(command_token):
    token = str(command_token or "").strip().lower()
    if not token.startswith("/"):
        return None
    for command in sorted(KNOWN_SLASH_COMMANDS, key=len, reverse=True):
        if token.startswith(command + "/"):
            return f"joined_command:{command}+{token[len(command):]}"
    return None


def command_route_analysis(raw_text):
    raw = str(raw_text or "")
    normalized = normalize_command_text(raw)
    stripped = normalized.strip()
    if not stripped.startswith("/"):
        return {
            "raw": raw,
            "normalized": normalized,
            "status": "chat",
            "base": None,
            "args": stripped,
            "known": False,
            "suggestion": None,
            "joined": None,
        }
    parts = stripped.split(maxsplit=1)
    base = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    known = base in KNOWN_SLASH_COMMANDS
    joined = None if known else command_joined_issue(base)
    status = "known" if known else ("joined_command" if joined else "unknown")
    truth = classify_command_truth(base, known=known)
    return {
        "raw": raw,
        "normalized": normalized,
        "status": status,
        "base": base,
        "args": args,
        "known": known,
        "suggestion": suggest_slash_command(stripped),
        "joined": joined,
        "truth_status": truth.status,
        "truth_reason": truth.reason,
        "truth_replacement": truth.replacement,
    }


def command_route_regression_rows():
    rows = []
    for raw, expected_status, expected_base, expected_joined in COMMAND_ROUTE_CASES:
        result = command_route_analysis(raw)
        passed = (
            result["status"] == expected_status
            and result["base"] == expected_base
            and result["joined"] == expected_joined
        )
        detail = f"got={result['status']} base={result['base']} joined={result['joined'] or 'none'}"
        rows.append((raw, passed, detail))
    return rows


def command_normalize_regression_rows():
    rows = []
    for raw, expected in COMMAND_NORMALIZATION_CASES:
        got = normalize_command_text(raw)
        rows.append((raw, got == expected, f"got={got} expected={expected}"))
    return rows
