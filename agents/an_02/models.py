"""AN-02 contracts built around the frozen Shared Foundation schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from shared.constants import AgentID
from shared.schemas import FactCheckClaim, FactVerdict, SourceRef


class ClaimType(str, Enum):
    """Deterministic factual claim categories."""

    FACT = "fact"
    STATISTIC = "statistic"
    DATE = "date"
    QUOTE = "quote"
    OPINION = "opinion"
    PREDICTION = "prediction"


class VerificationStatus(str, Enum):
    """Detailed AN-02 verification state."""

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"
    OUTDATED = "outdated"
    UNVERIFIABLE = "unverifiable"
    OPINION = "opinion"


class EvidenceItem(BaseModel):
    """Normalized provider evidence returned through the AN-02 provider boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim: str = Field(..., min_length=1)
    source: SourceRef
    excerpt: str = ""
    evidence_statement: str = ""
    supports_claim: bool | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str = Field(default="unknown", min_length=1)
    evidence_quality: float = Field(default=0.5, ge=0.0, le=1.0)


class VerifiedClaim(FactCheckClaim):
    """Extended Shared FactCheckClaim carrying AN-02 explainability fields."""

    claim_type: ClaimType
    verification_status: VerificationStatus
    conflicting_sources: list[SourceRef] = Field(default_factory=list)
    evidence_summary: str = ""
    verification_notes: list[str] = Field(default_factory=list)
    verification_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reliability_score: float = Field(default=0.0, ge=0.0, le=1.0)
    manual_review_required: bool = False
    evidence_quality: float = Field(default=0.0, ge=0.0, le=1.0)
    independent_confirmations: int = Field(default=0, ge=0)
    contradiction_severity: float = Field(default=0.0, ge=0.0, le=1.0)


class FactVerificationReport(BaseModel):
    """Structured AN-02 output returned to AN-17."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: AgentID = AgentID.FACT_GUARDIAN
    mission_id: UUID
    source_research_id: UUID | None = None
    claims: list[VerifiedClaim] = Field(default_factory=list)
    overall_reliability_score: float = Field(..., ge=0.0, le=1.0)
    verification_confidence: float = Field(..., ge=0.0, le=1.0)
    overall_pass: bool
    manual_review_required: bool
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider_failures: dict[str, str] = Field(default_factory=dict)
    claims_extracted: int = Field(default=0, ge=0)
    claims_verified: int = Field(default=0, ge=0)
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class FactCheckRequest(BaseModel):
    """Provider-neutral AN-02 input."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    research: Any
    language: str = Field(default="en", min_length=1, max_length=32)
    search_config: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FactScoringWeights:
    """Configurable reliability weights."""

    source_authority: float = 0.20
    independent_confirmations: float = 0.20
    evidence_consistency: float = 0.20
    freshness: float = 0.10
    citation_quality: float = 0.10
    official_source: float = 0.10
    contradiction_severity: float = 0.10

    def normalized(self) -> "FactScoringWeights":
        values = {
            "source_authority": self.source_authority,
            "independent_confirmations": self.independent_confirmations,
            "evidence_consistency": self.evidence_consistency,
            "freshness": self.freshness,
            "citation_quality": self.citation_quality,
            "official_source": self.official_source,
            "contradiction_severity": self.contradiction_severity,
        }
        if any(value < 0 for value in values.values()):
            raise ValueError("Fact scoring weights cannot be negative.")
        total = sum(values.values())
        if total <= 0:
            raise ValueError("At least one fact scoring weight must be positive.")
        return FactScoringWeights(**{key: value / total for key, value in values.items()})


@dataclass(frozen=True, slots=True)
class FactAnalysisConfig:
    """AN-02 settings loaded from agents['AN-02'].settings."""

    weights: FactScoringWeights = field(default_factory=FactScoringWeights)
    freshness_half_life_hours: float = 720.0
    min_verification_confidence: float = 0.70
    min_reliability_score: float = 0.70
    manual_review_on_single_source: bool = True
    manual_review_on_conflict: bool = True
    max_claims: int = 100

    def __post_init__(self) -> None:
        if self.freshness_half_life_hours <= 0:
            raise ValueError("freshness_half_life_hours must be positive.")
        for name in ("min_verification_confidence", "min_reliability_score"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1.")
        if self.max_claims < 1:
            raise ValueError("max_claims must be at least 1.")

    @classmethod
    def from_shared_config(cls) -> "FactAnalysisConfig":
        from shared.config import get_config

        settings = get_config().agents.get(AgentID.FACT_GUARDIAN.value)
        values = dict(settings.settings) if settings else {}
        defaults = cls()
        weight_values = dict(values.get("weights", {}))
        weights = FactScoringWeights(**weight_values) if weight_values else defaults.weights
        return cls(
            weights=weights,
            freshness_half_life_hours=float(values.get("freshness_half_life_hours", defaults.freshness_half_life_hours)),
            min_verification_confidence=float(values.get("min_verification_confidence", defaults.min_verification_confidence)),
            min_reliability_score=float(values.get("min_reliability_score", defaults.min_reliability_score)),
            manual_review_on_single_source=bool(values.get("manual_review_on_single_source", defaults.manual_review_on_single_source)),
            manual_review_on_conflict=bool(values.get("manual_review_on_conflict", defaults.manual_review_on_conflict)),
            max_claims=int(values.get("max_claims", defaults.max_claims)),
        )
