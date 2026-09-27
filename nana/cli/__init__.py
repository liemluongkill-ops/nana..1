"""nana.cli - CLI entry point subsystem.

Keep package import light. Importing any ``nana.cli.*`` module should not
eagerly wire the full runtime app/pulse stack.
"""

__all__ = ["main", "handle_text"]


async def main(*args, **kwargs):
    from nana.cli.app import main as _main

    return await _main(*args, **kwargs)


async def handle_text(*args, **kwargs):
    from nana.cli.handle_text import handle_text as _handle_text

    return await _handle_text(*args, **kwargs)
