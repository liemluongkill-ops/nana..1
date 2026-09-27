"""Deterministic local chat pages for the operator-session rehearsal.

Uses the real public pipeline and existing fake playback fixtures. No provider,
credentials, private state or output device is constructed here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import time


def build_demo_runtime(control, observed):
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.stream_cum2_response import PublicResponseGenerator
    from nana.runtime.stream_cum4_host import YouTubeTextHost
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
    from nana.runtime.stream_session_control import SessionPlaybackPort
    from nana.runtime.stream_voice_host import PublicVoiceHost
    from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress
    from nana.runtime.youtube_chat_simulator import _LocalFakePlaybackPort, _LocalLivePolicySource

    port = _LocalFakePlaybackPort()
    session = SocialSessionCache(priority_viewers=())
    policy = _LocalLivePolicySource(can_speak=True)
    replies = []
    def caller(**kwargs):
        observed["model_requests"] += 1
        replies.append(kwargs["messages"])
        return "Mình nhận được câu hỏi rồi, mình đang trả lời trong phiên mô phỏng.", "local_fake"
    generator = PublicResponseGenerator(caller=caller, session_context=session)
    host = YouTubeTextHost(cum1=YouTubeChatCum1(session=YouTubeLiveSession("step7-demo-room", "step7-demo-session"),
                                              policy_source=policy, ingress=YouTubeChatIngress(actionable_limit=1)),
                           generator=generator, publisher=None, social_session=session)
    class CountingPort(SessionPlaybackPort):
        def play(self, request, **kwargs):
            observed["playback_dispatches"] += 1
            return super().play(request, **kwargs)
    controller = PublicVoicePlaybackController(playback_port_factory=lambda: CountingPort(port, control),
                                               policy_source=policy, active_session_id="step7-demo-session",
                                               delivery_recorder=generator.delivery_recorder)
    voice_host = PublicVoiceHost(host=host, playback=controller, control=control)

    class DemoTransport:
        def __init__(self):
            self.polls = 0
            self.first = None

        def resolve_live_chat_id(self, video_id):
            return "step7-demo-room"

        def message(self, index):
            text = ("Yumi oi, hom nay ban thay the nao?", "Yumi oi, ban thich mau nao?",
                    "Yumi oi, ban co the ke mot chuyen vui khong?")[index - 1]
            return {"id": f"step7-provider-event-{index}", "snippet": {
                "type": "textMessageEvent", "liveChatId": "step7-demo-room",
                "authorChannelId": f"step7-viewer-{index}",
                "publishedAt": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "displayMessage": text,
            }, "authorDetails": {"displayName": f"Viewer {index}", "channelId": f"step7-viewer-{index}"}}

        def list_messages(self, room, *, page_token=None):
            self.polls += 1
            page = {"items": [], "nextPageToken": f"demo-page-{self.polls}", "pollingIntervalMillis": 100}
            if self.polls == 2:
                self.first = self.message(1)
                page["items"] = [self.first, self.message(2)]
            elif self.polls == 3:
                page["items"] = [self.first, self.message(3)]
            elif self.polls >= 4:
                page["offlineAt"] = datetime.now(timezone.utc).isoformat()
            return page

    transport = DemoTransport()
    def evidence():
        return {"fake_model_calls": len(replies), "fake_playback_calls": port.play_calls,
                "fake_first_audio_receipts": port.first_audio_receipts,
                "fake_completion_receipts": port.completion_receipts,
                "youtube_network_called": False, "youtube_output_called": False,
                "local_audio_sink_writes": 0, "tts_provider_requests": 0}
    return transport, lambda _room: voice_host, evidence
