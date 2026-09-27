"""Read-only Memory v2 Phase 2 status surface smoke."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_all() -> int:
    from nana.runtime.memory_phase2_status import (
        phase2_flag_snapshot,
        phase2_status_lines,
        phase2_status_snapshot,
    )

    flags = phase2_flag_snapshot()
    assert all(value is False for value in flags.values()), flags
    snapshot = phase2_status_snapshot()
    assert snapshot["read_only"] is True
    assert snapshot["can_act"] is False
    assert snapshot["memory_write"] is False
    lines = "\n".join(phase2_status_lines())
    assert "MEMORY-V2-PHASE2" in lines
    assert "memory_write=False" in lines
    print("Phase2 status: 1/1 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_all())
