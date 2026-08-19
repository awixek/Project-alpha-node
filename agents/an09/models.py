from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an07.models import VoicePackage
from agents.an08.models import SubtitlePackage
from shared.schemas import BaseAlphaModel


class Orientation(str, Enum):
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"
    SQUARE = "square"


class TransitionStyle(str, Enum):
    CUT = "cut"
    FADE = "fade"
    DISSOLVE = "dissolve"
    CROSSFADE = "crossfade"
    SLIDE = "slide"


class RenderStatus(str, Enum):
    PENDING = "pending"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    RESUMABLE = "resumable"


class VideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mission_id: UUID
    vision_plan: VisionPlan
    asset_package: AssetPackage
    voice_package: VoicePackage
    subtitle_package: SubtitlePackage
    script: BaseModel | None = None
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class RenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    render_quality: str = Field(default="high", min_length=1, max_length=64)
    fps: float = Field(default=30.0, gt=1, le=240)
    codec: str = Field(default="h264", min_length=1, max_length=64)
    resolution: str = Field(default="1920x1080", min_length=3, max_length=32)
    bitrate: str = Field(default="8M", min_length=1, max_length=32)
    export_format: str = Field(default="mp4", min_length=2, max_length=16)
    aspect_ratio: str = Field(default="16:9", min_length=3, max_length=16)
    orientation: Orientation = Orientation.LANDSCAPE
    transition_style: TransitionStyle = TransitionStyle.CUT
    animation_intensity: float = Field(default=0.5, ge=0, le=1)
    subtitle_style: str = Field(default="default", min_length=1, max_length=64)
    timeout: float = Field(default=3600.0, gt=0, le=86400)
    incremental_rendering: bool = True
    resume_enabled: bool = True
    cache_enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=20)

    @classmethod
    def from_shared_config(cls) -> "RenderSettings":
        from shared.config import get_config
        from shared.constants import AgentID

        settings = get_config().agents.get(AgentID.VIDEO_FORGE.value)
        return cls(**dict(settings.settings)) if settings else cls()


class TimelineScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: int = Field(..., ge=1)
    order: int = Field(..., ge=0)
    start_time: float = Field(..., ge=0)
    end_time: float = Field(..., ge=0)
    duration: float = Field(..., gt=0)
    asset_ids: list[UUID] = Field(default_factory=list)
    narration_segment_ids: list[str] = Field(default_factory=list)
    subtitle_segment_ids: list[str] = Field(default_factory=list)
    transition_in: str = "cut"
    transition_out: str = "cut"
    motion_effects: list[str] = Field(default_factory=list)
    overlays: list[str] = Field(default_factory=list)
    background_music_placeholder: str | None = None
    sound_effect_placeholders: list[str] = Field(default_factory=list)


class Timeline(BaseAlphaModel):
    mission_id: UUID
    scenes: list[TimelineScene] = Field(default_factory=list)
    total_runtime: float = Field(default=0.0, ge=0)
    timebase: str = "seconds"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_scene: int = Field(..., ge=1)
    to_scene: int = Field(..., ge=1)
    transition_type: str = Field(..., min_length=1)
    duration: float = Field(default=0.0, ge=0)


class RenderJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    status: RenderStatus = RenderStatus.PENDING
    completed_scene_ids: list[int] = Field(default_factory=list)
    remaining_scene_ids: list[int] = Field(default_factory=list)
    render_uri: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    retry_count: int = Field(default=0, ge=0)
    error: str | None = None


class RenderMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenes_requested: int = Field(default=0, ge=0)
    scenes_composed: int = Field(default=0, ge=0)
    scenes_failed: int = Field(default=0, ge=0)
    rendered_frames: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    render_time_ms: float = Field(default=0.0, ge=0)
    cache_hits: int = Field(default=0, ge=0)


class SynchronizationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(..., ge=0, le=100)
    findings: list[str] = Field(default_factory=list)
    narration_drift_seconds: float = Field(default=0.0, ge=0)
    subtitle_drift_seconds: float = Field(default=0.0, ge=0)


class VideoQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(..., ge=0, le=100)
    findings: list[str] = Field(default_factory=list)
    missing_assets: int = Field(default=0, ge=0)
    invalid_timing: int = Field(default=0, ge=0)
    duplicate_clips: int = Field(default=0, ge=0)


class ExportMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str
    uri: str
    resolution: str
    fps: float
    codec: str
    bitrate: str
    duration_seconds: float = Field(..., ge=0)
    size_bytes: int | None = Field(default=None, ge=0)


class VideoPackage(BaseAlphaModel):
    mission_id: UUID
    video_uri: str | None = None
    timeline: Timeline
    render_job: RenderJob
    render_metrics: RenderMetrics
    export_metadata: ExportMetadata | None = None
    quality_report: VideoQualityReport
    synchronization_report: SynchronizationReport
    asset_usage_report: dict[str, Any] = Field(default_factory=dict)
    production_metadata: dict[str, str] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VideoProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: UUID
    timeline: Timeline
    render_settings: RenderSettings
    scene_asset_uris: dict[int, list[str]] = Field(default_factory=dict)
    narration_uri: str | None = None
    subtitle_exports: dict[str, str] = Field(default_factory=dict)
    script_metadata: dict[str, str] = Field(default_factory=dict)
    completed_scene_ids: list[int] = Field(default_factory=list)


class VideoProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    video_uri: str = Field(..., min_length=1)
    duration_seconds: float = Field(..., ge=0)
    format: str
    resolution: str
    fps: float = Field(..., gt=0)
    codec: str
    bitrate: str
    size_bytes: int | None = Field(default=None, ge=0)
    completed_scene_ids: list[int] = Field(default_factory=list)
    rendered_frames: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    metadata: dict[str, str] = Field(default_factory=dict)


__all__ = [
    "Orientation", "TransitionStyle", "RenderStatus", "VideoRequest", "RenderSettings",
    "TimelineScene", "Timeline", "Transition", "RenderJob", "RenderMetrics",
    "SynchronizationReport", "VideoQualityReport", "ExportMetadata", "VideoPackage",
    "VideoProviderRequest", "VideoProviderResponse",
]
