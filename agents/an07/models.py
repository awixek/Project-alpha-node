from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.constants import AgentID
from shared.schemas import Script


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    voice: str = Field(default="default", min_length=1, max_length=128)
    language: str = Field(default="en", min_length=2, max_length=16)
    gender: str | None = Field(default=None, max_length=32)
    style: str = Field(default="documentary", min_length=1, max_length=64)
    speaking_rate: float = Field(default=1.0, gt=0.25, le=3.0)
    pitch: float = Field(default=0.0, ge=-20.0, le=20.0)
    volume: float = Field(default=1.0, gt=0.0, le=2.0)
    emotion: str = Field(default="neutral", min_length=1, max_length=64)


class VoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission_id: UUID
    script: Script
    vision_plan: BaseModel | None = None
    asset_metadata: BaseModel | None = None
    profile: VoiceProfile = Field(default_factory=VoiceProfile)
    pronunciation_dictionary: dict[str, str] = Field(default_factory=dict)
    max_retries: int = Field(default=3, ge=0, le=20)
    timeout: float = Field(default=120.0, gt=0, le=86400)
    preferred_provider: str | None = None
    fallback_provider: str | None = None
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class PronunciationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original: str
    pronunciation: str
    occurrences: int = Field(default=1, ge=1)


class VoiceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    segment_id: str = Field(..., min_length=1)
    section_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=0)
    text: str = Field(..., min_length=1)
    processed_text: str = Field(..., min_length=1)
    start_time: float = Field(..., ge=0)
    estimated_end_time: float = Field(..., ge=0)
    duration: float = Field(..., gt=0)
    narrator: str = Field(default="default", min_length=1)
    language: str = Field(..., min_length=2)
    emotion: str = Field(default="neutral", min_length=1)
    emphasis: list[str] = Field(default_factory=list)
    speech_rate: float = Field(default=1.0, gt=0)
    provider: str | None = None
    audio_uri: str | None = None
    mime_type: str | None = None
    pronunciation: list[PronunciationEntry] = Field(default_factory=list)
    generation_metadata: dict[str, str] = Field(default_factory=dict)


class VoiceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    voice: str
    style: str
    total_duration: float = Field(default=0.0, ge=0)
    segment_count: int = Field(default=0, ge=0)
    word_count: int = Field(default=0, ge=0)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    requests: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)


class GenerationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments_requested: int = Field(default=0, ge=0)
    segments_generated: int = Field(default=0, ge=0)
    segments_failed: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    generation_time_ms: float = Field(default=0.0, ge=0)


class VoiceQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(..., ge=0, le=100)
    findings: list[str] = Field(default_factory=list)
    checked_segments: int = Field(default=0, ge=0)


class SynchronizationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[VoiceSegment] = Field(default_factory=list)
    total_duration: float = Field(default=0.0, ge=0)
    timebase: str = "seconds"


class VoicePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: UUID
    narration_segments: list[VoiceSegment] = Field(default_factory=list)
    narration_uri: str | None = None
    metadata: VoiceMetadata
    pronunciation_metadata: list[PronunciationEntry] = Field(default_factory=list)
    synchronization: SynchronizationMetadata
    provider_statistics: list[ProviderHealth] = Field(default_factory=list)
    quality_report: VoiceQualityReport
    generation_metrics: GenerationMetrics
    production_metadata: dict[str, str] = Field(default_factory=dict)


class VoiceCoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    preferred_provider: str | None = None
    fallback_provider: str | None = None
    language: str = Field(default="en", min_length=2, max_length=16)
    voice: str = Field(default="default", min_length=1, max_length=128)
    gender: str | None = Field(default=None, max_length=32)
    style: str = Field(default="documentary", min_length=1, max_length=64)
    speaking_rate: float = Field(default=1.0, gt=0.25, le=3.0)
    pause_duration: float = Field(default=0.25, ge=0, le=10)
    pitch: float = Field(default=0.0, ge=-20.0, le=20.0)
    volume: float = Field(default=1.0, gt=0, le=2.0)
    emotion: str = Field(default="neutral", min_length=1, max_length=64)
    pronunciation_dictionary: dict[str, str] = Field(default_factory=dict)
    max_retries: int = Field(default=3, ge=0, le=20)
    timeout: float = Field(default=120.0, gt=0, le=86400)
    output_format: str = Field(default="audio/mpeg", min_length=3, max_length=64)
    minimum_quality_score: float = Field(default=70.0, ge=0, le=100)
    words_per_minute: float = Field(default=150.0, gt=40, le=300)


class VoiceProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: UUID
    segment_id: str
    text: str
    language: str
    profile: VoiceProfile
    pronunciation: list[PronunciationEntry] = Field(default_factory=list)
    timeout: float = Field(default=120.0, gt=0)
    output_format: str = "audio/mpeg"


class VoiceProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    audio_uri: str = Field(..., min_length=1)
    duration_seconds: float = Field(..., gt=0)
    mime_type: str = "audio/mpeg"
    content_bytes: bytes | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


_AGENT_ID: AgentID = AgentID.VOICE_CORE
