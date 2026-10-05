"""Keep blocking iterator reads off the shared Core asyncio loop."""
import asyncio


async def iterate_blocking(source):
    iterator = iter(source)
    end = object()
    try:
        while True:
            pending = asyncio.create_task(asyncio.to_thread(next, iterator, end))
            try:
                value = await asyncio.shield(pending)
            except asyncio.CancelledError:
                # Do not close a generator while another thread is inside it.
                # Its provider timeout still bounds the current read.
                await pending
                raise
            if value is end:
                break
            yield value
    finally:
        close = getattr(iterator, 'close', None)
        if callable(close):
            await asyncio.to_thread(close)
