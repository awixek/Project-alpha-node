"""Retention, archival, duplicate and integrity lifecycle policies."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from .models import LifecyclePolicy, MemoryRecord, MemoryStatus
from .storage import MemoryStore

class LifecycleManager:
    def __init__(self, store: MemoryStore): self.store=store
    @staticmethod
    def checksum(record: MemoryRecord) -> str:
        data=record.model_dump(mode="json", exclude={"checksum","updated_at"})
        return sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    def prepare(self, record: MemoryRecord) -> MemoryRecord:
        return record.model_copy(update={"checksum": self.checksum(record)})
    def apply(self, policy: LifecyclePolicy) -> list[str]:
        actions=[]; now=datetime.now(timezone.utc); records=self.store.all_records()
        for r in records:
            age=(now-r.updated_at).days
            if policy.verify_integrity and r.checksum and r.checksum != self.checksum(r): actions.append(f"integrity_warning:{r.record_id}")
            if policy.archive_after_days and age >= policy.archive_after_days and r.status == MemoryStatus.ACTIVE:
                self.store.upsert(r.model_copy(update={"status":MemoryStatus.ARCHIVED,"updated_at":now})); actions.append(f"archived:{r.record_id}")
            if policy.retention_days and age >= policy.retention_days and policy.allow_delete:
                self.store.delete(r.record_id); actions.append(f"deleted:{r.record_id}")
        return actions
    def duplicate_ids(self, threshold: float = 1.0) -> list[tuple[str,str]]:
        records=self.store.all_records(); pairs=[]
        for i,a in enumerate(records):
            for b in records[i+1:]:
                if a.mission_id != b.mission_id or a.content_type != b.content_type: continue
                if a.checksum and a.checksum == b.checksum: pairs.append((str(a.record_id),str(b.record_id)))
        return pairs
