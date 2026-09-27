"""Bounded binary media framing for Nana Presence sessions."""

from __future__ import annotations

from dataclasses import dataclass
import struct
import zlib


MEDIA_MAGIC = b"NPA1"
MEDIA_VERSION = 1
MEDIA_KIND_PCM_DOWNLINK = 1
MEDIA_KIND_PCM_UPLINK = 2
MEDIA_KIND_JPEG_UPLINK = 3
MEDIA_KINDS = frozenset(
    {
        MEDIA_KIND_PCM_DOWNLINK,
        MEDIA_KIND_PCM_UPLINK,
        MEDIA_KIND_JPEG_UPLINK,
    }
)
MEDIA_CHUNK_BYTES = 1024
MEDIA_HEADER = struct.Struct("!4sBBHIIII")
MEDIA_HEADER_BYTES = MEDIA_HEADER.size
MEDIA_FRAME_BYTES = MEDIA_HEADER_BYTES + MEDIA_CHUNK_BYTES


class PresenceMediaProtocolError(ValueError):
    """Raised when a binary media frame violates the bounded contract."""


@dataclass(frozen=True)
class PresenceMediaFrame:
    kind: int
    stream_id: int
    sequence: int
    payload: bytes
    flags: int = 0


def _uint32(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PresenceMediaProtocolError(f"{name} must be an integer")
    if value < 0 or value > 0xFFFFFFFF:
        raise PresenceMediaProtocolError(f"{name} is outside uint32 range")
    return value


def encode_media_frame(frame: PresenceMediaFrame) -> bytes:
    payload = bytes(frame.payload)
    if not payload or len(payload) > MEDIA_CHUNK_BYTES:
        raise PresenceMediaProtocolError("media payload length is invalid")
    kind = int(frame.kind)
    if kind not in MEDIA_KINDS:
        raise PresenceMediaProtocolError("media kind is not enabled")
    flags = int(frame.flags)
    if flags < 0 or flags > 0xFFFF:
        raise PresenceMediaProtocolError("media flags are outside uint16 range")
    stream_id = _uint32(frame.stream_id, "stream_id")
    sequence = _uint32(frame.sequence, "sequence")
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    header = MEDIA_HEADER.pack(
        MEDIA_MAGIC,
        MEDIA_VERSION,
        kind,
        flags,
        stream_id,
        sequence,
        len(payload),
        checksum,
    )
    return header + payload


def decode_media_frame(message: bytes | bytearray | memoryview) -> PresenceMediaFrame:
    raw = bytes(message)
    if len(raw) < MEDIA_HEADER_BYTES or len(raw) > MEDIA_FRAME_BYTES:
        raise PresenceMediaProtocolError("media frame length is invalid")
    magic, version, kind, flags, stream_id, sequence, length, checksum = (
        MEDIA_HEADER.unpack_from(raw)
    )
    if magic != MEDIA_MAGIC or version != MEDIA_VERSION:
        raise PresenceMediaProtocolError("media magic or version mismatch")
    if kind not in MEDIA_KINDS:
        raise PresenceMediaProtocolError("media kind is not enabled")
    if length == 0 or length > MEDIA_CHUNK_BYTES:
        raise PresenceMediaProtocolError("media payload length is invalid")
    payload = raw[MEDIA_HEADER_BYTES:]
    if len(payload) != length:
        raise PresenceMediaProtocolError("media payload length mismatch")
    if (zlib.crc32(payload) & 0xFFFFFFFF) != checksum:
        raise PresenceMediaProtocolError("media payload CRC32 mismatch")
    return PresenceMediaFrame(
        kind=kind,
        stream_id=stream_id,
        sequence=sequence,
        payload=payload,
        flags=flags,
    )


__all__ = [
    "MEDIA_CHUNK_BYTES",
    "MEDIA_FRAME_BYTES",
    "MEDIA_HEADER_BYTES",
    "MEDIA_KIND_JPEG_UPLINK",
    "MEDIA_KIND_PCM_DOWNLINK",
    "MEDIA_KIND_PCM_UPLINK",
    "PresenceMediaFrame",
    "PresenceMediaProtocolError",
    "decode_media_frame",
    "encode_media_frame",
]
