"""Vision command helpers for the new Nana runtime."""
from __future__ import annotations

import time

from nana.browser.vision import VisionPreviewer
from nana.core.context import broker_context_snapshot
from nana.core.recovery import recovery_notice
from nana.core.vision import set_last_vision_description
from nana.runtime.recovery import vision_recovery_kind, recovery_clear_vision


_vision_previewer = VisionPreviewer()


def print_social_draft_vision(raw_text):
    """Print social draft using the latest manual vision description."""
    from nana.core.vision import LAST_VISION_DESCRIPTION
    from nana.social.draft import print_social_draft_test

    print("📝 Social Draft + Vision")
    if not LAST_VISION_DESCRIPTION:
        print("  Status: skipped")
        print("  Reason: no_vision_description")
        print(f"  Recovery: {recovery_notice('vision_no_preview', cooldown=False)}")
        return
    age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
    if age > 180:
        print("  Status: skipped")
        print("  Reason: vision_stale")
        print(f"  Age: {age:.1f}s")
        print(f"  Recovery: {recovery_notice('vision_stale', cooldown=False)}")
        return
    print(f"  Vision age: {age:.1f}s")
    print_social_draft_test(
        raw_text,
        vision_description=LAST_VISION_DESCRIPTION.get("text"),
    )


def print_vision_preview():
    """Capture a local browser focus crop. No model call."""
    removed = _vision_previewer.cleanup_preview_cache()
    context = broker_context_snapshot(force_edge=True)
    result = _vision_previewer.capture_focus(context)
    print("👁️ Vision Preview")
    print(f"  Status: {result.get('status')}")
    print(f"  Reason: {result.get('reason')}")
    print(f"  Privacy risk: {result.get('privacy_risk')}")
    if removed:
        print(f"  Cache cleanup: removed {removed} old preview(s)")
    if result.get("status") != "captured":
        recovery_kind = vision_recovery_kind(result.get("reason"))
        if recovery_kind:
            print(
                "  Recovery: "
                f"{recovery_notice(recovery_kind, detail=result.get('reason'), cooldown=False)}"
            )
    if result.get("blocked_reasons"):
        print(f"  Blocked by: {', '.join(result.get('blocked_reasons'))}")
    if result.get("target"):
        print(f"  Target: {result.get('target')}")
    if result.get("clip"):
        clip = result.get("clip") or {}
        print(
            "  Clip: "
            f"x={clip.get('x')} y={clip.get('y')} "
            f"w={clip.get('width')} h={clip.get('height')} "
            f"scale={clip.get('scale')}"
        )
    if result.get("path"):
        print(f"  File: {result.get('path')}")
    if result.get("note"):
        print(f"  Note: {result.get('note')}")
    if result.get("status") == "captured":
        cleared = recovery_clear_vision(reason="vision_preview_captured")
        if cleared:
            print(f"  Recovery cleared: {', '.join(cleared)}")


def print_vision_describe():
    """Describe the latest local crop via the configured LLMGate vision model."""
    from nana.brain.llmgate_client import call_llmgate_vision

    path = _vision_previewer.latest_preview()
    print("👁️ Vision Describe")
    if not path:
        print("  Status: skipped")
        print("  Reason: no_preview_image")
        print(f"  Recovery: {recovery_notice('vision_no_preview', cooldown=False)}")
        return

    prompt = (
        "Mô tả vùng crop này trong 1-2 dòng tiếng Việt tự nhiên. "
        "Chỉ nói thứ nhìn thấy rõ; không đoán danh tính, không khuyên click, không đề xuất hành động."
    )
    description = None
    debug = "no_model_attempted"
    used_model = None
    for model_name in ["gpt-5.5", "gpt-5.4"]:
        description, debug = call_llmgate_vision(
            model_name,
            prompt,
            path,
            max_tokens=120,
            temperature=0.1,
        )
        if description:
            used_model = model_name
            break

    print(f"  File: {path}")
    print(f"  Model: {used_model or 'None'}")
    print(f"  Debug: {debug}")
    if not description:
        print("  Status: skipped")
        print("  Reason: vision_model_failed_or_unsupported")
        print(f"  Recovery: {recovery_notice('vision_model_failed', detail=debug, cooldown=False)}")
        return
    print("  Status: described")
    print("  Safety: manual_trigger_only, latest_local_crop, no_action_control, no_post")
    print(f"  Description: {description}")
    cleared = recovery_clear_vision(reason="vision_described")
    if cleared:
        print(f"  Recovery cleared: {', '.join(cleared)}")
    set_last_vision_description({
        "text": description,
        "path": str(path),
        "model": used_model,
        "time": time.time(),
    })


def print_vision_cache_state():
    output_dir = _vision_previewer.output_dir
    files = []
    if output_dir.exists():
        files = sorted(
            output_dir.glob("vision_focus_*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    print("🧹 Vision Cache")
    print(f"  Dir: {output_dir}")
    print(f"  Files: {len(files)}")
    if files:
        newest = files[0]
        age = time.time() - newest.stat().st_mtime
        print(f"  Latest: {newest.name} ({age:.1f}s ago)")


def clear_vision_preview_cache():
    return _vision_previewer.clear_preview_cache()
