"""Public, viewer, bridge, and model route status panels for Nana."""

from __future__ import annotations

def print_persona_boundary_status() -> None:
    from nana.runtime.persona_boundary import persona_boundary_status_lines

    for line in persona_boundary_status_lines():
        print(line)

def print_persona_spine_status() -> None:
    from nana.runtime.persona_spine import spine_status

    for line in spine_status():
        print(line)

def print_core_self_status() -> None:
    from nana.runtime.core_self import core_self_status_lines

    for line in core_self_status_lines():
        print(line)

def print_lane_affect_status() -> None:
    from nana.runtime.affect_lane import status_lines

    for line in status_lines():
        print(line)

def print_persona_boundary_test(scope: str = "public", name: str = "viewer", message: str = "hello") -> None:
    from nana.runtime.persona_boundary import persona_boundary_test_report

    for line in persona_boundary_test_report(scope=scope, name=name, message=message):
        print(line)

def print_viewer_chat_status() -> None:
    from nana.runtime.viewer_chat import viewer_chat_status_lines

    for line in viewer_chat_status_lines():
        print(line)

def print_viewer_chat_test(
    name: str = "viewer",
    message: str = "hello Nana",
    *,
    platform: str = "discord",
    channel: str = "chung",
) -> None:
    from nana.runtime.viewer_chat import viewer_chat_test_report

    for line in viewer_chat_test_report(name=name, message=message, platform=platform, channel=channel):
        print(line)

def print_viewer_chat_clear() -> None:
    from nana.runtime.viewer_chat import viewer_chat_clear_report

    for line in viewer_chat_clear_report():
        print(line)

def print_social_session_status() -> None:
    from nana.runtime.social_session import social_session_status_lines

    for line in social_session_status_lines():
        print(line)

def print_memory_grounding_status() -> None:
    print("🧠 Memory Grounding Status")
    print("  Mode: CORE-MEMORY-GROUNDING-1 | read_only=True | can_act=False")
    try:
        from nana.runtime.memory_grounding import MemoryClaimDetector, EvidenceBuilder, ConfidenceVerifier
    except Exception:
        print("  Module: unavailable")
        print("  Safety: no LLM judge | no disk write | no live action")
        return
    try:
        detector = MemoryClaimDetector()
        builder = EvidenceBuilder(lane="operator_backstage")
        verifier = ConfidenceVerifier()
        sample_text = "hôm qua mình sửa bridge tới 4 giờ sáng đúng không?"
        evidence = builder.build(query=sample_text, claim_text=sample_text)
        sample_verdict = verifier.verify(evidence, "Đúng rồi Ba, token expire lúc 4 giờ sáng.")
        print(f"  Detector: ready | sample_claim={detector.is_memory_claim(sample_text)}")
        print(
            "  Evidence: "
            f"status={evidence.status} | confidence={evidence.confidence:.2f} | "
            f"visibility={evidence.lane_visible_to} | strength={evidence.evidence_strength} | "
            f"snippets={len(evidence.snippets)}"
        )
        print(
            "  Verifier: "
            f"sample_passed={sample_verdict.passed} | reason={sample_verdict.fail_reason or sample_verdict.warn_reason or 'none'}"
        )
        print("  Lanes: private_owner uses private/public evidence | public_stage uses public_safe only | operator sees evidence state")
        print("  Rule: not_found/user_claim_only must not be affirmed as memory.")
    except Exception as exc:
        print(f"  Module: error={type(exc).__name__}")
    print("  Safety: no LLM judge | no disk write | no live action | no TTS/VTS/OBS/game input")

def print_social_starter_status() -> None:
    from nana.runtime.social_starters import social_starter_status_lines

    for line in social_starter_status_lines():
        print(line)

def print_social_starter_preview() -> None:
    from nana.runtime.social_starters import get_social_starter

    snap = get_social_starter().snapshot()
    result = get_social_starter().preview()
    print("💬 Social Starter Preview")
    print(f"  Mode: {snap.get('phase')} | read_only=True | preview_only=True")
    print(f"  Eligible: {result.get('eligible')}")
    print(f"  Blocked: {result.get('blocked')} | reason={result.get('blocked_reason') or 'none'}")
    print(f"  Room topic: {result.get('room_topic') or 'none'} | velocity={result.get('chat_velocity', 0.0):.2f}/s")
    print(f"  Idle: {result.get('idle_seconds', 0):.0f}s | cooldown_remaining: {result.get('cooldown_remaining', 0):.0f}s")
    print(f"  Count: {result.get('starter_count')}")
    starter = result.get("starter")
    if starter:
        print(f"  Preview: \"{starter}\"")
    else:
        print(f"  Preview: (blocked — no starter generated)")

def print_public_quality_status() -> None:
    from nana.runtime.public_quality import public_quality_status_lines

    for line in public_quality_status_lines():
        print(line)

def print_public_quality_preview(text: str, viewer: str = "viewer", vibe: str = "quiet_room") -> None:
    from nana.runtime.public_quality import get_public_quality_guard
    from nana.runtime.social_session import get_social_session

    guard = get_public_quality_guard()
    last_addressed = get_social_session()._last_addressed_viewer or None
    preview = guard.preview_guard(
        text,
        viewer_name=viewer,
        room_vibe=vibe,
        last_addressed_viewer=last_addressed,
    )
    print("🛡️ Public Reply Quality Preview")
    print(f"  Input viewer: {viewer} | room_vibe: {vibe} | last_addressed: {last_addressed or 'none'}")
    print(f"  Original: \"{preview['original']}\"")
    print(f"  Would: rewrite={preview['would_rewrite']} | truncate={preview['would_truncate']} | fallback={preview['would_fallback']}")
    print(f"  Max chars for vibe: {preview['max_chars']}")
    if preview["violations"]:
        print(f"  Violations ({len(preview['violations'])}):")
        for v in preview["violations"]:
            print(f"    - [{v['kind']}] {v['detail']}")
    print(f"  Preview: \"{preview['preview_text']}\"")

def print_public_stage_status() -> None:
    from nana.runtime.public_stage_identity import public_stage_identity_status_lines

    for line in public_stage_identity_status_lines():
        print(line)

def print_public_stage_preview(text: str, viewer_name: str = "viewer") -> None:
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    guard = get_public_stage_identity_guard()
    classification = guard.classify_public_input(text)
    firewall_line = guard.public_command_firewall(text)
    rewrite_result = guard.rewrite_public_stage_reply(text, viewer_name=viewer_name)
    print("🎭 Public Stage Identity Preview")
    print(f"  Input: \"{text}\"")
    print(f"  Viewer: \"{viewer_name}\"")
    print(f"  Classification: {classification}")
    if firewall_line is not None:
        print(f"  Firewall line: \"{firewall_line}\"")
    else:
        print(f"  Firewall line: (none — not a backstage command)")
    print(f"  Original: \"{rewrite_result.original}\"")
    if rewrite_result.violations:
        print(f"  Violations ({len(rewrite_result.violations)}):")
        for v in rewrite_result.violations:
            kind = getattr(v, "kind", "<unknown>")
            detail = getattr(v, "detail", "")
            action = getattr(v, "action", "")
            print(f"    - [{kind}] {detail} → {action}")
    print(f"  After stage identity: \"{rewrite_result.text}\"")
    snap = guard.snapshot()
    stats = dict(snap.get("stats") or {})
    variety = dict(snap.get("variety") or {})
    print(
        "  Stats: "
        f"command_blocks={stats.get('backstage_command_blocks', 0)} | "
        f"assistant_rewrites={stats.get('assistant_tone_rewrites', 0)} | "
        f"residue_replaced={stats.get('residue_replaced', 0)} | "
        f"name_throttled={stats.get('name_throttle_stripped', 0)} | "
        f"variety_swaps={stats.get('variety_swaps', 0)} | "
        f"last={snap.get('last_action')}"
    )
    print(
        "  Variety: "
        f"recent_themes={variety.get('recent_fallback_window', 0)} | "
        f"recent_names={variety.get('recent_name_window', 0)}"
    )

def print_external_bridge_status() -> None:
    from nana.runtime.external_bridge import external_bridge_status_lines

    for line in external_bridge_status_lines():
        print(line)

def print_external_bridge_test(name: str = "linh", message: str = "hello Nana") -> None:
    from nana.runtime.external_bridge import external_bridge_test_report

    for line in external_bridge_test_report(name=name, message=message):
        print(line)

def print_llm_route_status() -> None:
    from nana.runtime.llm_route_status import llm_route_status_lines

    for line in llm_route_status_lines():
        print(line)

def print_llm_route_probe(args: str = "") -> None:
    from nana.runtime.llm_route_status import llm_route_probe_lines

    for line in llm_route_probe_lines(args):
        print(line)

def print_llm_route_bakeoff(args: str = "") -> None:
    from nana.runtime.llm_route_status import llm_route_bakeoff_lines

    for line in llm_route_bakeoff_lines(args):
        print(line)
