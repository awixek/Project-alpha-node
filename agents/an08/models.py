from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field, field_validator
from agents.an07.models import VoicePackage

class SubtitleStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    font_recommendation: str = "sans-serif"
    font_size: int = Field(42, ge=8, le=200)
    text_color: str = "#FFFFFF"
    outline: str = "2px"
    shadow: str = "1px"
    alignment: str = "center"
    screen_position: str = "bottom"
    animation_hint: str | None = None
    emphasis_style: str = "bold"

class SubtitleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mission_id: UUID
    voice_package: VoicePackage
    script: BaseModel | None = None
    vision_plan: BaseModel | None = None
    language: str = Field("en", min_length=2, max_length=16)
    translated_text: dict[str, str] = Field(default_factory=dict)
    bilingual_mode: bool = False
    subtitle_format: str = "srt"
    formats: list[str] = Field(default_factory=list)
    max_characters_per_line: int = Field(42, ge=10, le=200)
    max_lines: int = Field(2, ge=1, le=4)
    reading_speed: float = Field(17.0, gt=1, le=40)
    punctuation_rules: dict[str, str] = Field(default_factory=dict)
    style_profile: SubtitleStyle = Field(default_factory=SubtitleStyle)
    timing_offset: float = Field(0.0, ge=-60, le=60)
    timing_tolerance: float = Field(0.15, ge=0, le=10)
    speaker_labels: bool = True
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subtitle_format")
    @classmethod
    def normalize_format(cls, value: str) -> str:
        value = value.lower().lstrip(".")
        if value not in {"srt", "vtt", "ass", "ttml", "json"}:
            raise ValueError("Unsupported subtitle format.")
        return value

class SubtitleSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subtitle_id: str = Field(default_factory=lambda: str(uuid4()))
    scene_id: str = ""
    sequence: int = Field(..., ge=0)
    start_time: float = Field(..., ge=0)
    end_time: float = Field(..., ge=0)
    duration: float = Field(..., gt=0)
    language: str = Field(..., min_length=2)
    speaker: str | None = None
    text: str = Field(..., min_length=1)
    confidence: float = Field(1.0, ge=0, le=1)
    synchronization_score: float = Field(1.0, ge=0, le=1)
    line_count: int = Field(1, ge=1)
    word_count: int = Field(1, ge=1)
    emphasis: list[str] = Field(default_factory=list)

class SubtitleTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track_id: UUID = Field(default_factory=uuid4)
    language: str
    label: str
    segments: list[SubtitleSegment] = Field(default_factory=list)
    format: str = "srt"

class SubtitleMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_id: UUID
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    language: str
    track_count: int = 0
    segment_count: int = 0
    total_duration: float = 0.0
    formats: list[str] = Field(default_factory=list)

class SynchronizationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_segments: int = 0
    overlaps: int = 0
    invalid_timings: int = 0
    average_score: float = 0.0
    average_drift_seconds: float = 0.0
    reading_speed_violations: int = 0

class SubtitleQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    score: float = Field(..., ge=0, le=100)
    findings: list[str] = Field(default_factory=list)
    metrics: SynchronizationMetrics

class SubtitlePackage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_id: UUID
    subtitle_tracks: list[SubtitleTrack] = Field(default_factory=list)
    synchronization_metadata: SynchronizationMetrics
    exported_formats: dict[str, str] = Field(default_factory=dict)
    quality_report: SubtitleQualityReport
    validation_report: list[str] = Field(default_factory=list)
    formatting_metadata: SubtitleStyle = Field(default_factory=SubtitleStyle)
    generation_statistics: dict[str, int | float | str] = Field(default_factory=dict)
    metadata: SubtitleMetadata
