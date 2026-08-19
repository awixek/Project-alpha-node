from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.an03.models import ScriptDocument
from agents.an04.models import SEOResult
from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an09.models import VideoPackage
from shared.constants import AgentID
from shared.schemas import BaseAlphaModel


class ThumbnailStrategy(str, Enum):
    DOCUMENTARY = "documentary"
    EDUCATIONAL = "educational"
    HISTORICAL = "historical"
    MYTH_VS_FACT = "myth_vs_fact"
    COMPARISON = "comparison"
    TIMELINE = "timeline"
    BEFORE_AFTER = "before_after"
    BREAKING_NEWS = "breaking_news"
    CURIOSITY_GAP = "curiosity_gap"
    QUESTION_STYLE = "question_style"


class ThumbnailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mission_id: UUID
    video: VideoPackage
    vision_plan: VisionPlan
    assets: AssetPackage
    script: ScriptDocument
    seo: SEOResult | None = None
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class VisualAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dominant_subject: str
    emotional_peak: str
    educational_highlight: str
    curiosity_moment: str
    negative_space: str
    focal_path: str
    color_harmony: float = Field(ge=0, le=100)
    contrast: float = Field(ge=0, le=100)
    visual_clutter: float = Field(ge=0, le=100)
    mobile_visibility: float = Field(ge=0, le=100)
    evidence_basis: list[str] = Field(default_factory=list)


class CTRScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: float = Field(ge=0, le=100)
    curiosity: float = Field(ge=0, le=100)
    readability: float = Field(ge=0, le=100)
    contrast: float = Field(ge=0, le=100)
    composition: float = Field(ge=0, le=100)
    branding: float = Field(ge=0, le=100)
    mobile_visibility: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=100)
    recommendation_reason: str
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class ThumbnailLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    focal_region: str
    text_region: str
    branding_region: str
    composition: str
    aspect_ratio: str
    text_density: str
    visual_hierarchy: list[str] = Field(default_factory=list)


class ThumbnailConcept(BaseAlphaModel):
    concept_id: UUID = Field(default_factory=uuid4)
    rank: int = Field(default=0, ge=0)
    strategy: ThumbnailStrategy
    title: str = Field(min_length=1)
    focal_subject: str = Field(min_length=1)
    emotional_hook: str = Field(min_length=1)
    text_overlay: str | None = None
    layout: ThumbnailLayout
    branding: dict[str, str] = Field(default_factory=dict)
    prompt: str = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    visual_analysis: VisualAnalysis
    ctr_score: CTRScore
    supporting_scene_ids: list[int] = Field(default_factory=list)
    supporting_asset_ids: list[UUID] = Field(default_factory=list)
    factual_guardrails: list[str] = Field(default_factory=list)


class CTRReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates_considered: int = Field(ge=0)
    candidates_ranked: int = Field(ge=0)
    average_score: float = Field(ge=0, le=100)
    top_score: float = Field(ge=0, le=100)
    weights: dict[str, float] = Field(default_factory=dict)
    methodology: list[str] = Field(default_factory=list)


class ThumbnailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    number_of_candidates: int = Field(default=5, ge=1, le=20)
    thumbnail_style: str = Field(default="cinematic", min_length=1, max_length=64)
    branding: str = Field(default="subtle", min_length=1, max_length=64)
    color_palette: str = Field(default="high-contrast natural", min_length=1, max_length=128)
    text_density: str = Field(default="low", min_length=1, max_length=32)
    font_preferences: str = Field(default="bold sans-serif", min_length=1, max_length=128)
    ctr_weights: dict[str, float] = Field(default_factory=lambda: {
        "curiosity": 0.22,
        "readability": 0.16,
        "contrast": 0.14,
        "composition": 0.18,
        "branding": 0.08,
        "mobile_visibility": 0.14,
        "confidence": 0.08,
    })
    layout_preferences: str = Field(default="clear focal subject with negative space", min_length=1, max_length=256)
    aspect_ratio: str = Field(default="16:9", min_length=3, max_length=16)
    max_text_characters: int = Field(default=32, ge=0, le=120)

    @classmethod
    def from_shared_config(cls) -> "ThumbnailConfig":
        from shared.config import get_config
        settings = get_config().agents.get(AgentID.THUMBNAIL_STUDIO.value)
        values = dict(settings.settings) if settings else {}
        defaults = cls()
        weights = dict(defaults.ctr_weights)
        weights.update(values.get("CTR_weights", values.get("ctr_weights", {})))
        values["ctr_weights"] = weights
        return cls(**values)


class ThumbnailPackage(BaseAlphaModel):
    mission_id: UUID
    agent_id: AgentID = AgentID.THUMBNAIL_STUDIO
    ranked_concepts: list[ThumbnailConcept] = Field(default_factory=list)
    recommendation: str
    visual_analysis: VisualAnalysis
    ctr_report: CTRReport
    generation_statistics: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
