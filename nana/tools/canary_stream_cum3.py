"""Owner-invoked one-message live canary; stops after one publish attempt."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--video-id', required=True)
    parser.add_argument('--marker', required=True)
    parser.add_argument('--inject-prompt')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    def manifest():
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (root / 'data').glob('*') if p.is_file()}
    before = manifest()
    # Nana package import reads its existing memory snapshot. The before/after
    # manifest below proves this canary did not mutate production data.
    import nana.config
    from nana.runtime.youtube_oauth import get_youtube_oauth_token
    from nana.runtime.youtube_chat_transport import YouTubeCredentials, YouTubeRestChatTransport
    from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
    from nana.runtime.stream_state import StreamStateCore
    from nana.runtime.stream_cum2_response import PublicResponseGenerator, YouTubeCum2Pipeline
    from nana.runtime.stream_cum3_youtube_publish import YouTubeCum3Publisher, YouTubeLiveChatSender, YouTubeCum3Pipeline
    from nana.brain import llmgate_client
    import requests
    counts = {'read': [], 'model': [], 'input': [], 'publish': []}
    class ReadSession(requests.Session):
        def get(self, *a, **kw):
            response = super().get(*a, **kw)
            counts['read'].append(response.status_code)
            return response
    class WriteSession(requests.Session):
        def post(self, *a, **kw):
            if counts['publish']:
                raise RuntimeError('one_publish_only')
            counts['publish'].append('attempted')
            response = super().post(*a, **kw)
            counts['publish'][-1] = response.status_code
            return response
    class InputSession(requests.Session):
        def post(self, *a, **kw):
            if counts['input']:
                raise RuntimeError('one_input_only')
            counts['input'].append('attempted')
            response = super().post(*a, **kw)
            counts['input'][-1] = response.status_code
            return response
    original_post = llmgate_client._http_post
    def model_post(*a, **kw):
        response = original_post(*a, **kw)
        counts['model'].append(response.status_code)
        return response
    llmgate_client._http_post = model_post
    oauth_token = get_youtube_oauth_token()
    assert oauth_token, 'oauth_unavailable'
    read = YouTubeRestChatTransport(
        YouTubeCredentials(oauth_token=oauth_token),
        session=ReadSession(),
    )
    chat = read.resolve_live_chat_id(args.video_id)
    policy = StreamStateCore()
    policy.go_live()  # Isolated owner-controlled canary policy, not production singleton.
    cum1 = YouTubeChatCum1(session=YouTubeLiveSession(chat, 'canary-' + args.video_id), policy_source=policy)
    pipeline = YouTubeCum3Pipeline(response_pipeline=YouTubeCum2Pipeline(
        ingress=cum1, generator=PublicResponseGenerator()),
        publisher=YouTubeCum3Publisher(sender=YouTubeLiveChatSender(session=WriteSession())))
    flags = ('NANA_STREAM_CUM0_ENABLED', 'NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED',
             'NANA_STREAM_CUM2_RESPONSE_ENABLED', 'NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED')
    saved = {k: os.environ.get(k) for k in flags}
    result_data = {'status': 'timeout'}
    try:
        for k in flags:
            os.environ[k] = '1'
        page = read.list_messages(chat)
        if page.get('offlineAt'):
            raise RuntimeError('chat_offline')
        pipeline.ingest_generate_publish(page, output_id='bootstrap', delivery_attempt_id='bootstrap', bootstrap=True, now=time.time())
        print(json.dumps({'state': 'READY', 'marker': args.marker}), flush=True)
        if args.inject_prompt:
            prompt = args.marker + ' ' + args.inject_prompt.strip()
            injected = YouTubeLiveChatSender(
                token_provider=lambda: oauth_token,
                session=InputSession(),
            ).send_text(chat, prompt)
            if injected.outcome != 'published':
                raise RuntimeError('input_injection_failed')
            print(json.dumps({'state': 'INPUT_POSTED', 'http_status': injected.http_status}), flush=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            time.sleep(max(1, float(page.get('pollingIntervalMillis', 2500)) / 1000 + .3))
            page = read.list_messages(chat, page_token=page.get('nextPageToken'))
            if page.get('offlineAt'):
                result_data = {'status': 'offline'}
                break
            items = [i for i in page.get('items', []) if args.marker in i.get('snippet', {}).get('displayMessage', '')]
            if not items:
                continue
            now = time.time()
            result = pipeline.ingest_generate_publish({**page, 'items': items[:1]},
                output_id=args.marker + '-output', delivery_attempt_id=args.marker + '-send', received_at=now, now=now)
            artifact = result.upstream.generation.artifact if result.upstream and result.upstream.generation else None
            sent = result.publish
            result_data = {'status': result.status, 'reason': result.reason_code,
                'reply': artifact.text if artifact else None,
                'correlation_id': artifact.correlation_id if artifact else None,
                'provider_message_id_sha256': hashlib.sha256(sent.provider_message_id.encode()).hexdigest() if sent and sent.provider_message_id else None,
                'delivery_state': sent.delivery_record.state if sent and sent.delivery_record else None,
                'model': llmgate_client.llmgate_transport_snapshot()}
            break
    except Exception as exc:
        result_data = {'status': 'error', 'error_type': type(exc).__name__}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    result_data.update(http_statuses=counts, production_data_unchanged=before == manifest(),
                       flags_restored=all(os.environ.get(k) == v for k, v in saved.items()),
                       policy='isolated_StreamStateCore', tts=False, avatar=False, obs=False)
    print(json.dumps(result_data, ensure_ascii=False), flush=True)
    return 0 if result_data['status'] == 'published' and result_data['production_data_unchanged'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
