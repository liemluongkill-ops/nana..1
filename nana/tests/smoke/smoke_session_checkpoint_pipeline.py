"""Run the isolated private pipeline checkpoint branch matrix."""
from pathlib import Path
import importlib.util
import sys


phase_path = Path(__file__).with_name("smoke_memory_v2_phase1.py")
spec = importlib.util.spec_from_file_location("checkpoint_phase1_fixture", phase_path)
phase = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = phase
assert spec.loader is not None
spec.loader.exec_module(phase)


if __name__ == "__main__":
    try:
        phase.test_failed_explicit_save_stops_before_model()
        print("PASS test_private_pipeline_checkpoint_branch_matrix")
    except Exception as exc:
        print(f"FAIL test_private_pipeline_checkpoint_branch_matrix: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
