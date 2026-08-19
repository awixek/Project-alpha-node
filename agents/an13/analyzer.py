"""Deterministic performance and audience analysis."""
from __future__ import annotations

from statistics import mean
from agents.an13.models import AudienceInsight, NormalizedMetric


class AnalyticsAnalyzer:
    def engagement_rate(self, metrics: list[NormalizedMetric]) -> float:
        if not metrics:
            return 0.0
        total_views = sum(m.views for m in metrics)
        total_engagement = sum(m.likes + m.comments + m.shares + m.saves for m in metrics)
        return min(1.0, total_engagement / total_views) if total_views else 0.0

    def retention(self, metrics: list[NormalizedMetric]) -> float:
        values = [m.audience_retention for m in metrics if m.audience_retention is not None]
        return mean(values) if values else 0.0

    def audience_insights(self, metrics: list[NormalizedMetric]) -> list[AudienceInsight]:
        if not metrics:
            return []
        views = sum(m.views for m in metrics)
        returning = sum(m.returning_viewers for m in metrics)
        new = sum(m.new_viewers for m in metrics)
        search = sum(m.search_traffic for m in metrics)
        recommendations = sum(m.recommendation_traffic for m in metrics)
        denominator = max(1, returning + new)
        return [
            AudienceInsight(label="returning_viewer_share", value=returning / denominator, unit="ratio", confidence=0.5 if returning + new else 0.2,
                            explanation="Share derived from normalized returning/new viewer counts."),
            AudienceInsight(label="search_traffic_share", value=search / max(1, views), unit="ratio", confidence=0.5 if views else 0.2,
                            explanation="Search traffic divided by total views; zero when provider data is unavailable."),
            AudienceInsight(label="recommendation_traffic_share", value=recommendations / max(1, views), unit="ratio", confidence=0.5 if views else 0.2,
                            explanation="Recommendation traffic divided by total views."),
        ]
