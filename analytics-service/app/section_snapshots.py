"""Bounded in-memory snapshots used while a reader views a saved section."""
from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any


def _setting(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer setting without making a bad env value fatal."""
    try:
        return max(minimum, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


@dataclass
class SectionSnapshot:
    """Parsed source tables for one workspace, viewer and configuration version."""

    snapshot_id: str
    workspace_id: str
    viewer_id: str
    fingerprint: str
    tables: dict[str, tuple[Any, dict[str, Any]]]
    last_used_at: float


class SectionSnapshotStore:
    """Thread-safe LRU store; snapshots never contain OAuth tokens."""

    def __init__(self, max_entries: int | None = None, ttl_seconds: int | None = None):
        self.max_entries = max_entries or _setting("SECTION_SNAPSHOT_MAX_ENTRIES", 8)
        self.ttl_seconds = ttl_seconds or _setting("SECTION_SNAPSHOT_TTL_SECONDS", 1800, 60)
        self._entries: OrderedDict[str, SectionSnapshot] = OrderedDict()
        self._lock = RLock()

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, item in self._entries.items() if now - item.last_used_at >= self.ttl_seconds]
        for key in expired:
            self._entries.pop(key, None)

    def get(self, snapshot_id: str, workspace_id: str, viewer_id: str, fingerprint: str) -> SectionSnapshot | None:
        """Return only a snapshot that belongs to the current authorized view."""
        now = monotonic()
        with self._lock:
            self._purge_expired(now)
            item = self._entries.get(snapshot_id)
            if not item or (item.workspace_id, item.viewer_id, item.fingerprint) != (workspace_id, viewer_id, fingerprint):
                return None
            item.last_used_at = now
            self._entries.move_to_end(snapshot_id)
            return item

    def put(self, snapshot: SectionSnapshot) -> None:
        with self._lock:
            self._purge_expired(monotonic())
            self._entries[snapshot.snapshot_id] = snapshot
            self._entries.move_to_end(snapshot.snapshot_id)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def discard(self, snapshot_id: str | None) -> None:
        if snapshot_id:
            with self._lock:
                self._entries.pop(snapshot_id, None)

    def discard_workspace(self, workspace_id: str) -> None:
        """Release stale tables immediately after an author saves a workspace."""
        with self._lock:
            stale = [key for key, item in self._entries.items() if item.workspace_id == workspace_id]
            for key in stale:
                self._entries.pop(key, None)


snapshots = SectionSnapshotStore()
