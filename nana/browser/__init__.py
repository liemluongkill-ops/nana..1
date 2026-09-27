"""Browser awareness for Nana Phase 3."""

__all__ = ["vision_previewer"]


def __getattr__(name):
    if name == "vision_previewer":
        from nana.runtime.browser_refresh import vision_previewer
        return vision_previewer
    raise AttributeError(name)
