"""Focused offline smoke for the local Stream V1 YouTube-chat simulator."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Avoid Nana's eager facade: this smoke owns an isolated, provider-free runtime.
NANA_ROOT = ROOT / "nana"
if "nana" not in sys.modules:
    nana_package = types.ModuleType("nana")
    nana_package.__path__ = [str(NANA_ROOT)]
    sys.modules["nana"] = nana_package
if "nana.runtime" not in sys.modules:
    runtime_package = types.ModuleType("nana.runtime")
    runtime_package.__path__ = [str(NANA_ROOT / "runtime")]
    sys.modules["nana.runtime"] = runtime_package


NOW = 1_700_000_400.0
PIPELINE_FLAGS = {
    "NANA_STREAM_CUM0_ENABLED": "0",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "0",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED": "0",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0",
    "NANA_STREAM_CUM4_HOST_ENABLED": "0",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "0",
}


class _FakeCaller:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return f"Mình nhận được tin mô phỏng số {len(self.calls)}.", "ok"


def test_disabled_simulator_does_not_touch_pipeline_or_model() -> None:
    from nana.runtime.youtube_chat_simulator import LocalYouTubeChatSimulator

    caller = _FakeCaller()
    simulator = LocalYouTubeChatSimulator(stage="generate", model_caller=caller)
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "0"},
        clear=False,
    ):
        before = {name: os.environ.get(name) for name in PIPELINE_FLAGS}
        result = simulator.submit("Yumi oi", now=NOW)
        after = {name: os.environ.get(name) for name in PIPELINE_FLAGS}

    assert result.status == "disabled", result
    assert result.reason_code == "local_simulator_disabled", result
    assert result.evidence_level == "local_simulation"
    assert result.public_turn is None
    assert result.response_artifact is None
    assert caller.calls == []
    assert after == before


def test_ingress_stage_uses_real_cum1_and_restores_flags() -> None:
    from nana.runtime.youtube_chat_simulator import LocalYouTubeChatSimulator

    simulator = LocalYouTubeChatSimulator(stage="ingress")
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "1"},
        clear=False,
    ):
        result = simulator.submit("Yumi oi, nghe thay khong?", now=NOW)
        restored = {name: os.environ.get(name) for name in PIPELINE_FLAGS}

    assert result.status == "accepted", result
    assert result.reason_code == "admitted", result
    assert result.evidence_level == "local_simulation"
    assert result.input_source == "local_fixture"
    assert result.event_id == "local-sim-event-000001"
    assert result.public_turn is not None
    assert result.public_turn.scope.platform == "youtube"
    assert result.public_turn.scope.room_id == "local-sim-room"
    assert result.public_turn.scope.stream_session_id == "local-sim-session"
    assert result.public_turn.scope.identity.author_id == "local-sim-viewer"
    assert result.response_artifact is None
    assert restored == PIPELINE_FLAGS
    assert result.to_dict()["youtube_network_called"] is False
    assert result.to_dict()["youtube_output_called"] is False


def test_generate_stage_preserves_session_context_across_messages() -> None:
    from nana.runtime.youtube_chat_simulator import LocalYouTubeChatSimulator

    caller = _FakeCaller()
    simulator = LocalYouTubeChatSimulator(stage="generate", model_caller=caller)
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "1"},
        clear=False,
    ):
        first = simulator.submit("Tin dau tien cua phong", now=NOW)
        second = simulator.submit("Tin thu hai noi tiep", now=NOW + 1)
        restored = {name: os.environ.get(name) for name in PIPELINE_FLAGS}

    assert first.status == "generated", first
    assert second.status == "generated", second
    assert first.event_id == "local-sim-event-000001"
    assert second.event_id == "local-sim-event-000002"
    assert first.response_artifact is not None
    assert second.response_artifact is not None
    assert first.response_artifact.text == "Mình nhận được tin mô phỏng số 1."
    assert second.response_artifact.text == "Mình nhận được tin mô phỏng số 2."
    assert len(caller.calls) == 2
    second_system_prompt = caller.calls[1]["messages"][0]["content"]
    assert "Tin dau tien cua phong" in second_system_prompt
    assert "Tin thu hai noi tiep" in second_system_prompt
    assert second.model_mode == "fake"
    assert restored == PIPELINE_FLAGS


def test_cli_emits_sanitized_local_simulation_json() -> None:
    from nana.runtime.youtube_chat_simulator import main

    output = io.StringIO()
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "1"},
        clear=False,
    ), redirect_stdout(output):
        exit_code = main([
            "--stage",
            "generate",
            "--text",
            "Yumi ke chuyen vui di",
            "--fake-reply",
            "Mình kể một chuyện nhỏ nha.",
        ])

    records = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
    assert exit_code == 0
    assert len(records) == 1
    assert records[0]["status"] == "generated"
    assert records[0]["evidence_level"] == "local_simulation"
    assert records[0]["response_generated"] is True
    assert records[0]["reply"] == "Mình kể một chuyện nhỏ nha."
    assert records[0]["youtube_network_called"] is False
    assert records[0]["youtube_output_called"] is False
    assert records[0]["model_provider_called"] is False
    assert "actor_key" not in records[0]


def test_host_stage_runs_social_cum2_and_fake_publish_without_youtube() -> None:
    from nana.runtime.youtube_chat_simulator import LocalYouTubeChatSimulator

    caller = _FakeCaller()
    simulator = LocalYouTubeChatSimulator(stage="host", model_caller=caller)
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "1"},
        clear=False,
    ):
        result = simulator.submit("Yumi oi, hom nay vui khong?", now=NOW)
        restored = {name: os.environ.get(name) for name in PIPELINE_FLAGS}

    data = result.to_dict()
    assert result.status == "published", result
    assert data["host_action"] == "full_reply"
    assert data["reply"] == "Mình nhận được tin mô phỏng số 1."
    assert data["local_publish_simulated"] is True
    assert data["youtube_network_called"] is False
    assert data["youtube_output_called"] is False
    assert len(caller.calls) == 1
    assert restored == PIPELINE_FLAGS


def test_voice_stage_runs_fake_full_path_and_restores_every_flag() -> None:
    from nana.runtime.youtube_chat_simulator import LocalYouTubeChatSimulator

    caller = _FakeCaller()
    simulator = LocalYouTubeChatSimulator(stage="voice", model_caller=caller)
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "1"},
        clear=False,
    ), patch(
        "requests.sessions.Session.request",
        side_effect=AssertionError("fixture voice stage attempted network I/O"),
    ):
        result = simulator.submit("Yumi oi, tra loi bang giong noi nhe", now=NOW)
        restored = {name: os.environ.get(name) for name in PIPELINE_FLAGS}

    data = result.to_dict()
    assert result.status == "delivered", result
    assert result.playback_result is not None
    assert result.playback_result.status == "delivered"
    assert [record.state for record in simulator.voice_delivery_records] == [
        "generated",
        "playback_started",
        "delivered",
    ]
    assert data["voice_receipt_source"] == "fake_playback_fixture"
    assert data["fake_playback_calls"] == 1
    assert data["fake_first_audio_receipts"] == 1
    assert data["fake_completion_receipts"] == 1
    assert data["tts_provider_requests"] == 0
    assert data["local_audio_sink_writes"] == 0
    assert data["provider_calls_attempted"] is False
    assert data["provider_network_observed"] is False
    assert data["effects_measurement"]["provider_network"] == "not_attempted"
    assert data["youtube_network_called"] is False
    assert data["youtube_output_called"] is False
    assert data["cum3_imported"] is True
    assert data["local_publisher_constructed"] is False
    assert data["local_publication_calls"] == 0
    assert data["local_publish_simulated"] is False
    assert len(caller.calls) == 1
    assert restored == PIPELINE_FLAGS


def test_provider_attempts_do_not_claim_observed_network_evidence() -> None:
    from nana.runtime.youtube_chat_simulator import LocalSimulationResult

    result = LocalSimulationResult(
        "failed",
        "provider_error",
        "voice",
        model_mode="real_llmgate",
        voice_mode="real_voice",
        model_requests=1,
        tts_provider_requests=1,
    ).to_dict()

    assert result["provider_calls_attempted"] is True
    assert result["model_provider_called"] is True
    assert result["tts_called"] is True
    assert result["provider_network_observed"] is None
    assert result["effects_measurement"]["provider_network"] == "unmeasured"
    assert result["effects_measurement"]["model"] == "gateway_client_invocations"
    assert result["effects_measurement"]["tts_provider"] == "client_invocations"


def test_voice_stage_restores_flags_after_failed_playback() -> None:
    from nana.runtime.stream_cum5_voice_playback import (
        AudioCompletionResult,
        PlaybackReadiness,
        PlaybackStopResult,
    )
    from nana.runtime.youtube_chat_simulator import LocalYouTubeChatSimulator

    class FailingPlayback:
        calls = 0

        def ready(self):
            return PlaybackReadiness(True, "ready")

        def play(self, request, *, should_continue, on_first_audio):
            self.calls += 1
            assert should_continue() is True
            return AudioCompletionResult(
                "failed",
                False,
                1,
                0,
                0,
                1,
                len(request.artifact.text),
                len(request.artifact.text),
                0.0,
                "private token=do-not-print",
                request.playback_id,
                request.content_sha256,
                request.requested_at + 0.001,
                True,
            )

        def cancel(self, _playback_id):
            return PlaybackStopResult("confirmed_stopped", "fixture_stopped")

    port = FailingPlayback()
    simulator = LocalYouTubeChatSimulator(
        stage="voice",
        model_caller=_FakeCaller(),
        playback_port_factory=lambda: port,
    )
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "1"},
        clear=False,
    ):
        result = simulator.submit("Yumi oi, thu loi phat", now=NOW)
        restored = {name: os.environ.get(name) for name in PIPELINE_FLAGS}

    assert result.status == "interrupted", result
    assert result.reason_code == "voice_playback_interrupted"
    assert result.playback_result is not None
    assert result.playback_result.status == "interrupted"
    data = result.to_dict()
    assert data["voice_receipt_source"] == "injected_playback"
    assert data["playback_reason_code"] == "voice_playback_interrupted"
    assert "private" not in json.dumps(data)
    assert port.calls == 1
    assert restored == PIPELINE_FLAGS


def test_real_voice_is_rejected_outside_voice_stage() -> None:
    from nana.runtime.youtube_chat_simulator import LocalYouTubeChatSimulator

    try:
        LocalYouTubeChatSimulator(stage="host", real_voice=True)
    except ValueError as exc:
        assert str(exc) == "real voice requires voice stage"
    else:
        raise AssertionError("real voice was accepted outside voice stage")


def test_voice_cli_defaults_to_labelled_fake_receipts() -> None:
    from nana.runtime.youtube_chat_simulator import main

    output = io.StringIO()
    with patch.dict(
        os.environ,
        {**PIPELINE_FLAGS, "NANA_STREAM_LOCAL_SIMULATOR_ENABLED": "1"},
        clear=False,
    ), patch(
        "requests.sessions.Session.request",
        side_effect=AssertionError("fake voice CLI attempted network I/O"),
    ), redirect_stdout(output):
        exit_code = main([
            "--stage",
            "voice",
            "--text",
            "Yumi oi, noi mot cau nhe",
            "--fake-reply",
            "Minh dang nghe day nha.",
        ])

    records = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
    assert exit_code == 0
    assert len(records) == 1
    assert records[0]["status"] == "delivered"
    assert records[0]["reply"] == "Minh dang nghe day nha."
    assert records[0]["voice_mode"] == "fake"
    assert records[0]["voice_receipt_source"] == "fake_playback_fixture"
    assert records[0]["model_provider_called"] is False
    assert records[0]["tts_provider_requests"] == 0
    assert records[0]["local_audio_sink_writes"] == 0


def test_direct_voice_cli_never_reads_env_or_production_data() -> None:
    simulator = NANA_ROOT / "runtime" / "youtube_chat_simulator.py"
    environment = os.environ.copy()
    environment["NANA_STREAM_LOCAL_SIMULATOR_ENABLED"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory() as temp_dir:
        guard = Path(temp_dir) / "sitecustomize.py"
        guard.write_text(
            (
                "import os\n"
                "import sys\n"
                f"_data = os.path.normcase(os.path.abspath({str(NANA_ROOT / 'data')!r}))\n"
                "def _audit(event, args):\n"
                "    if event == 'open' and args:\n"
                "        path = os.path.normcase(os.path.abspath(os.fspath(args[0])))\n"
                "        if os.path.basename(path).lower() == '.env' or path == _data or path.startswith(_data + os.sep):\n"
                "            raise RuntimeError('forbidden production file access')\n"
                "    if event == 'socket.connect':\n"
                "        raise RuntimeError('forbidden network call')\n"
                "sys.addaudithook(_audit)\n"
            ),
            encoding="ascii",
        )
        environment["PYTHONPATH"] = os.pathsep.join((temp_dir, str(ROOT)))
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(simulator),
                "--stage",
                "voice",
                "--text",
                "Yumi oi, noi mot cau nhe",
                "--fake-reply",
                "Minh dang nghe day nha.",
            ],
            cwd=str(ROOT),
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    assert completed.returncode == 0, completed
    records = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert len(records) == 1, completed.stdout
    assert records[0]["status"] == "delivered"
    assert records[0]["voice_receipt_source"] == "fake_playback_fixture"
    assert records[0]["youtube_network_called"] is False
    assert records[0]["tts_provider_requests"] == 0
    assert completed.stderr == ""


def main() -> None:
    tests = (
        test_disabled_simulator_does_not_touch_pipeline_or_model,
        test_ingress_stage_uses_real_cum1_and_restores_flags,
        test_generate_stage_preserves_session_context_across_messages,
        test_cli_emits_sanitized_local_simulation_json,
        test_host_stage_runs_social_cum2_and_fake_publish_without_youtube,
        test_voice_stage_runs_fake_full_path_and_restores_every_flag,
        test_provider_attempts_do_not_claim_observed_network_evidence,
        test_voice_stage_restores_flags_after_failed_playback,
        test_real_voice_is_rejected_outside_voice_stage,
        test_voice_cli_defaults_to_labelled_fake_receipts,
        test_direct_voice_cli_never_reads_env_or_production_data,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_youtube_chat_simulator: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
