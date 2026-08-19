from __future__ import annotations

from .models import PublishAudit, PublicationRecord, PublisherConfig
from shared.constants import Platform
from shared.schemas import ExecutionStatus


class PublishAuditor:
    def build(self, *, started_at, completed_at, quality_decision, requested: list[Platform], records: list[PublicationRecord], config: PublisherConfig, notes: list[str] | None = None) -> PublishAudit:
        published = [r.platform for r in records if r.status is not None and r.status.value == "verified"]
        failed = [r.platform for r in records if r.status.value == "failed"]
        return PublishAudit(
            started_at=started_at,
            completed_at=completed_at,
            eligible=quality_decision.value in {"PASS", "PASS_WITH_WARNINGS"},
            quality_decision=quality_decision,
            platforms_requested=requested,
            platforms_published=published,
            platforms_failed=failed,
            dry_run=config.dry_run,
            notes=notes or [],
        )
