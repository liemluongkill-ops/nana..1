"""Serialized atomic JSON snapshots; no I/O or worker is started on import."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
import threading
import time


@dataclass(frozen=True)
class WriteReceipt:
    schema_version: int
    snapshot_revision: int
    committed: bool = False
    recovered: bool = False
    error: str = ""


class AtomicMemoryWriter:
    """Synchronous ordered writer. ``flush`` waits for any in-flight commit.

    Explicit revisions reject stale snapshots. Omit snapshot_revision to allocate
    the next revision under the same lock as commit. Locks are shared by writers
    for the same path within this process. Cross-process writers are unsupported.
    """
    _locks_guard = threading.Lock()
    _locks = {}

    def __init__(self, path, replace_func=os.replace):
        self.path = Path(path)
        self.previous_path = self.path.with_name(self.path.name + ".previous")
        self._replace = replace_func
        key = os.path.normcase(str(self.path.resolve()))
        with self._locks_guard:
            self._lock = self._locks.setdefault(key, threading.RLock())
        self.last_receipt = WriteReceipt(1, 0)

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict):
            raise ValueError("Memory snapshot must be an object")
        if not isinstance(data.get("profile"), dict) or not isinstance(data.get("long_term"), list):
            raise ValueError("Memory snapshot requires profile object and long_term list")
        for key, default, minimum in (("schema_version", 1, 1), ("snapshot_revision", 0, 0)):
            value = data.get(key, default)
            if type(value) is not int or value < minimum:
                raise ValueError(f"Invalid {key}")
        if data.get("schema_version", 1) not in (1, 2):
            raise ValueError("Unsupported memory schema_version")
        # Reject non-finite floats as well as objects JSON cannot round-trip.
        json.dumps(data, allow_nan=False)
        return data

    def _read(self, path):
        with path.open("r", encoding="utf-8") as handle:
            return self._validate(json.load(handle))

    def _atomic_replace(self, destination, data):
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load_locked(self):
        if not self.path.exists() and not self.previous_path.exists():
            return None, False
        try:
            return self._read(self.path), False
        except (OSError, ValueError, TypeError):
            try:
                previous = self._read(self.previous_path)
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError("No valid primary or previous memory snapshot; refusing defaults") from exc
            # Never copy a damaged primary over the valid recovery snapshot.
            self._atomic_replace(self.path, previous)
            return previous, True

    def load(self):
        with self._lock:
            try:
                data, recovered = self._load_locked()
                if data is None:
                    data = {"profile": {}, "long_term": []}
                data = copy.deepcopy(data)
                data.setdefault("schema_version", 1)
                data.setdefault("snapshot_revision", 0)
                self.last_receipt = WriteReceipt(data["schema_version"], data["snapshot_revision"], recovered=recovered)
                return data
            except (OSError, ValueError, TypeError) as exc:
                self.last_receipt = WriteReceipt(1, 0, error=str(exc))
                raise

    def submit(self, snapshot):
        with self._lock:
            schema, revision = 1, 0
            recovered = False
            try:
                data = copy.deepcopy(snapshot)
                self._validate(data)
                current, recovered = self._load_locked()
                current_revision = current.get("snapshot_revision", 0) if current else 0
                schema = data.get("schema_version", 1)
                revision = data.get("snapshot_revision", current_revision + 1)
                if revision <= current_revision:
                    raise ValueError("Stale snapshot_revision; refusing rollback")
                data["schema_version"] = schema
                data["snapshot_revision"] = revision
                data["snapshot_saved_at"] = time.time()
                if current is not None:
                    self._atomic_replace(self.previous_path, current)
                self._atomic_replace(self.path, data)
                self.last_receipt = WriteReceipt(schema, revision, committed=True, recovered=recovered)
            except (OSError, ValueError, TypeError) as exc:
                self.last_receipt = WriteReceipt(schema, revision, recovered=recovered, error=str(exc))
            return self.last_receipt

    def flush(self):
        with self._lock:
            return self.last_receipt
