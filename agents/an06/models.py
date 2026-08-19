from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field
from agents.an05.models import VisionPlan
from shared.schemas import AssetType, BaseAlphaModel, MediaAsset

class GenerationKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
class GenerationStatus(str, Enum):
    PENDING = "pending"
    GENERATED = "generated"
    RETRYING = "retrying"
    FAILED = "failed"
    SKIPPED = "skipped"
class QualityStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"

class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mission_id: UUID
    scene_id: int = Field(..., ge=1)
    kind: GenerationKind
    prompt: str = Field(..., min_length=1, max_length=20000)
    negative_prompt: str | None = Field(default=None, max_length=20000)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    resolution: str = Field(default="1920x1080", min_length=3, max_length=32)
    aspect_ratio: str = Field(default="16:9", min_length=3, max_length=16)
    quality_level: str = Field(default="high", min_length=1, max_length=64)
    realism_level: str = Field(default="high", min_length=1, max_length=64)
    output_format: str = Field(default="png", min_length=2, max_length=16)
    language: str = Field(default="en", min_length=2, max_length=16)
    reference_asset_ids: list[UUID] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

class ProviderAssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage_path: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1, max_length=128)
    asset_type: AssetType
    width_px: int | None = Field(default=None, gt=0)
    height_px: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    checksum: str | None = Field(default=None, min_length=1)
    content_bytes: bytes | None = None
    mime_type: str | None = None
    provider_metadata: dict[str, str] = Field(default_factory=dict)

class GeneratedAsset(MediaAsset):
    scene_id: int = Field(..., ge=1)
    generation_kind: GenerationKind
    generation_status: GenerationStatus = GenerationStatus.GENERATED
    asset_version: int = Field(default=1, ge=1)
    prompt_version: str = Field(default="1", min_length=1, max_length=32)
    retry_count: int = Field(default=0, ge=0)
    generation_time_ms: float = Field(default=0.0, ge=0)
    width_px: int | None = Field(default=None, gt=0)
    height_px: int | None = Field(default=None, gt=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    mime_type: str | None = None
    source_checksum: str | None = None
    reusable: bool = True
    reference_asset_ids: list[UUID] = Field(default_factory=list)
    quality_status: QualityStatus = QualityStatus.PASSED
    quality_findings: list[str] = Field(default_factory=list)

class AssetManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: UUID
    scene_id: int = Field(..., ge=1)
    asset_type: AssetType
    storage_path: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    reusable: bool = True
    checksum: str | None = None
    version: int = Field(default=1, ge=1)
    purpose: str = Field(default="scene asset", min_length=1)

class AssetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[AssetManifestItem] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    requests: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    last_error: str | None = None
    healthy: bool = True

class GenerationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenes_requested: int = Field(default=0, ge=0)
    scenes_completed: int = Field(default=0, ge=0)
    scenes_failed: int = Field(default=0, ge=0)
    assets_generated: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    total_generation_time_ms: float = Field(default=0.0, ge=0)

class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    score: float = Field(..., ge=0.0, le=100.0)
    findings: list[str] = Field(default_factory=list)
    checked_assets: int = Field(default=0, ge=0)

class ContinuityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passed: bool
    findings: list[str] = Field(default_factory=list)
    checked_scenes: int = Field(default=0, ge=0)

class OptimizationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applied: bool
    findings: list[str] = Field(default_factory=list)
    normalized_assets: int = Field(default=0, ge=0)

class AssetPackage(BaseAlphaModel):
    mission_id: UUID
    assets: list[GeneratedAsset] = Field(default_factory=list)
    asset_manifest: AssetManifest
    generation_metrics: GenerationMetrics
    provider_statistics: list[ProviderHealth] = Field(default_factory=list)
    quality_report: QualityReport
    continuity_report: ContinuityReport
    optimization_report: OptimizationReport
    production_metadata: dict[str, str] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class VisionCreatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    mission_id: UUID
    vision_plan: VisionPlan
    script: BaseModel | None = None
    seo_metadata: BaseModel | None = None
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)

class VisionCreatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    preferred_provider: str | None = None
    fallback_provider: str | None = None
    image_resolution: str = Field(default="1920x1080", min_length=3, max_length=32)
    aspect_ratio: str = Field(default="16:9", min_length=3, max_length=16)
    quality_level: str = Field(default="high", min_length=1, max_length=64)
    realism_level: str = Field(default="high", min_length=1, max_length=64)
    max_retries: int = Field(default=3, ge=0, le=20)
    timeout: float = Field(default=120.0, gt=0, le=86400)
    optimization_level: str = Field(default="standard", min_length=1, max_length=64)
    output_format: str = Field(default="png", min_length=2, max_length=16)
    language: str = Field(default="en", min_length=2, max_length=16)
    required_assets_per_scene: int = Field(default=1, ge=1, le=10)
    generate_video_assets: bool = False
    minimum_quality_score: float = Field(default=70.0, ge=0.0, le=100.0)
