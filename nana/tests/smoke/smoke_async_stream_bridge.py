"""A blocking provider iterator must not starve Core heartbeat/receipts."""
import asyncio
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

async def run():
    from nana.runtime.async_stream_bridge import iterate_blocking
    entered = threading.Event()
    release = threading.Event()
    closed = []
    def source():
        try:
            entered.set()
            if not release.wait(1):
                raise AssertionError('event loop did not release provider')
            yield 'fixture'
        finally:
            closed.append(True)
    async def heartbeat():
        await asyncio.to_thread(entered.wait, .5)
        release.set()
    beat = asyncio.create_task(heartbeat())
    assert [part async for part in iterate_blocking(source())] == ['fixture']
    await beat
    assert closed == [True]
    print('smoke_async_stream_bridge: PASS (1/1)')

if __name__ == '__main__':
    asyncio.run(run())
