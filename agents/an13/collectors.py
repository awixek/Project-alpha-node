"""Provider-neutral analytics collection boundaries."""
from __future__ import annotations

from typing import Protocol

from agents.an13.models import AnalyticsRequest, NormalizedMetric
from shared.constants import Platform


class AnalyticsProvider(Protocol):
    """External analytics adapter contract; no vendor is referenced here."""

    name: str

    def collect(self, request: AnalyticsRequest, platform: Platform) -> list[NormalizedMetric]:
        ...


class PublishingBaselineCollector:
    """Creates a deterministic baseline from verified publication records.

    It deliberately does not invent audience metrics. Its values are useful for
    publication-performance analysis until real analytics adapters are wired.
    """

    name = "publishing-baseline"

    def collect(self, request: AnalyticsRequest, platform: Platform) -> list[NormalizedMetric]:
        records = [r for r in request.publish.platform_records if r.platform is platform]
        if not records:
            return []
        verified = sum(1 for record in records if record.status.value == "verified")
        return [NormalizedMetric(
            platform=platform,
            source="publishing",
            provider=self.name,
            metadata={"publication_status": records[-1].status.value, "verified": str(verified)},
        )]
