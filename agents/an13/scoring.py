"""Explainable analytics scoring engine."""
from __future__ import annotations

from statistics import mean
from agents.an13.models import AnalyticsConfig, NormalizedMetric, PerformanceScore


class AnalyticsScorer:
    def score(self, metrics: list[NormalizedMetric], *, seo: float, thumbnail: float, publishing: float,
              audience: float, quality: float, weights: dict[str, float]) -> dict[str, PerformanceScore]:
        views = sum(m.views for m in metrics)
        impressions = sum(m.impressions for m in metrics)
        likes = sum(m.likes for m in metrics)
        comments = sum(m.comments for m in metrics)
        shares = sum(m.shares for m in metrics)
        ctr_values = [m.click_through_rate for m in metrics if m.click_through_rate is not None]
        retention_values = [m.audience_retention for m in metrics if m.audience_retention is not None]
        engagement_rate = min(1.0, (likes + comments + shares) / max(1, views))
        ctr = mean(ctr_values) if ctr_values else 0.0
        retention = mean(retention_values) if retention_values else 0.0
        performance = min(100.0, views / max(1, impressions) * 100 if impressions else min(100.0, views))
        engagement = engagement_rate * 100
        confidence = 100.0 if metrics and any(m.source.value == "provider" for m in metrics) else 45.0
        factors = {
            "performance": performance,
            "engagement": engagement,
            "seo": seo,
            "retention": retention * 100,
            "thumbnail": thumbnail,
            "publishing": publishing,
            "audience": audience,
            "confidence": confidence,
        }
        overall = sum(factors[k] * weights.get(k, 0.0) for k in factors)
        return {
            "performance": PerformanceScore(score=performance, calculation=f"min(100, views/impressions*100) = {performance:.2f}",
                                             explanation=f"Performance reflects reach relative to impressions; views={views}, impressions={impressions}.",
                                             confidence=confidence / 100, contributing_factors={"views": float(views), "impressions": float(impressions)}),
            "engagement": PerformanceScore(score=engagement, calculation=f"(likes+comments+shares)/views*100 = {engagement:.2f}",
                                             explanation="Engagement uses normalized interaction counts without opaque model inference.",
                                             confidence=confidence / 100, contributing_factors={"likes": float(likes), "comments": float(comments), "shares": float(shares)}),
            "seo": PerformanceScore(score=seo, calculation="AN-04 seo_score passthrough", explanation="SEO performance is anchored to the existing SEO Brain score until provider search analytics are available.", confidence=.9, contributing_factors={"seo_score": seo}),
            "retention": PerformanceScore(score=retention * 100, calculation=f"mean(audience_retention)*100 = {retention*100:.2f}", explanation="Retention uses provider-supplied audience retention when available.", confidence=.7 if retention_values else .25, contributing_factors={"sample_count": float(len(retention_values))}),
            "thumbnail": PerformanceScore(score=thumbnail, calculation="AN-10 top CTR concept score", explanation="Thumbnail effectiveness starts from the completed thumbnail CTR score.", confidence=.9, contributing_factors={"thumbnail_score": thumbnail}),
            "publishing": PerformanceScore(score=publishing, calculation="verified platforms / requested platforms * 100", explanation="Publishing performance reflects verified publication coverage.", confidence=.95, contributing_factors={"publishing_score": publishing}),
            "audience": PerformanceScore(score=audience, calculation="audience signal composite", explanation="Audience score combines retention and traffic-source signals.", confidence=.6, contributing_factors={"audience_score": audience}),
            "overall": PerformanceScore(score=overall, calculation="weighted sum of component scores", explanation=f"Weights are normalized from agents['AN-13'].settings: {weights}.", confidence=confidence / 100, contributing_factors=factors),
        }
