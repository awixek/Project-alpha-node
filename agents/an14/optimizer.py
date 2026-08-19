"""Recommendation generation for AN-14."""
from __future__ import annotations

from agents.an13.models import AnalyticsReport
from .models import EvolutionConfig, EvolutionTarget, OptimizationRecommendation, PatternFinding


class OptimizationEngine:
    """Maps measurable patterns to bounded, actionable upstream improvements."""

    _TARGET_MAP = {
        "performance": EvolutionTarget.AN01,
        "retention": EvolutionTarget.AN03,
        "seo": EvolutionTarget.AN04,
        "audience": EvolutionTarget.AN05,
        "thumbnail": EvolutionTarget.AN10,
        "publishing": EvolutionTarget.AN12,
    }

    def generate(self, reports: list[AnalyticsReport], patterns: list[PatternFinding], config: EvolutionConfig) -> list[OptimizationRecommendation]:
        recommendations: list[OptimizationRecommendation] = []
        for pattern in patterns:
            key = pattern.label.removeprefix("weak_").removeprefix("strong_")
            target = self._TARGET_MAP.get(key)
            if target is None and pattern.pattern_type.value == "trend":
                target = self._trend_target(pattern)
            if target is None:
                continue
            impact = self._impact(pattern)
            confidence = pattern.confidence
            if impact < config.optimization_threshold or confidence < config.confidence_threshold:
                continue
            action, rationale, difficulty = self._recommendation_for(target, pattern)
            recommendations.append(OptimizationRecommendation(
                target_agent=target,
                action=action,
                rationale=rationale,
                expected_impact=impact,
                confidence=confidence,
                optimization_priority=self._priority(impact, confidence),
                implementation_difficulty=difficulty,
                supporting_evidence=list(pattern.evidence),
                contributing_patterns=[pattern.pattern_id],
            ))

        # De-duplicate same target/action while preserving strongest evidence.
        unique: dict[tuple[str, str], OptimizationRecommendation] = {}
        for recommendation in recommendations:
            key = (recommendation.target_agent.value, recommendation.action)
            existing = unique.get(key)
            if existing is None or (recommendation.expected_impact, recommendation.confidence) > (existing.expected_impact, existing.confidence):
                unique[key] = recommendation
        return sorted(unique.values(), key=lambda r: (r.optimization_priority, -r.expected_impact, -r.confidence))[: config.recommendation_limit]

    @staticmethod
    def _impact(pattern: PatternFinding) -> float:
        return max(0.0, min(1.0, pattern.strength * 0.65 + pattern.confidence * 0.35))

    @staticmethod
    def _priority(impact: float, confidence: float) -> int:
        urgency = impact * 0.7 + confidence * 0.3
        return max(1, min(10, 11 - int(urgency * 10)))

    @staticmethod
    def _trend_target(pattern: PatternFinding) -> EvolutionTarget:
        name = pattern.label.lower()
        if "seo" in name or "search" in name:
            return EvolutionTarget.AN04
        if "thumbnail" in name or "ctr" in name:
            return EvolutionTarget.AN10
        if "retention" in name or "audience" in name:
            return EvolutionTarget.AN03
        if "publish" in name or "schedule" in name:
            return EvolutionTarget.AN12
        return EvolutionTarget.AN01

    @staticmethod
    def _recommendation_for(target: EvolutionTarget, pattern: PatternFinding) -> tuple[str, str, int]:
        if target is EvolutionTarget.AN01:
            return ("Prioritize future research toward topics and source profiles associated with stronger measured performance, while preserving source diversity and freshness.", "AN-13 shows a reach/performance opportunity; topic selection should be optimized before downstream packaging changes.", 3)
        if target is EvolutionTarget.AN03:
            return ("Strengthen the opening hook, tighten pacing, and align section transitions with observed retention signals.", "Retention is the clearest downstream content lever when audience drop-off or weak retention is observed.", 3)
        if target is EvolutionTarget.AN04:
            return ("Refine primary keywords, title intent, metadata coverage, and search-facing wording using observed SEO performance.", "The analytics evidence indicates a search/discoverability opportunity that AN-04 can address without changing the factual content.", 2)
        if target is EvolutionTarget.AN05:
            return ("Adjust visual pacing and scene complexity toward patterns associated with stronger audience engagement.", "Audience signals can guide future visual planning while AN-05 remains the sole owner of scene decisions.", 4)
        if target is EvolutionTarget.AN10:
            return ("Favor thumbnail layouts, contrast, text density, and focal compositions associated with stronger CTR signals.", "Thumbnail effectiveness is a measurable packaging lever and can be improved without altering the finished video.", 2)
        return ("Optimize publication timing, platform sequencing, and rollout priority using observed verified-publication performance.", "Publishing performance can improve distribution efficiency without modifying content itself.", 2)
