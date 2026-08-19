"""Contracts for AN-03 Script Forge.

AN-03 owns only script-generation concerns. Shared schemas remain the
canonical source for generic script and source value objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.an01.models import ResearchBatch
from agents.an02.models import FactVerificationReport
from shared.constants import AgentID
from shared.schemas import Script, ScriptSection as SharedScriptSection, SourceRef


class ScriptStyle(str, Enum):
    EDUCATIONAL = "educational"
    DOCUMENTARY = "documentary"
    STORYTELLING = "storytelling"
    NEWS = "news"
    ANALYTICAL = "analytical"


class CitationMode(str, Enum):
    NONE = "none"
    INLINE = "inline"
    END_NOTES = "end_notes"


class SectionType(str, Enum):
    HOOK = "hook"
    INTRO = "intro"
    BACKGROUND = "background"
    MAIN_EXPLANATION = "main_explanation"
    EVIDENCE_BLOCK = "evidence_block"
    HISTORICAL_CONTEXT = "historical_context"
    COUNTERPOINTS = "counterpoints"
    CONCLUSION = "conclusion"
    CALL_TO_ACTION = "call_to_action"


DEFAULT_SECTION_ORDER: tuple[SectionType, ...] = (
    SectionType.HOOK, SectionType.INTRO, SectionType.BACKGROUND,
    SectionType.MAIN_EXPLANATION, SectionType.EVIDENCE_BLOCK,
    SectionType.HISTORICAL_CONTEXT, SectionType.COUNTERPOINTS,
    SectionType.CONCLUSION, SectionType.CALL_TO_ACTION,
)


class ScriptRequest(BaseModel):
    """Validated AN-03 execution request."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    research: ResearchBatch
    fact_check: FactVerificationReport
    style: ScriptStyle | None = None
    target_duration_seconds: int | None = Field(default=None, ge=15, le=86_400)
    tone: str | None = Field(default=None, min_length=1, max_length=256)
    section_order: list[SectionType] | None = None
    max_length: int | None = Field(default=None, ge=100, le=1_000_000)
    citation_mode: CitationMode | None = None
    language: str | None = Field(default=None, min_length=1, max_length=32)
    user_constraints: dict[str, str] = Field(default_factory=dict)
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class ScriptOutline(BaseModel):
    """Provider-independent narrative plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(..., min_length=1)
    thesis: str = Field(..., min_length=1)
    sections: list[SectionType] = Field(..., min_length=1)
    audience: str = "general"
    style: ScriptStyle


class ScriptSection(SharedScriptSection):
    """Shared script section extended only with AN-03 evidence links."""

    section_type: SectionType
    evidence_source_urls: list[str] = Field(default_factory=list)
    claim_references: list[str] = Field(default_factory=list)
    quality_notes: list[str] = Field(default_factory=list)


class ScriptMetadata(BaseModel):
    """Generation metadata and explainability information."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    style: ScriptStyle
    language: str
    tone: str
    target_duration_seconds: int
    estimated_duration_seconds: float
    word_count: int = Field(ge=0)
    citation_mode: CitationMode
    evidence_sources: list[SourceRef] = Field(default_factory=list)
    source_candidate_ids: list[UUID] = Field(default_factory=list)
    fact_claim_count: int = Field(default=0, ge=0)
    manual_review_required: bool = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScriptDocument(Script):
    """Structured AN-03 output, compatible with the shared Script model."""

    outline: ScriptOutline
    sections: list[ScriptSection] = Field(default_factory=list)
    metadata: ScriptMetadata
    evidence_sources: list[SourceRef] = Field(default_factory=list)
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_errors: list[str] = Field(default_factory=list)


class ScriptGenerationRequest(BaseModel):
    """Provider request. It contains normalized facts, not provider-specific fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    outline: ScriptOutline
    evidence_context: str = Field(..., min_length=1)
    style: ScriptStyle
    tone: str
    language: str
    target_duration_seconds: int
    max_length: int
    citation_mode: CitationMode


class ScriptGenerationResponse(BaseModel):
    """Normalized provider response consumed by the coordinator."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(..., min_length=1)
    sections: list[ScriptSection] = Field(default_factory=list)
    provider: str = Field(..., min_length=1)


@dataclass(frozen=True, slots=True)
class ScriptGenerationConfig:
    """Runtime configuration loaded from agents["AN-03"].settings."""

    style: ScriptStyle = ScriptStyle.EDUCATIONAL
    target_duration_seconds: int = 300
    tone: str = "clear, authoritative, engaging"
    section_order: tuple[SectionType, ...] = (
        SectionType.HOOK,
        SectionType.INTRO,
        SectionType.BACKGROUND,
        SectionType.MAIN_EXPLANATION,
        SectionType.EVIDENCE_BLOCK,
        SectionType.HISTORICAL_CONTEXT,
        SectionType.COUNTERPOINTS,
        SectionType.CONCLUSION,
        SectionType.CALL_TO_ACTION,
    )
    max_length: int = 20_000
    citation_mode: CitationMode = CitationMode.INLINE
    language: str = "en"

    def __post_init__(self) -> None:
        if self.target_duration_seconds < 15:
            raise ValueError("target_duration_seconds must be at least 15.")
        if self.max_length < 100:
            raise ValueError("max_length must be at least 100.")
        if not self.tone.strip():
            raise ValueError("tone must not be empty.")
        if not self.section_order:
            raise ValueError("section_order must contain at least one section.")

    @classmethod
    def from_shared_config(cls) -> "ScriptGenerationConfig":
        from shared.config import get_config

        settings = get_config().agents.get(AgentID.SCRIPT_FORGE.value)
        values = dict(settings.settings) if settings else {}
        defaults = cls()
        raw_sections = values.get("section_order", [item.value for item in defaults.section_order])
        return cls(
            style=ScriptStyle(values.get("style", defaults.style.value)),
            target_duration_seconds=int(values.get("target_duration_seconds", defaults.target_duration_seconds)),
            tone=str(values.get("tone", defaults.tone)),
            section_order=tuple(SectionType(item) for item in raw_sections),
            max_length=int(values.get("max_length", defaults.max_length)),
            citation_mode=CitationMode(values.get("citation_mode", defaults.citation_mode.value)),
            language=str(values.get("language", defaults.language)),
        )
