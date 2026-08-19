"""Metadata indexes for deterministic retrieval."""
from __future__ import annotations
from collections import defaultdict
from threading import RLock
from uuid import UUID
from .models import MemoryRecord

class MemoryIndexer:
    def __init__(self) -> None:
        self._indexes: dict[str, dict[str, set[UUID]]] = defaultdict(lambda: defaultdict(set))
        self._lock = RLock()
    def add(self, record: MemoryRecord) -> None:
        values = {
            "mission_id": str(record.mission_id), "agent": record.source_agent.value if record.source_agent else "",
            "domain": record.domain.value, "content_type": record.content_type.value,
            "platform": record.platform or "", "language": record.language or "", "topic": (record.topic or "").lower(),
        }
        with self._lock:
            for key, value in values.items():
                if value: self._indexes[key][value].add(record.record_id)
            for keyword in record.keywords: self._indexes["keyword"][keyword.lower()].add(record.record_id)
            for tag in record.tags: self._indexes["tag"][tag.lower()].add(record.record_id)
    def remove(self, record: MemoryRecord) -> None:
        with self._lock:
            for bucket in self._indexes.values():
                for ids in bucket.values(): ids.discard(record.record_id)
    def lookup(self, field: str, value: str) -> set[UUID]:
        with self._lock: return set(self._indexes.get(field, {}).get(value.lower(), set()))
    def stats(self) -> dict[str, int]:
        with self._lock: return {field: sum(len(ids) for ids in bucket.values()) for field, bucket in self._indexes.items()}
