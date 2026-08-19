"""Lightweight knowledge linking without coupling to a graph database."""
from __future__ import annotations
from .models import MemoryRecord, MemoryRelationship
from .storage import MemoryStore

class KnowledgeLinker:
    def __init__(self, store: MemoryStore) -> None: self.store = store
    def link(self, relationship: MemoryRelationship) -> None:
        if not self.store.get(relationship.from_record_id) or not self.store.get(relationship.to_record_id):
            raise ValueError("Both relationship endpoints must exist in memory.")
        self.store.add_relationship(relationship)
    def infer(self, records: list[MemoryRecord]) -> list[MemoryRelationship]:
        by_type = {r.content_type.value: r for r in records}
        chain = [("research","script"),("script","video"),("video","thumbnail"),("thumbnail","analytics"),("analytics","evolution"),("evolution","mission")]
        result=[]
        for left,right in chain:
            a,b=by_type.get(left),by_type.get(right)
            if a and b: result.append(MemoryRelationship(mission_id=a.mission_id,from_record_id=a.record_id,to_record_id=b.record_id,relation=f"{left}_to_{right}"))
        return result
