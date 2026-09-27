"""Autonomy command routing for the CLI."""

from __future__ import annotations


def handle_autonomy_runtime_command(text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()

    if text_lower in {"/autonomy-status"}:
        from nana.cli.autonomy_command import autonomy_status_snapshot

        snap = autonomy_status_snapshot()
        print("🌙 Autonomy status")
        print(f"  state:        {snap.get('state')}")
        print(f"  paused:       {snap.get('paused')}")
        print(f"  output:       {'enabled' if snap.get('output_enabled') else 'suppressed'}")
        print(f"  tick_count:   {snap.get('tick_count')}")
        print(f"  accepted:     {snap.get('accepted')}")
        print(f"  rejected:     {snap.get('rejected')}")
        last = snap.get("last_event")
        if last is not None:
            print(
                f"  last_event:   t={last.t_monotonic:.1f}s mode={last.mode} "
                f"accepted={last.accepted} reason={last.reason}"
            )

        print()
        print("  Diagnostics:")
        top = snap.get("top_reject_reasons", [])
        if top:
            print("  Top Reject Reasons:")
            for rank, (reason, count) in enumerate(top, 1):
                prefix = reason.split("/", 1) if "/" in reason else (reason, "")
                gate_or_cadence = prefix[0]
                detail = prefix[1] if len(prefix) > 1 else ""
                print(f"    {rank}. [{gate_or_cadence.upper()}] {detail or reason} — {count}x")
        else:
            print("  Top Reject Reasons: (none yet)")

        gate_d = snap.get("gate_diagnostics")
        if gate_d:
            print()
            print("  Gate Diagnostics (live):")
            hard = gate_d.get("hard_check", {})
            hard_passed = hard.get("passed", True)
            hard_failed = hard.get("reason_if_failed")
            print(
                f"  Hard gates:     {'ALL PASSED' if hard_passed else 'BLOCKED — ' + (hard_failed or 'unknown')}"
            )
            intens = gate_d.get("intensity", {})
            score = intens.get("score", 0)
            thr_full = intens.get("threshold_full", 0.70)
            thr_vts = intens.get("threshold_vts_only", 0.40)
            level_r = intens.get("level_result", "?")
            print(
                f"  Intensity:      {score:.3f} "
                f"(VTS>= {thr_vts:.2f} | Full>= {thr_full:.2f}) → {level_r}"
            )
            comps = intens.get("components", {})
            weights = intens.get("weights", {})
            contribs = {
                k: round(comps.get(k, 0) * weights.get(k, 0), 3)
                for k in comps
            }
            biggest = max(contribs, key=contribs.get) if contribs else "n/a"
            print(
                f"  Intensity contribs: mood={contribs.get('mood', 0):.3f} "
                f"relevance={contribs.get('relevance', 0):.3f} "
                f"silence={contribs.get('silence', 0):.3f} "
                f"jitter={contribs.get('jitter', 0):.3f}"
            )
            print(f"  Biggest driver: {biggest}")
            print(f"  Suggestion: {gate_d.get('suggestion', 'n/a')}")

        print()
        return True

    if text_lower in {"/autonomy-pause"}:
        from nana.cli.globals import AUTONOMY_LOOP

        AUTONOMY_LOOP.pause()
        print("🌙 Autonomy paused. Idle banter, observer aware, and stream host suppressed.")
        print("  Use /autonomy-resume to re-enable.")
        return True

    if text_lower in {"/autonomy-resume"}:
        from nana.cli.globals import AUTONOMY_LOOP

        AUTONOMY_LOOP.resume()
        print("🌙 Autonomy resumed. Nana will start idle banter at next cycle.")
        return True

    if text_lower.startswith("/autonomy-mode"):
        from nana.cli.autonomy_command import autonomy_set_mode
        from nana.cli.globals import AUTONOMY_EXPRESS

        rest = text[len("/autonomy-mode"):].strip()
        if not rest:
            cur = "ultra_short" if AUTONOMY_EXPRESS.prefer_ultra_short else "full"
            print(f"🌙 Autonomy mode: {cur}")
            print("  /autonomy-mode ultra-short   (ưu tiên câu ngắn)")
            print("  /autonomy-mode full          (chạy full pool)")
        else:
            new_mode = autonomy_set_mode(rest)
            if new_mode == "unknown":
                print(f"  [autonomy] unknown mode {rest!r}. Use ultra-short|full.")
            else:
                print(f"🌙 Autonomy mode -> {new_mode}")
        return True

    return False


__all__ = ["handle_autonomy_runtime_command"]
