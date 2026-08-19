"""Domain-specific insight extraction."""
from __future__ import annotations
from agents.an13.models import AnalyticsRequest, NormalizedMetric


class InsightEngine:
    def seo(self, request: AnalyticsRequest, metrics: list[NormalizedMetric]) -> list[str]:
        insights = [f"AN-04 SEO score: {request.seo.seo_score:.1f}/100."]
        if metrics and any(m.search_traffic for m in metrics):
            insights.append("Search traffic is present; future iterations should compare keyword rankings with search impressions.")
        else:
            insights.append("No provider search-traffic sample was supplied; keyword performance cannot yet be attributed from analytics.")
        return insights

    def thumbnail(self, request: AnalyticsRequest) -> list[str]:
        top = request.thumbnail.ctr_report.top_score
        return [f"Top thumbnail concept CTR score: {top:.1f}/100.",
                "Use observed CTR by concept when provider analytics become available; current result is a pre-publication intelligence baseline."]

    def publishing(self, request: AnalyticsRequest) -> list[str]:
        requested = len(request.publish.platform_records)
        verified = sum(1 for record in request.publish.platform_records if record.status.value == "verified")
        return [f"Verified publications: {verified}/{requested}.",
                "Platform sequencing and verification quality are derived from AN-12 publication records."]
