"""Run existing pure public-memory tests without starting Nana or loading its data."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from smoke_memory_v2_phase1 import _isolated_nana_imports


CASES = {
    "smoke_stage9j_public_thread_memory.py": (
        "_test_repeat_prompt_builds_awareness", "_test_repeat_fatigue_after_many_repeats",
        "_test_topic_continuation_uses_recent_public_thread"),
    "smoke_stage9n_public_viewer_memory.py": (
        "_test_quiet_room_profile_is_per_viewer", "_test_story_and_emoji_profiles",
        "_test_boundary_and_model_profiles"),
    "smoke_stage9m_public_memory_filter.py": (
        "_test_repeated_prompt_excluded_from_learning", "_test_boundary_rehearsal_reduced_or_excluded"),
    "smoke_stage9z_stream_event_timeline.py": ("_test_record_snapshot_and_status_lines",),
}


if __name__ == "__main__":
    count = 0
    for filename, cases in CASES.items():
        for case in cases:
            with _isolated_nana_imports():
                spec = spec_from_file_location("isolated_legacy_case", Path(__file__).parent / filename)
                module = module_from_spec(spec)
                spec.loader.exec_module(module)
                getattr(module, case)()
                count += 1
    print(f"Legacy public memory: {count} passed")
