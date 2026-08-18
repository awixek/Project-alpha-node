"""
shared/schemas.py

Project Alpha Node — Shared Schema Layer
==========================================

This module defines the complete data-contract layer shared by every agent
in the Alpha Node platform (AN-01 .. AN-17 and any future agent).

Design rules enforced in this file:
    * No business logic, no API calls, no logging, no calculations.
    * Pure data structures only (Pydantic v2 models + Enums + type aliases).
    * Every model is strongly typed, self-documenting, and serializable.
    * Records that represent a completed fact are immutable (frozen=True).
    * Mutable working state (e.g. MissionState) is NOT frozen, since agents
      progress it over time.
    * Every model carries `schema_version` so future agents can evolve the
      contract without breaking older records.

This file must remain dependency-free with respect to the rest of the
platform: it may only depend on the Python standard library, pydantic,
and shared.constants. shared.constants is itself stdlib-only (no
pydantic, no I/O, no config, no logging), so importing the four
platform-wide vocabulary enums (AgentID, MissionStatus, WorkflowStage,
Platform) from there instead of redefining them here does not pull in
any real dependency — it only removes duplicate enum definitions that
had drifted out of sync with shared.constants (see Phase 2.1 Foundation
Review). Every other enum in this file is schema-specific and stays
defined here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from shared.constants import AgentID, MissionStatus, Platform, WorkflowStage

# ──────────────────────────────────────────────────────────────────────────
# Schema versioning
# ──────────────────────────────────────────────────────────────────────────

SCHEMA_VERSION: str = "1.0.0"


# ──────────────────────────────────────────────────────────────────────────
# Base model
# ──────────────────────────────────────────────────────────────────────────

class BaseAlphaModel(BaseModel):
    """
    Base class for every schema in Alpha Node.

    Provides:
        * A consistent Pydantic configuration across the platform.
        * A `schema_version` field so consumers can detect and handle
          older/newer payload shapes gracefully.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        populate_by_name=True,
    )

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Version of the schema this record was created against.",
    )


class ImmutableAlphaModel(BaseAlphaModel):
    """Base class for records that represent a completed, unchangeable fact."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        populate_by_name=True,
        frozen=True,
    )


# ──────────────────────────────────────────────────────────────────────────
# Enums — shared vocabulary across all agents
#
# AgentID, MissionStatus, WorkflowStage, and Platform are imported from
# shared.constants (the canonical source for all four — see that
# module's "NOTE ON AgentID / MissionStatus / ..." docstring) rather
# than redefined here. The enums below are schema-specific and have no
# canonical definition outside this file.
# ──────────────────────────────────────────────────────────────────────────

class ExecutionStatus(str, Enum):
    """Result status of a single agent execution."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


class Severity(str, Enum):
    """Severity levels for errors, warnings, and quality findings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class SourceReliability(str, Enum):
    """Reliability classification for a research source."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"


class FactVerdict(str, Enum):
    """Outcome of a single fact-check claim."""

    VERIFIED_TRUE = "verified_true"
    VERIFIED_FALSE = "verified_false"
    PARTIALLY_TRUE = "partially_true"
    UNVERIFIABLE = "unverifiable"
    OPINION = "opinion"


class AssetType(str, Enum):
    """Type discriminator for generated media assets."""

    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    SUBTITLE = "subtitle"
    THUMBNAIL = "thumbnail"


class ApprovalDecision(str, Enum):
    """Human-in-the-loop approval outcomes."""

    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    PENDING = "pending"


# ──────────────────────────────────────────────────────────────────────────
# Small reusable value objects
# ──────────────────────────────────────────────────────────────────────────

class SourceRef(BaseAlphaModel):
    """A single reference to an external source used in research."""

    url: str = Field(..., description="URL of the source.")
    title: str | None = Field(default=None, description="Title of the source page.")
    publisher: str | None = Field(default=None, description="Publisher or site name.")
    published_at: datetime | None = Field(
        default=None, description="Original publication date, if known."
    )
    reliability: SourceReliability = Field(
        default=SourceReliability.UNVERIFIED,
        description="Assessed reliability of this source.",
    )
    retrieved_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when this source was fetched.",
    )


class TimeRange(BaseAlphaModel):
    """A start/end timestamp pair, used for timed assets (subtitles, clips)."""

    start_seconds: float = Field(..., ge=0, description="Start offset in seconds.")
    end_seconds: float = Field(..., ge=0, description="End offset in seconds.")


class ErrorReport(BaseAlphaModel):
    """Structured error payload returned instead of a raw exception."""

    agent_id: AgentID = Field(..., description="Agent that raised the error.")
    severity: Severity = Field(..., description="Severity of the error.")
    code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable error message.")
    retryable: bool = Field(
        default=False, description="Whether the operation may be safely retried."
    )
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    context: dict[str, str] = Field(
        default_factory=dict,
        description="Additional key/value context for debugging (no secrets).",
    )


class APIResponse(BaseAlphaModel):
    """Normalized envelope for any external API call result."""

    provider: str = Field(..., description="Name of the external provider (config-driven).")
    endpoint: str = Field(..., description="Logical endpoint or operation name.")
    status_code: int | None = Field(default=None, description="HTTP status code, if applicable.")
    success: bool = Field(..., description="Whether the call succeeded.")
    latency_ms: float | None = Field(default=None, ge=0, description="Round-trip latency.")
    error: ErrorReport | None = Field(default=None, description="Populated when success=False.")


# ──────────────────────────────────────────────────────────────────────────
# Generic agent result wrapper
# ──────────────────────────────────────────────────────────────────────────

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class AgentResult(BaseAlphaModel, Generic[PayloadT]):
    """
    Universal wrapper every agent returns to the Orchestrator.

    The Orchestrator only ever consumes AgentResult objects — it never
    depends on any individual agent's internal payload type.
    """

    agent_id: AgentID = Field(..., description="Agent that produced this result.")
    mission_id: UUID = Field(..., description="Mission this result belongs to.")
    status: ExecutionStatus = Field(..., description="Outcome of the execution.")
    payload: PayloadT | None = Field(
        default=None, description="Typed output payload, present on success."
    )
    error: ErrorReport | None = Field(
        default=None, description="Populated when status is FAILED or PARTIAL_SUCCESS."
    )
    started_at: datetime = Field(..., description="Execution start timestamp.")
    completed_at: datetime | None = Field(
        default=None, description="Execution end timestamp, if finished."
    )
    retry_count: int = Field(default=0, ge=0, description="Number of retries attempted.")


# ──────────────────────────────────────────────────────────────────────────
# Mission / orchestration schemas
# ──────────────────────────────────────────────────────────────────────────

class Topic(BaseAlphaModel):
    """The seed topic that a Mission is built around."""

    title: str = Field(..., description="Working title or subject of the topic.")
    description: str | None = Field(default=None, description="Extra framing/context.")
    keywords: list[str] = Field(default_factory=list, description="Seed keywords.")
    target_audience: str | None = Field(default=None, description="Intended audience.")


class Mission(BaseAlphaModel):
    """
    The root unit of work in Alpha Node. One Mission produces one piece
    of content (and its distribution) end to end.
    """

    mission_id: UUID = Field(default_factory=uuid4)
    topic: Topic = Field(..., description="Topic this mission is built around.")
    requested_by: str = Field(..., description="User or system that created the mission.")
    target_platforms: list[Platform] = Field(
        default_factory=list, description="Platforms this mission should publish to."
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    priority: int = Field(default=5, ge=1, le=10, description="1=highest, 10=lowest.")
    requires_human_approval: bool = Field(
        default=True, description="Whether this mission must pause for approval before publish."
    )


class MissionState(BaseAlphaModel):
    """
    Mutable progress tracker for a Mission. Updated by the Orchestrator as
    each agent completes its stage. This is the only schema in the file
    that is expected to be re-saved repeatedly over a mission's lifetime.
    """

    mission_id: UUID = Field(..., description="Mission this state belongs to.")
    status: MissionStatus = Field(
        ..., description="Coarse mission lifecycle state (see shared.constants.MissionStatus)."
    )
    stage: WorkflowStage = Field(
        default=WorkflowStage.MISSION_CREATED,
        description="Granular pipeline position (see shared.constants.WorkflowStage). "
        "Distinct from `status`: two missions can share a `status` of RUNNING while "
        "sitting at completely different `stage` values.",
    )
    current_agent: AgentID | None = Field(
        default=None, description="Agent currently processing this mission."
    )
    completed_agents: list[AgentID] = Field(
        default_factory=list, description="Agents that have finished successfully."
    )
    artifact_ids: dict[str, UUID] = Field(
        default_factory=dict,
        description="Map of artifact type name -> artifact UUID produced so far "
        "(e.g. 'script' -> Script.script_id). Enables resume-instead-of-restart.",
    )
    last_error: ErrorReport | None = Field(default=None, description="Most recent error, if any.")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DecisionRecord(ImmutableAlphaModel):
    """An immutable log of a decision made by an agent or a human."""

    decision_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Mission this decision applies to.")
    made_by: str = Field(..., description="AgentID value or human identifier.")
    decision: str = Field(..., description="Short description of the decision made.")
    rationale: str | None = Field(default=None, description="Why this decision was made.")
    made_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowEvent(ImmutableAlphaModel):
    """A single immutable event on the shared Event Bus."""

    event_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID | None = Field(default=None, description="Related mission, if any.")
    agent_id: AgentID | None = Field(default=None, description="Emitting agent, if any.")
    event_type: str = Field(..., description="Dot-separated event name, e.g. 'mission.created'.")
    payload: dict[str, str] = Field(default_factory=dict, description="Lightweight event data.")
    occurred_at: datetime = Field(default_factory=datetime.utcnow)


class ApprovalRequest(BaseAlphaModel):
    """Human-in-the-loop checkpoint record."""

    approval_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Mission awaiting approval.")
    requested_stage: WorkflowStage = Field(..., description="Stage the mission is paused at.")
    decision: ApprovalDecision = Field(default=ApprovalDecision.PENDING)
    reviewer: str | None = Field(default=None, description="Human reviewer identifier.")
    comments: str | None = Field(default=None, description="Reviewer comments.")
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = Field(default=None)


# ──────────────────────────────────────────────────────────────────────────
# Research & fact-checking schemas (AN-01, AN-02)
# ──────────────────────────────────────────────────────────────────────────

class Research(BaseAlphaModel):
    """Aggregated research output produced by the Research Core."""

    research_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    summary: str = Field(..., description="Synthesized summary of findings.")
    key_points: list[str] = Field(default_factory=list, description="Bullet-level findings.")
    sources: list[SourceRef] = Field(default_factory=list, description="Sources consulted.")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FactCheckClaim(BaseAlphaModel):
    """A single claim evaluated by the Fact Guardian."""

    claim: str = Field(..., description="The claim being checked, verbatim.")
    verdict: FactVerdict = Field(..., description="Outcome of the check.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in the verdict.")
    supporting_sources: list[SourceRef] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="Explanation of the verdict.")


class FactCheck(ImmutableAlphaModel):
    """Completed fact-check pass over a Research or Script artifact."""

    fact_check_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    claims: list[FactCheckClaim] = Field(default_factory=list)
    overall_pass: bool = Field(..., description="Whether the content clears the fact bar.")
    checked_at: datetime = Field(default_factory=datetime.utcnow)


# ──────────────────────────────────────────────────────────────────────────
# Script & SEO schemas (AN-03, AN-04)
# ──────────────────────────────────────────────────────────────────────────

class ScriptSection(BaseAlphaModel):
    """A single beat/section within a script."""

    order: int = Field(..., ge=0, description="Position of this section in the script.")
    heading: str | None = Field(default=None, description="Optional section heading.")
    narration: str = Field(..., description="Narration/voiceover text for this section.")
    on_screen_text: str | None = Field(default=None, description="Any on-screen text overlay.")
    visual_notes: str | None = Field(default=None, description="Guidance for Vision Planner.")
    estimated_duration_seconds: float | None = Field(default=None, ge=0)


class Script(BaseAlphaModel):
    """Full script produced by Script Forge."""

    script_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    title: str = Field(..., description="Working title of the piece.")
    sections: list[ScriptSection] = Field(default_factory=list)
    tone: str | None = Field(default=None, description="Intended tone/voice of the script.")
    version: int = Field(default=1, ge=1, description="Draft version number.")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SEOMetadata(BaseAlphaModel):
    """Metadata produced by SEO Brain for a mission's content."""

    seo_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    primary_title: str = Field(..., description="SEO-optimized title.")
    alt_titles: list[str] = Field(default_factory=list, description="A/B title candidates.")
    description: str = Field(..., description="SEO-optimized description.")
    tags: list[str] = Field(default_factory=list, description="Search tags/keywords.")
    hashtags: list[str] = Field(default_factory=list, description="Social hashtags.")


# ──────────────────────────────────────────────────────────────────────────
# Visual, voice, subtitle, video, thumbnail schemas (AN-05..AN-10)
# ──────────────────────────────────────────────────────────────────────────

class VisualShot(BaseAlphaModel):
    """A single planned shot/scene within the Visual Plan."""

    order: int = Field(..., ge=0, description="Position of this shot in the sequence.")
    script_section_order: int | None = Field(
        default=None, description="Corresponding ScriptSection.order, if linked."
    )
    prompt: str = Field(..., description="Generation prompt / creative brief for this shot.")
    style_notes: str | None = Field(default=None, description="Style/consistency guidance.")
    duration_seconds: float | None = Field(default=None, ge=0)


class VisualPlan(BaseAlphaModel):
    """Full shot list produced by Vision Planner, consumed by Vision Creator."""

    visual_plan_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    shots: list[VisualShot] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MediaAsset(BaseAlphaModel):
    """
    Common shape for any generated media file (image, voice, video,
    subtitle, thumbnail). Specific asset schemas below extend this.
    """

    asset_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    asset_type: AssetType = Field(..., description="Discriminator for this asset's kind.")
    storage_path: str = Field(..., description="Path or URI where the asset is stored.")
    provider: str = Field(..., description="Generation provider, from config (no vendor lock-in).")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    checksum: str | None = Field(default=None, description="Integrity hash of the file.")


class ImageAsset(MediaAsset):
    """A generated image, typically tied to a VisualShot."""

    asset_type: AssetType = Field(default=AssetType.IMAGE, frozen=True)
    shot_order: int | None = Field(default=None, description="Linked VisualShot.order.")
    width_px: int | None = Field(default=None, gt=0)
    height_px: int | None = Field(default=None, gt=0)


class VoiceAsset(MediaAsset):
    """A generated voiceover track."""

    asset_type: AssetType = Field(default=AssetType.VOICE, frozen=True)
    script_id: UUID | None = Field(default=None, description="Script this narration is for.")
    voice_profile: str | None = Field(default=None, description="Voice/persona identifier.")
    duration_seconds: float | None = Field(default=None, ge=0)


class SubtitleLine(BaseAlphaModel):
    """A single timed subtitle line."""

    time_range: TimeRange = Field(..., description="Timing of this line.")
    text: str = Field(..., description="Subtitle text.")


class Subtitle(MediaAsset):
    """Full subtitle track produced by the Subtitle Engine."""

    asset_type: AssetType = Field(default=AssetType.SUBTITLE, frozen=True)
    language: str = Field(default="en", description="ISO language code.")
    lines: list[SubtitleLine] = Field(default_factory=list)


class Video(MediaAsset):
    """Final assembled video produced by Video Forge."""

    asset_type: AssetType = Field(default=AssetType.VIDEO, frozen=True)
    duration_seconds: float = Field(..., ge=0)
    resolution: str | None = Field(default=None, description="e.g. '1920x1080'.")
    fps: float | None = Field(default=None, gt=0)


class Thumbnail(MediaAsset):
    """Thumbnail image produced by Thumbnail Studio."""

    asset_type: AssetType = Field(default=AssetType.THUMBNAIL, frozen=True)
    variant_label: str | None = Field(default=None, description="A/B test label, if any.")


# ──────────────────────────────────────────────────────────────────────────
# Quality, publishing, analytics, memory, learning (AN-11..AN-16)
# ──────────────────────────────────────────────────────────────────────────

class QualityFinding(BaseAlphaModel):
    """A single issue or note raised during quality review."""

    category: str = Field(..., description="e.g. 'audio_sync', 'pacing', 'brand_safety'.")
    severity: Severity = Field(..., description="Severity of this finding.")
    description: str = Field(..., description="Description of the issue found.")
    suggested_fix: str | None = Field(default=None, description="Recommended remediation.")


class QualityReport(ImmutableAlphaModel):
    """Completed quality-review pass produced by Quality Sentinel."""

    quality_report_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    findings: list[QualityFinding] = Field(default_factory=list)
    overall_score: float = Field(..., ge=0.0, le=100.0, description="Composite quality score.")
    passed: bool = Field(..., description="Whether the content clears the quality bar.")
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)


class YouTubeUpload(BaseAlphaModel):
    """Publishing record specific to YouTube, produced by Publisher."""

    upload_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    video_id: str | None = Field(default=None, description="YouTube-assigned video ID.")
    title: str = Field(..., description="Published title.")
    description: str = Field(..., description="Published description.")
    tags: list[str] = Field(default_factory=list)
    visibility: str = Field(default="private", description="'public' | 'unlisted' | 'private'.")
    published_at: datetime | None = Field(default=None)


class TelegramMessage(BaseAlphaModel):
    """A notification or approval-request message sent via Telegram."""

    message_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID | None = Field(default=None, description="Related mission, if any.")
    chat_id: str = Field(..., description="Target chat identifier (from config).")
    text: str = Field(..., description="Message body.")
    requires_response: bool = Field(default=False)
    sent_at: datetime = Field(default_factory=datetime.utcnow)


class PublishRecord(BaseAlphaModel):
    """Generic cross-platform publish record used by Omni Republisher."""

    publish_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    platform: Platform = Field(..., description="Platform this record refers to.")
    external_id: str | None = Field(default=None, description="Platform-assigned content ID.")
    url: str | None = Field(default=None, description="Public URL of the published content.")
    published_at: datetime | None = Field(default=None)
    error: ErrorReport | None = Field(default=None, description="Populated on publish failure.")


class Analytics(BaseAlphaModel):
    """Performance metrics snapshot captured by Analytics Brain."""

    analytics_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID = Field(..., description="Owning mission.")
    platform: Platform = Field(..., description="Platform these metrics come from.")
    views: int = Field(default=0, ge=0)
    likes: int = Field(default=0, ge=0)
    comments: int = Field(default=0, ge=0)
    shares: int = Field(default=0, ge=0)
    watch_time_seconds: float = Field(default=0.0, ge=0)
    click_through_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    captured_at: datetime = Field(default_factory=datetime.utcnow)


class MemoryRecord(BaseAlphaModel):
    """A single stored memory entry managed by Memory Core."""

    memory_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID | None = Field(default=None, description="Related mission, if any.")
    namespace: str = Field(..., description="Logical grouping, e.g. 'topic_history'.")
    key: str = Field(..., description="Lookup key within the namespace.")
    value: str = Field(..., description="Stored value (serialized as needed by the caller).")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = Field(default=None, description="Optional TTL.")


class LearningRecord(ImmutableAlphaModel):
    """An immutable insight produced by the Evolution Engine."""

    learning_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID | None = Field(default=None, description="Mission that produced this insight.")
    insight: str = Field(..., description="What was learned.")
    evidence: list[str] = Field(default_factory=list, description="Supporting evidence points.")
    recommended_action: str | None = Field(default=None, description="Suggested platform change.")
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ──────────────────────────────────────────────────────────────────────────
# Configuration schema
# ──────────────────────────────────────────────────────────────────────────

class Configuration(BaseAlphaModel):
    """
    Typed view over runtime configuration values consumed by agents.
    This schema describes the SHAPE of config only — actual values are
    always loaded from the shared config module, never hardcoded here.
    """

    key: str = Field(..., description="Configuration key name.")
    value: str = Field(..., description="Configuration value, as a string (cast by consumer).")
    scope: str = Field(default="global", description="e.g. 'global', agent_id, or mission_id.")
    description: str | None = Field(default=None, description="What this config key controls.")


__all__ = [
    "SCHEMA_VERSION",
    "BaseAlphaModel",
    "ImmutableAlphaModel",
    "AgentID",
    "MissionStatus",
    "WorkflowStage",
    "ExecutionStatus",
    "Severity",
    "SourceReliability",
    "FactVerdict",
    "AssetType",
    "Platform",
    "ApprovalDecision",
    "SourceRef",
    "TimeRange",
    "ErrorReport",
    "APIResponse",
    "AgentResult",
    "Topic",
    "Mission",
    "MissionState",
    "DecisionRecord",
    "WorkflowEvent",
    "ApprovalRequest",
    "Research",
    "FactCheckClaim",
    "FactCheck",
    "ScriptSection",
    "Script",
    "SEOMetadata",
    "VisualShot",
    "VisualPlan",
    "MediaAsset",
    "ImageAsset",
    "VoiceAsset",
    "SubtitleLine",
    "Subtitle",
    "Video",
    "Thumbnail",
    "QualityFinding",
    "QualityReport",
    "YouTubeUpload",
    "TelegramMessage",
    "PublishRecord",
    "Analytics",
    "MemoryRecord",
    "LearningRecord",
    "Configuration",
]
