"""Stable retrieval API, independent of the storage/search implementation."""
from __future__ import annotations
from .models import MemoryQuery, MemoryRecord
from .storage import MemoryStore
from .indexing import MemoryIndexer

class SemanticRetrievalProvider:
    """Optional future extension; current implementation intentionally returns no results."""
    def search(self, query: str, limit: int = 50) -> list[MemoryRecord]:
        return []

class MemoryRetriever:
    def __init__(self, store: MemoryStore, indexer: MemoryIndexer, semantic: SemanticRetrievalProvider | None = None) -> None:
        self.store, self.indexer, self.semantic = store, indexer, semantic
    def retrieve(self, query: MemoryQuery) -> list[MemoryRecord]:
        candidates: set = set()
        indexed = [
            ("mission_id", str(query.mission_id) if query.mission_id else None),
            ("topic", query.topic.lower() if query.topic else None),
            ("keyword", query.keyword.lower() if query.keyword else None),
            ("agent", query.agent_id.value if query.agent_id else None),
            ("platform", query.platform.lower() if query.platform else None),
            ("content_type", query.content_type.value if query.content_type else None),
            ("domain", query.domain.value if query.domain else None),
        ]
        for field, value in indexed:
            if value:
                ids = self.indexer.lookup(field, value)
                candidates = ids if not candidates else candidates & ids
        records = [self.store.get(i) for i in candidates] if candidates else self.store.all_records()
        out = [r for r in records if r and self._matches(r, query)]
        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out[:query.limit]
    @staticmethod
    def _matches(r: MemoryRecord, q: MemoryQuery) -> bool:
        if r.status != q.status: return False
        if q.start_date and r.created_at < q.start_date: return False
        if q.end_date and r.created_at > q.end_date: return False
        return True
    def by_mission(self, mission_id, limit=50): return self.retrieve(MemoryQuery(mission_id=mission_id, limit=limit))
    def by_topic(self, topic, limit=50): return self.retrieve(MemoryQuery(topic=topic, limit=limit))
    def by_keyword(self, keyword, limit=50): return self.retrieve(MemoryQuery(keyword=keyword, limit=limit))
    def by_agent(self, agent_id, limit=50): return self.retrieve(MemoryQuery(agent_id=agent_id, limit=limit))
    def by_platform(self, platform, limit=50): return self.retrieve(MemoryQuery(platform=platform, limit=limit))
    def by_date(self, start_date, end_date=None, limit=50): return self.retrieve(MemoryQuery(start_date=start_date, end_date=end_date, limit=limit))
    def by_content_type(self, content_type, limit=50): return self.retrieve(MemoryQuery(content_type=content_type, limit=limit))
