"""Recommendation post-processing and ranking."""
from __future__ import annotations

from .models import OptimizationRecommendation


class RecommendationRanker:
    """Provides stable, deterministic ranking for downstream consumers."""

    def rank(self, recommendations: list[OptimizationRecommendation]) -> list[OptimizationRecommendation]:
        return sorted(
            recommendations,
            key=lambda item: (item.optimization_priority, -item.expected_impact, -item.confidence, item.target_agent.value),
        )
