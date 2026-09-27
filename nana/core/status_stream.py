"""Stream readiness status panel for Nana."""

from __future__ import annotations


def print_stream_ready_status(voice=None) -> None:
    from nana.runtime.stream_ready_status import stream_ready_status_lines

    for line in stream_ready_status_lines(voice):
        print(line)


def print_stream_event_log() -> None:
    from nana.runtime.stream_event_timeline import stream_event_timeline_status_lines

    for line in stream_event_timeline_status_lines():
        print(line)
