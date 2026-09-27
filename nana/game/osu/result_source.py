"""
osu! Result Source — Phase 18.

Read-only bridge status helper for result auto pipeline.
Tries to extract result-like fields from the existing tosu bridge state.

Public result fields extracted (if available):
  score, accuracy, misses, combo, max_combo, rank, passed
  song_title, difficulty, activity_state (result/menu/gameplay/unknown)

Does NOT:
- call ElevenLabs
- call VTS
- call OBS API
- add osu input
- move/click mouse
- submit score
- add Vision
- change Stardew
- write any file

No live input. No VTS. No ElevenLabs. No OBS API. No mouse/click. No submit.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Public-safe result field names
# ---------------------------------------------------------------------------
_RESULT_PUBLIC_FIELDS = frozenset({
    "score",
    "accuracy",
    "misses",
    "misscount",
    "combo",
    "max_combo",
    "maxcombo",
    "rank",
    "grade",
    "passed",
})


def _dig(data: Any, *paths: str) -> Any:
    """Safely navigate nested dict/list by dot-separated path segments."""
    for path in paths:
        if data is None:
            return None
        cur = data
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit():
                idx = int(part)
                if 0 <= idx < len(cur):
                    cur = cur[idx]
                else:
                    ok = False
                    break
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _try_float(value: Any) -> float | None:
    """Try to convert a value to float, return None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _try_int(value: Any) -> int | None:
    """Try to convert a value to int, return None on failure."""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_result_fields_from_bridge(state: dict[str, Any]) -> dict[str, Any]:
    """
    Extract public-safe result fields from a tosu bridge state dict.

    Looks in common tosu JSON paths for result-like fields.
    Returns only fields that are present and successfully parsed.

    Does NOT fake or infer missing fields.
    """
    raw = state.get("raw") or {}

    fields: dict[str, Any] = {}

    # Score
    score = _dig(
        raw,
        "play.score",
        "resultsScreen.score",
        "gameplay.score",
        "score",
        "totalScore",
        "gameplay.totalScore",
    )
    parsed = _try_int(score)
    if parsed is not None:
        fields["score"] = parsed

    # Accuracy (stored as decimal, e.g. 0.992 for 99.2%)
    accuracy = _dig(
        raw,
        "play.accuracy",
        "resultsScreen.accuracy",
        "gameplay.accuracy",
        "accuracy",
        "hitAccuracy",
        "gameplay.accuracy",
    )
    parsed = _try_float(accuracy)
    if parsed is not None:
        # Normalize: if already a percentage (e.g. 99.2), use as-is.
        # If a ratio (e.g. 0.992), convert to percentage.
        if parsed <= 1.0:
            fields["accuracy"] = round(parsed * 100, 2)
        else:
            fields["accuracy"] = round(parsed, 2)

    # Misses / misscount
    misses = _dig(
        raw,
        "play.miss",
        "play.misses",
        "resultsScreen.misses",
        "gameplay.miss",
        "gameplay.misses",
        "misses",
        "misscount",
        "nMiss",
    )
    parsed = _try_int(misses)
    if parsed is not None:
        fields["misses"] = parsed

    # Combo
    combo = _dig(
        raw,
        "play.combo",
        "resultsScreen.combo",
        "gameplay.combo",
        "combo",
        "currentCombo",
        "maxCombo",
    )
    parsed = _try_int(combo)
    if parsed is not None:
        fields["combo"] = parsed

    # Max combo
    max_combo = _dig(
        raw,
        "play.max_combo",
        "resultsScreen.maxCombo",
        "gameplay.maxCombo",
        "max_combo",
        "maxcombo",
        "maxCombo",
        "maximumCombo",
    )
    parsed = _try_int(max_combo)
    if parsed is not None:
        fields["max_combo"] = parsed

    # Rank / grade (tosu uses "rank" or "grade")
    rank = _dig(
        raw,
        "play.rank",
        "resultsScreen.rank",
        "gameplay.rank",
        "rank",
        "grade",
        "resultsScreen.grade",
    )
    if rank is not None and isinstance(rank, str):
        rank_str = str(rank).strip().upper()
        if rank_str:
            fields["rank"] = rank_str

    # Passed
    passed = _dig(
        raw,
        "play.passed",
        "resultsScreen.passed",
        "gameplay.passed",
        "passed",
        "completed",
        "isPassed",
    )
    if passed is not None:
        if isinstance(passed, bool):
            fields["passed"] = passed
        elif isinstance(passed, (int, float)):
            fields["passed"] = bool(passed)
        elif isinstance(passed, str):
            fields["passed"] = passed.strip().lower() in {"true", "1", "yes", "on"}

    return fields


def _extract_song_metadata(state: dict[str, Any]) -> tuple[str, str]:
    """Extract song title and difficulty from bridge state."""
    beatmap = state.get("beatmap") or {}
    title = beatmap.get("title") or "unknown"
    difficulty = beatmap.get("version") or "unknown"
    return str(title), str(difficulty)


def _detect_activity_state(state: dict[str, Any]) -> str:
    """
    Detect activity state from bridge state.

    Tries to read screen/menu/gameplay state from tosu JSON.
    Falls back to 'unknown' if not determinable.
    """
    raw = state.get("raw") or {}

    # Screen state paths used by tosu
    screen = _dig(raw, "menu.state", "menu.gameMode", "osu!.state", "state", "screen")
    if screen is not None:
        screen_str = str(screen).lower()
        if "result" in screen_str or "results" in screen_str:
            return "result"
        if "select" in screen_str or "songselect" in screen_str or "menu" in screen_str:
            return "menu"
        if "play" in screen_str or "gameplay" in screen_str:
            return "gameplay"
        if "break" in screen_str:
            return "break"

    # Numeric mode: 0=menu, 1=play, 2=edit, 3=gamemode_select, etc.
    mode = _dig(raw, "menu.bm.time.current", "play.time.current", "gameMode")
    if mode is not None:
        mode_int = _try_int(mode)
        if mode_int == 1:
            return "gameplay"
        if mode_int == 0:
            return "menu"

    # Check if beatmap is loaded (implies menu or gameplay, not idle)
    beatmap = state.get("beatmap") or {}
    if beatmap.get("title"):
        # Beatmap is loaded — could be menu or gameplay
        # If current_time_ms is set and positive, likely in gameplay
        current_time = state.get("current_time_ms")
        if current_time is not None and current_time > 0:
            return "gameplay"
        return "menu"

    return "unknown"


def _is_default_menu_field_value(value: Any, field_name: str) -> bool:
    """Check if a field value looks like a default/uninformative menu placeholder."""
    if value is None:
        return True
    if field_name == "score" and (value == 0 or value == ""):
        return True
    if field_name == "accuracy":
        try:
            acc = float(value)
            if acc == 100.0 or acc == 100:
                return True
        except (TypeError, ValueError):
            pass
    if field_name == "max_combo" and (value == 0 or value == ""):
        return True
    return False


def _is_result_snapshot_ready(
    activity_state: str,
    result_fields: dict[str, Any],
    song_title: str,
    difficulty: str,
) -> tuple[bool, str]:
    """
    Determine if a bridge snapshot represents a real result screen.

    Returns (ready, reason):
      ready=True: snapshot has sufficient result-like evidence
      ready=False, reason: specific reason why not ready

    Readiness rules:
    1. gameplay state -> unavailable, reason=result_source_not_result_state
    2. missing song AND difficulty AND result fields too thin -> unavailable
    3. All fields are default placeholders -> unavailable
    4. Must have at least score OR accuracy (non-default)
    5. Must have at least one of: misses, rank, passed, combo/max_combo with meaning

    Does NOT fake or infer missing fields.
    """
    # Rule 1: gameplay is not a result
    if activity_state == "gameplay":
        return False, "result_source_not_result_state"

    # Rule 2: thin metadata + thin fields = unavailable
    metadata_thin = song_title in ("unknown", "") and difficulty in ("unknown", "")
    fields_thin = len(result_fields) <= 2
    if metadata_thin and fields_thin:
        return False, "result_source_thin_fields_and_unknown_metadata"

    # Check for default menu placeholder values
    score = result_fields.get("score")
    accuracy = result_fields.get("accuracy")
    max_combo = result_fields.get("max_combo")

    score_is_default = _is_default_menu_field_value(score, "score")
    accuracy_is_default = _is_default_menu_field_value(accuracy, "accuracy")
    max_combo_is_default = _is_default_menu_field_value(max_combo, "max_combo")

    # Rule 3: if ALL result fields look like defaults, treat as unavailable
    if result_fields and score_is_default and accuracy_is_default and max_combo_is_default:
        # Check if there are ANY non-default fields
        non_default_keys = {
            k: v for k, v in result_fields.items()
            if k not in ("score", "accuracy", "max_combo")
            and v is not None
            and v != ""
        }
        if not non_default_keys:
            return False, "result_source_default_menu_fields"

    # Rule 4: must have at least score OR accuracy (non-default)
    has_meaningful_score = score is not None and not score_is_default
    has_meaningful_accuracy = accuracy is not None and not accuracy_is_default

    if not has_meaningful_score and not has_meaningful_accuracy:
        return False, "result_source_no_meaningful_score_or_accuracy"

    # Rule 5: must have at least one supplementary result field
    # (misses, rank, passed, combo, max_combo with meaning)
    combo = result_fields.get("combo")
    misses = result_fields.get("misses")
    rank = result_fields.get("rank")
    passed = result_fields.get("passed")

    has_combo_meaning = combo is not None and combo > 0
    has_max_combo_meaning = max_combo is not None and max_combo > 0
    has_misses = misses is not None
    has_rank = rank is not None and rank != ""
    has_passed = passed is not None

    supplementary = has_combo_meaning or has_max_combo_meaning or has_misses or has_rank or has_passed
    if not supplementary:
        return False, "result_source_missing_supplementary_fields"

    return True, ""


def read_result_source_snapshot(bridge_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Read the current osu! result source snapshot.

    If bridge_state is None, reads from the existing tosu bridge.
    If provided, uses the given state dict directly.

    Returns a snapshot dict:
    {
        "available": bool,
        "unavailable_reason": str | None,       # only if available=False
        "source": str,                           # e.g. "tosu:/json/v2"
        "activity_state": str,                   # result/menu/gameplay/break/unknown
        "result_fields": dict | None,            # score/accuracy/misses/combo/max_combo/rank/passed
        "song_title": str,
        "difficulty": str,
        "bridge_ok": bool,
        "bridge_source": str,
    }

    Safety guarantees:
    - Read-only, no file write
    - No voice/VTS/OBS/real input/submit calls
    """
    from nana.game.osu.bridge import read_tosu_state

    if bridge_state is None:
        try:
            state = read_tosu_state()
        except Exception:
            state = {"ok": False, "source": "tosu", "raw": None, "beatmap": {}}
    else:
        state = bridge_state

    bridge_ok = bool(state.get("ok", False))
    bridge_source = str(state.get("source") or "tosu")

    # Extract result fields
    result_fields = _extract_result_fields_from_bridge(state)

    # Song metadata
    song_title, difficulty = _extract_song_metadata(state)

    # Activity state
    activity_state = _detect_activity_state(state)

    # Determine availability using readiness helper
    ready, unavailable_reason = _is_result_snapshot_ready(
        activity_state=activity_state,
        result_fields=result_fields,
        song_title=song_title,
        difficulty=difficulty,
    )
    available = ready

    return {
        "available": available,
        "unavailable_reason": unavailable_reason,
        "source": bridge_source,
        "activity_state": activity_state,
        "result_fields": result_fields if available else None,
        "song_title": song_title,
        "difficulty": difficulty,
        "bridge_ok": bridge_ok,
        "bridge_source": bridge_source,
    }


def build_result_source_status_output(snapshot: dict[str, Any]) -> str:
    """
    Build the formatted output for /osu-result-source-status command.

    Reads only. Does not write file.
    """
    lines = []
    lines.append("osu! Result Source Status")
    lines.append(f"  Decision: result_source_status_only_no_write")
    lines.append(f"  Source: {snapshot['source']}")

    available = snapshot["available"]
    lines.append(f"  Available: {str(available).lower()}")

    if available:
        fields = snapshot["result_fields"] or {}
        activity = snapshot["activity_state"]
        lines.append(f"  Activity state: {activity}")

        score = fields.get("score")
        accuracy = fields.get("accuracy")
        misses = fields.get("misses")
        combo = fields.get("combo")
        max_combo = fields.get("max_combo")
        rank = fields.get("rank")
        passed = fields.get("passed")
        song_title = snapshot.get("song_title", "unknown")
        difficulty = snapshot.get("difficulty", "unknown")

        if score is not None:
            lines.append(f"  score: {score}")
        if accuracy is not None:
            lines.append(f"  accuracy: {accuracy}")
        if misses is not None:
            lines.append(f"  misses: {misses}")
        if combo is not None:
            lines.append(f"  combo: {combo}")
        if max_combo is not None:
            lines.append(f"  max_combo: {max_combo}")
        if rank is not None:
            lines.append(f"  rank: {rank}")
        if passed is not None:
            lines.append(f"  passed: {passed}")

        lines.append(f"  song: {song_title}")
        lines.append(f"  difficulty: {difficulty}")
    else:
        lines.append(f"  Unavailable reason: {snapshot['unavailable_reason']}")

    lines.append(f"  File write: False")
    lines.append(f"  Pipeline write: False")
    lines.append(f"  Voice call: False")
    lines.append(f"  VTS call: False")
    lines.append(f"  OBS call: False")
    lines.append(f"  Real input: False")
    lines.append(f"  Submit: False")

    return "\n".join(lines)


def parse_manual_result_flags(text: str) -> dict[str, Any]:
    """
    Parse manual result flags from command text.

    Supports:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break/gameplay>
      --path=<path>

    Returns dict of parsed fields.
    """
    values: dict[str, Any] = {}
    _BOOL_TRUE = {"true", "1", "yes", "on"}

    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "score":
            values["score"] = int(raw_value)
        elif name == "accuracy":
            values["accuracy"] = float(raw_value)
        elif name == "misses":
            values["misses"] = int(raw_value)
        elif name == "combo":
            values["combo"] = int(raw_value)
        elif name == "max-combo":
            values["max_combo"] = int(raw_value)
        elif name == "rank":
            values["rank"] = raw_value.strip().upper()
        elif name == "passed":
            values["passed"] = raw_value.strip().lower() in _BOOL_TRUE
        elif name == "activity-state":
            values["activity_state"] = raw_value.strip()
        elif name == "path":
            values["path"] = raw_value.strip()
    return values


def merge_result_sources(
    bridge_snapshot: dict[str, Any] | None,
    manual_flags: dict[str, Any],
    staged_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Merge result data from bridge, staged result, and manual flags.

    Manual flags override bridge fields if both exist.
    Returns merged result fields dict.

    Also returns metadata dict:
    {
        "source": "bridge" | "staged_manual" | "manual" | "none",
        "source_available": bool,
        "activity_state": str,
        "song_title": str,
        "difficulty": str,
        "has_result_data": bool,
    }
    """
    result_fields: dict[str, Any] = {}
    source = "none"
    source_available = False

    # Start with bridge fields if available
    if bridge_snapshot is not None and bridge_snapshot.get("available"):
        bridge_fields = bridge_snapshot.get("result_fields") or {}
        result_fields.update(bridge_fields)
        source = "bridge"
        source_available = True
    elif staged_snapshot is not None and staged_snapshot.get("available"):
        staged_fields = staged_snapshot.get("result_fields") or {}
        result_fields.update(staged_fields)
        source = "staged_manual"
        source_available = True

    # Override with manual flags
    manual_result_keys = {"score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"}
    manual_result = {k: v for k, v in manual_flags.items() if k in manual_result_keys}
    if manual_result:
        result_fields.update(manual_result)
        source = "manual"
        source_available = True

    has_result_data = bool(result_fields)

    # Activity state: manual > bridge > default
    activity_state = "result"
    if manual_flags.get("activity_state"):
        activity_state = manual_flags["activity_state"]
    elif bridge_snapshot is not None:
        if bridge_snapshot.get("available"):
            activity_state = bridge_snapshot.get("activity_state", "result")
        elif staged_snapshot is not None and staged_snapshot.get("available"):
            activity_state = staged_snapshot.get("activity_state", "result")
        else:
            activity_state = bridge_snapshot.get("activity_state", "result")
    elif staged_snapshot is not None:
        activity_state = staged_snapshot.get("activity_state", "result")

    # Song metadata: selected source > unknown
    song_title = "unknown"
    difficulty = "unknown"
    if source == "bridge" and bridge_snapshot is not None:
        song_title = bridge_snapshot.get("song_title", "unknown")
        difficulty = bridge_snapshot.get("difficulty", "unknown")
    elif source == "staged_manual" and staged_snapshot is not None:
        song_title = staged_snapshot.get("song_title", "unknown")
        difficulty = staged_snapshot.get("difficulty", "unknown")

    return {
        "source": source,
        "source_available": source_available,
        "activity_state": activity_state,
        "song_title": song_title,
        "difficulty": difficulty,
        "has_result_data": has_result_data,
        "result_fields": result_fields,
    }
