"""Prioritized, explainable recommendations for upstream agents."""
from __future__ import annotations
from agents.an13.models import AnalyticsConfig, AnalyticsRecommendation, AnalyticsRequest, RecommendationTarget


class RecommendationEngine:
    def generate(self, request: AnalyticsRequest, *, scores: dict[str, float], config: AnalyticsConfig) -> list[AnalyticsRecommendation]:
        out: list[AnalyticsRecommendation] = []
        def add(target: RecommendationTarget, priority: int, impact: float, confidence: float, action: str, rationale: str, evidence: list[str]) -> None:
            if impact < config.recommendation_threshold:
                return
            out.append(AnalyticsRecommendation(target_agent=target, priority=priority, expected_impact=impact,
                                                confidence=confidence, action=action, rationale=rationale, evidence=evidence))
        if scores.get("performance", 0) < 50:
            add(RecommendationTarget.AN01, 2, .78, .65, "Increase research coverage around topics with stronger historical or current audience demand signals.", "Low observed reach suggests topic selection should be reviewed before optimizing downstream packaging.", [f"performance={scores.get('performance',0):.1f}"])
        if scores.get("retention", 0) < 55:
            add(RecommendationTarget.AN03, 1, .82, .70, "Tighten early narrative pacing and strengthen section transitions.", "Low retention is consistent with a script-level pacing opportunity, subject to provider evidence.", [f"retention={scores.get('retention',0):.1f}"])
        if scores.get("seo", 0) < 60:
            add(RecommendationTarget.AN04, 3, .72, .85, "Review title, keyword coverage and search intent alignment.", "The existing SEO score is below the configured performance baseline.", [f"seo={scores.get('seo',0):.1f}"])
        if scores.get("thumbnail", 0) < 60:
            add(RecommendationTarget.AN10, 2, .75, .85, "Prioritize the highest-contrast, most readable thumbnail concept and test variants.", "Low pre-publication CTR quality indicates a thumbnail optimization opportunity.", [f"thumbnail={scores.get('thumbnail',0):.1f}"])
        if scores.get("publishing", 0) < 100:
            add(RecommendationTarget.AN12, 4, .58, .90, "Review platform publication failures or unverified records before campaign sequencing.", "Publication coverage is incomplete in the AN-12 package.", [f"publishing={scores.get('publishing',0):.1f}"])
        if scores.get("audience", 0) < 50:
            add(RecommendationTarget.AN05, 5, .56, .55, "Use stronger visual pacing and scene-level retention cues in future plans.", "Audience signals are weak or unavailable, so this is a lower-confidence visual recommendation.", [f"audience={scores.get('audience',0):.1f}"])
        return sorted(out, key=lambda r: (r.priority, -r.expected_impact))
