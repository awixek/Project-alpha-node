"""Typed contracts for AN-14 Evolution Engine."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.an11.models import QualityReport
from agents.an12.models import PublishPackage
from agents.an13.models import AnalyticsReport
from shared.constants import AgentID
from shared.schemas import BaseAlphaModel


class EvolutionTarget(str, Enum):
    AN01 = "AN-01"
    AN03 = "AN-03"
    AN04 = "AN-04"
    AN05 = "AN-05"
    AN10 = "AN-10"
    AN12 = "AN-12"


class PatternType(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TREND = "trend"
    STABILITY = "stability"
    OPPORTUNITY = "opportunity"


class PatternFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_id: UUID = Field(default_factory=uuid4)
    pattern_type: PatternType
    label: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class OptimizationRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_id: UUID = Field(default_factory=uuid4)
    target_agent: EvolutionTarget
    action: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    expected_impact: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    optimization_priority: int = Field(ge=1, le=10)
    implementation_difficulty: int = Field(ge=1, le=5)
    supporting_evidence: list[str] = Field(default_factory=list)
    contributing_patterns: list[UUID] = Field(default_factory=list)


class ExplainableScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100)
    calculation: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    contributing_factors: dict[str, float] = Field(default_factory=dict)


class EvolutionScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learning: ExplainableScore
    optimization: ExplainableScore
    confidence: ExplainableScore
    stability: ExplainableScore
    expected_impact: ExplainableScore


class EvolutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    optimization_threshold: float = Field(default=0.55, ge=0, le=1)
    recommendation_limit: int = Field(default=10, ge=1, le=100)
    confidence_threshold: float = Field(default=0.60, ge=0, le=1)
    learning_window: int = Field(default=7, ge=1, le=365)
    scoring_weights: dict[str, float] = Field(default_factory=lambda: {
        "learning": 0.25,
        "optimization": 0.25,
        "confidence": 0.20,
        "stability": 0.15,
        "expected_impact": 0.15,
    })
    trend_sensitivity: float = Field(default=0.15, ge=0, le=1)

    @classmethod
    def from_shared_config(cls) -> "EvolutionConfig":
        from shared.config import get_config

        agent = get_config().agents.get(AgentID.EVOLUTION_ENGINE.value)
        values = dict(agent.settings) if agent else {}
        defaults = cls()
        weights = dict(defaults.scoring_weights)
        weights.update(dict(values.get("scoring_weights", {})))
        values["scoring_weights"] = weights
        return cls(**values)

    def effective_weights(self) -> dict[str, float]:
        positive = {key: max(0.0, float(value)) for key, value in self.scoring_weights.items()}
        total = sum(positive.values())
        if total <= 0:
            raise ValueError("At least one evolution scoring weight must be positive.")
        return {key: value / total for key, value in positive.items()}


class EvolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mission_id: UUID
    analytics: AnalyticsReport
    historical_reports: list[AnalyticsReport] = Field(default_factory=list)
    publish: PublishPackage | None = None
    quality: QualityReport | None = None
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class EvolutionReport(BaseAlphaModel):
    mission_id: UUID
    agent_id: AgentID = AgentID.EVOLUTION_ENGINE
    optimization_recommendations: list[OptimizationRecommendation] = Field(default_factory=list)
    priority_ranking: list[UUID] = Field(default_factory=list)
    expected_impact: float = Field(ge=0, le=1)
    confidence_metrics: dict[str, float] = Field(default_factory=dict)
    supporting_evidence: list[str] = Field(default_factory=list)
    optimization_scores: EvolutionScores
    patterns: list[PatternFinding] = Field(default_factory=list)
    execution_statistics: dict[str, float | int | str] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
