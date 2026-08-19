"""AN-01 research contracts and structured result models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from shared.constants import AgentID, Platform
from shared.schemas import SourceRef


class ResearchRequest(BaseModel):
    """Validated input envelope for one Research Core execution."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    language: str = Field(default="en", min_length=1, max_length=32)
    platform: Platform | None = None
    keywords: list[str] = Field(default_factory=list)
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    search_config: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, str] = Field(default_factory=dict)


class ProviderSearchRequest(BaseModel):
    """Provider-neutral request passed through the shared API router."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    query: str = Field(..., min_length=1, max_length=2000)
    language: str = Field(default="en", min_length=1, max_length=32)
    platform: Platform | None = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    search_config: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderSearchItem:
    """Normalized item returned by a concrete provider adapter."""

    title: str
    summary: str
    url: str
    publisher: str | None = None
    published_at: datetime | None = None
    reliability: str = "unverified"
    keywords: tuple[str, ...] = ()
    provider: str = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderSearchResponse:
    """Provider response normalized before research analysis."""

    provider: str
    items: tuple[ProviderSearchItem, ...]
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchCandidate(BaseModel):
    """Ranked research candidate returned by AN-01."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    title: str = Field(..., min_length=1)
    summary: str = ""
    sources: list[SourceRef] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    freshness_score: float = Field(..., ge=0.0, le=1.0)
    authority_score: float = Field(..., ge=0.0, le=1.0)
    relevance_score: float = Field(..., ge=0.0, le=1.0)
    information_completeness: float = Field(..., ge=0.0, le=1.0)
    cross_source_confirmation: float = Field(..., ge=0.0, le=1.0)
    source_diversity: float = Field(..., ge=0.0, le=1.0)
    overall_priority_score: float = Field(..., ge=0.0, le=1.0)
    discovery_timestamp: datetime
    cluster_id: str
    supporting_providers: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class ResearchBatch(BaseModel):
    """Complete structured output of one Research Core run."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: AgentID = AgentID.RESEARCH_CORE
    mission_id: UUID
    query: str
    candidates: list[ResearchCandidate] = Field(default_factory=list)
    providers_attempted: list[str] = Field(default_factory=list)
    providers_succeeded: list[str] = Field(default_factory=list)
    provider_failures: dict[str, str] = Field(default_factory=dict)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ResearchScoringWeights:
    """Configurable scoring weights. Values are normalized at validation time."""

    freshness: float = 0.15
    authority: float = 0.20
    cross_source_confirmation: float = 0.20
    relevance: float = 0.20
    information_completeness: float = 0.10
    source_diversity: float = 0.05
    confidence: float = 0.10

    def normalized(self) -> "ResearchScoringWeights":
        values = {
            "freshness": self.freshness,
            "authority": self.authority,
            "cross_source_confirmation": self.cross_source_confirmation,
            "relevance": self.relevance,
            "information_completeness": self.information_completeness,
            "source_diversity": self.source_diversity,
            "confidence": self.confidence,
        }
        if any(value < 0 for value in values.values()):
            raise ValueError("Research scoring weights cannot be negative.")
        total = sum(values.values())
        if total <= 0:
            raise ValueError("At least one research scoring weight must be positive.")
        normalized = {key: value / total for key, value in values.items()}
        return ResearchScoringWeights(**normalized)


@dataclass(frozen=True, slots=True)
class ResearchAnalysisConfig:
    """Runtime-tunable research analysis settings.

    When no explicit object is supplied, Research Core reads the generic
    ``agents["AN-01"].settings`` bucket from the frozen shared config.
    This keeps deployment configuration in the Shared Configuration layer
    without requiring a Shared Foundation schema change.
    """

    near_duplicate_threshold: float = 0.86
    cluster_threshold: float = 0.62
    freshness_half_life_hours: float = 72.0
    max_candidates: int = 25
    weights: ResearchScoringWeights = field(default_factory=ResearchScoringWeights)
    publisher_authority: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.near_duplicate_threshold <= 1.0:
            raise ValueError("near_duplicate_threshold must be between 0 and 1.")
        if not 0.0 <= self.cluster_threshold <= 1.0:
            raise ValueError("cluster_threshold must be between 0 and 1.")
        if self.freshness_half_life_hours <= 0:
            raise ValueError("freshness_half_life_hours must be positive.")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1.")

    @classmethod
    def from_shared_config(cls) -> "ResearchAnalysisConfig":
        """Build settings from the generic frozen Shared Config agent bucket."""
        from shared.config import get_config

        settings = get_config().agents.get(AgentID.RESEARCH_CORE.value)
        values = dict(settings.settings) if settings is not None else {}
        defaults = cls()
        weight_values = dict(values.get("weights", {}))
        weights = ResearchScoringWeights(**weight_values) if weight_values else defaults.weights
        return cls(
            near_duplicate_threshold=float(values.get("near_duplicate_threshold", defaults.near_duplicate_threshold)),
            cluster_threshold=float(values.get("cluster_threshold", defaults.cluster_threshold)),
            freshness_half_life_hours=float(values.get("freshness_half_life_hours", defaults.freshness_half_life_hours)),
            max_candidates=int(values.get("max_candidates", defaults.max_candidates)),
            weights=weights,
            publisher_authority={
                str(key): float(value)
                for key, value in dict(values.get("publisher_authority", {})).items()
            },
        )
