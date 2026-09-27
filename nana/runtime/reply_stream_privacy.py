"""Keep possible credentials behind the output boundary until fully classified."""
import re

from .history_privacy import redact_history_text


_POSSIBLE_SECRET = re.compile(
    r'(?i)\b(?:password|passwd|pwd|passphrase|token|secret|authorization|credential|'
    r'openai_api_key|apikey|api(?:[_ -]?key)?|private(?:[_ -]?key)?|access(?:[_ -]?token)?|auth(?:[_ -]?token)?|'
    r'otp|mật|mat|mã|ma|bearer)\b'
    r'|\b(?:sk[-_]|gh[pousr][_-]|github_pat_|glpat-|xox[baprs]-|AKIA|eyJ)'
    r'|-----BEGIN'
)


class ReplyStreamPrivacy:
    """Emit benign whole words immediately; hold a suspicious suffix until EOF.

    Credential values, labels and PEMs can span arbitrary provider chunks. Once
    a possible label appears, the suffix must remain uncommitted until the shared
    policy can inspect it as a whole. Normal sentences keep early voice dispatch.
    """
    def __init__(self):
        self._pending = ''
        self._holding_secret = False
        self._emitted_context = ''

    def feed(self, text: str) -> str:
        self._pending += str(text or '')
        if self._holding_secret:
            return ''
        candidate = _POSSIBLE_SECRET.search(self._pending)
        if candidate:
            safe = self._pending[:candidate.start()]
            self._emitted_context = (self._emitted_context + safe)[-80:]
            self._pending = self._pending[candidate.start():]
            self._holding_secret = True
            return safe
        # Hold the unfinished word so split labels/key prefixes are never emitted.
        trailing = re.search(r'\S*$', self._pending)
        boundary = trailing.start() if trailing else len(self._pending)
        safe, self._pending = self._pending[:boundary], self._pending[boundary:]
        self._emitted_context = (self._emitted_context + safe)[-80:]
        return safe

    def flush(self) -> str:
        # The shared parser uses preceding ownership/budget words to distinguish
        # actual supplied credentials from definitions. Keep that context without
        # emitting it twice or delaying already-safe speech.
        classified = redact_history_text(self._emitted_context + self._pending)
        safe = (classified[len(self._emitted_context):]
                if classified.startswith(self._emitted_context) else '[redacted secret]')
        self._pending = ''
        self._holding_secret = False
        self._emitted_context = ''
        return safe
