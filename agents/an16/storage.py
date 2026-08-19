"""Provider-independent thread-safe memory storage."""
from __future__ import annotations
from abc import ABC, abstractmethod
from threading import RLock
from uuid import UUID
from .models import MemoryRecord, MemoryRelationship, MemoryQuery, MemoryStatus

class MemoryStore(ABC):
    @abstractmethod
    def upsert(self, record: MemoryRecord) -> bool: ...
    @abstractmethod
    def get(self, record_id: UUID) -> MemoryRecord | None: ...
    @abstractmethod
    def delete(self, record_id: UUID) -> bool: ...
    @abstractmethod
    def all_records(self) -> list[MemoryRecord]: ...
    @abstractmethod
    def add_relationship(self, relationship: MemoryRelationship) -> None: ...
    @abstractmethod
    def relationships(self) -> list[MemoryRelationship]: ...

class InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._records: dict[UUID, MemoryRecord] = {}
        self._relationships: dict[UUID, MemoryRelationship] = {}
        self._lock = RLock()
    def upsert(self, record: MemoryRecord) -> bool:
        with self._lock:
            existed = record.record_id in self._records
            self._records[record.record_id] = record
            return not existed
    def get(self, record_id: UUID) -> MemoryRecord | None:
        with self._lock: return self._records.get(record_id)
    def delete(self, record_id: UUID) -> bool:
        with self._lock:
            return self._records.pop(record_id, None) is not None
    def all_records(self) -> list[MemoryRecord]:
        with self._lock: return list(self._records.values())
    def add_relationship(self, relationship: MemoryRelationship) -> None:
        with self._lock: self._relationships[relationship.relationship_id] = relationship
    def relationships(self) -> list[MemoryRelationship]:
        with self._lock: return list(self._relationships.values())
