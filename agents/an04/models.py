"""Contracts and runtime configuration for AN-04 SEO Brain."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agents.an03.models import ScriptDocument
from shared.constants import AgentID
from shared.schemas import SEOMetadata


class SEOKeywordType(str, Enum):
    """Classification used by the deterministic keyword engine."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    LONG_TAIL = "long_tail"
    SEMANTIC = "semantic"


class SEORequest(BaseModel):
    """Validated input envelope for one SEO Brain execution."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    script: ScriptDocument
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class OpenGraphMetadata(BaseModel):
    """Open Graph fields generated from the optimized content."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str
    description: str
    type: str = "article"
    url: str | None = None
    locale: str = "en_US"


class TwitterCardMetadata(BaseModel):
    """Twitter/X card metadata."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    card: str = "summary_large_image"
    title: str
    description: str


class SEOScoreBreakdown(BaseModel):
    """Explainable components contributing to the overall SEO score."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title_quality: float = Field(ge=0, le=100)
    keyword_coverage: float = Field(ge=0, le=100)
    readability: float = Field(ge=0, le=100)
    keyword_density: float = Field(ge=0, le=100)
    content_completeness: float = Field(ge=0, le=100)
    clickbait_penalty: float = Field(ge=0, le=100)


class SEOResult(BaseModel):
    """Complete AN-04 output returned to AN-17 and downstream agents."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: AgentID = AgentID.SEO_BRAIN
    mission_id: UUID
    optimized_title: str = Field(..., min_length=1)
    alternative_titles: list[str] = Field(default_factory=list)
    primary_keywords: list[str] = Field(default_factory=list)
    secondary_keywords: list[str] = Field(default_factory=list)
    long_tail_keywords: list[str] = Field(default_factory=list)
    semantic_clusters: dict[str, list[str]] = Field(default_factory=dict)
    hashtags: list[str] = Field(default_factory=list)
    slug: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    excerpt: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    metadata: SEOMetadata
    open_graph: OpenGraphMetadata
    twitter_card: TwitterCardMetadata
    readability_score: float = Field(ge=0, le=100)
    seo_score: float = Field(ge=0, le=100)
    clickbait_score: float = Field(ge=0, le=100)
    keyword_density: float = Field(ge=0, le=100)
    score_breakdown: SEOScoreBreakdown
    recommendations: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class SEOConfig:
    """Deployment-tunable AN-04 settings from agents['AN-04'].settings."""

    language: str = "en"
    title_min_length: int = 35
    title_max_length: int = 65
    description_max_length: int = 160
    excerpt_max_length: int = 240
    max_primary_keywords: int = 5
    max_secondary_keywords: int = 10
    max_long_tail_keywords: int = 10
    max_semantic_clusters: int = 6
    max_hashtags: int = 10
    max_tags: int = 20
    keyword_density_warning: float = 3.0
    clickbait_warning: float = 55.0
    preferred_keyword_length: int = 2
    title_variations: int = 5
    site_url: str | None = None
    locale: str = "en_US"
    score_weights: dict[str, float] = field(
        default_factory=lambda: {
            "title_quality": 0.25,
            "keyword_coverage": 0.20,
            "readability": 0.20,
            "keyword_density": 0.10,
            "content_completeness": 0.15,
            "clickbait_penalty": 0.10,
        }
    )

    def __post_init__(self) -> None:
        if self.title_min_length < 1 or self.title_max_length < self.title_min_length:
            raise ValueError("Invalid title length bounds.")
        if self.description_max_length < 40:
            raise ValueError("description_max_length must be at least 40.")
        if self.excerpt_max_length < 40:
            raise ValueError("excerpt_max_length must be at least 40.")
        if self.preferred_keyword_length < 1:
            raise ValueError("preferred_keyword_length must be positive.")
        if self.title_variations < 1:
            raise ValueError("title_variations must be positive.")
        if self.keyword_density_warning <= 0:
            raise ValueError("keyword_density_warning must be positive.")
        if not 0 <= self.clickbait_warning <= 100:
            raise ValueError("clickbait_warning must be between 0 and 100.")
        if any(value < 0 for value in self.score_weights.values()):
            raise ValueError("SEO score weights cannot be negative.")
        if sum(self.score_weights.values()) <= 0:
            raise ValueError("At least one SEO score weight must be positive.")

    @classmethod
    def from_shared_config(cls) -> "SEOConfig":
        """Load AN-04 settings without changing the frozen Shared Config."""
        from shared.config import get_config

        settings = get_config().agents.get(AgentID.SEO_BRAIN.value)
        values = dict(settings.settings) if settings else {}
        defaults = cls()
        weights = dict(defaults.score_weights)
        weights.update(dict(values.get("score_weights", {})))
        return cls(
            language=str(values.get("language", defaults.language)),
            title_min_length=int(values.get("title_min_length", defaults.title_min_length)),
            title_max_length=int(values.get("title_max_length", defaults.title_max_length)),
            description_max_length=int(values.get("description_max_length", defaults.description_max_length)),
            excerpt_max_length=int(values.get("excerpt_max_length", defaults.excerpt_max_length)),
            max_primary_keywords=int(values.get("max_primary_keywords", defaults.max_primary_keywords)),
            max_secondary_keywords=int(values.get("max_secondary_keywords", defaults.max_secondary_keywords)),
            max_long_tail_keywords=int(values.get("max_long_tail_keywords", defaults.max_long_tail_keywords)),
            max_semantic_clusters=int(values.get("max_semantic_clusters", defaults.max_semantic_clusters)),
            max_hashtags=int(values.get("max_hashtags", defaults.max_hashtags)),
            max_tags=int(values.get("max_tags", defaults.max_tags)),
            keyword_density_warning=float(values.get("keyword_density_warning", defaults.keyword_density_warning)),
            clickbait_warning=float(values.get("clickbait_warning", defaults.clickbait_warning)),
            preferred_keyword_length=int(values.get("preferred_keyword_length", defaults.preferred_keyword_length)),
            title_variations=int(values.get("title_variations", defaults.title_variations)),
            site_url=values.get("site_url", defaults.site_url),
            locale=str(values.get("locale", defaults.locale)),
            score_weights=weights,
        )
