"""AN-05 Vision Planner contracts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agents.an03.models import ScriptDocument
from shared.constants import AgentID
from shared.schemas import SourceRef, VisualPlan, VisualShot


class VisualStyle(str, Enum):
    DOCUMENTARY = "documentary"
    HISTORICAL = "historical"
    CINEMATIC = "cinematic"
    REALISTIC = "realistic"
    HYPER_REALISTIC = "hyper_realistic"
    ILLUSTRATED = "illustrated"
    ANCIENT_PAINTING = "ancient_painting"
    THREE_D = "3d"
    ANIME = "anime"
    EDUCATIONAL = "educational"
    NEWS = "news"
    MINIMAL = "minimal"


class ShotType(str, Enum):
    CLOSE_UP = "close_up"
    MEDIUM = "medium"
    WIDE = "wide"
    AERIAL = "aerial"
    ESTABLISHING = "establishing"
    OVER_THE_SHOULDER = "over_the_shoulder"
    POV = "pov"
    STATIC = "static"
    TRACKING = "tracking"


class CameraAngle(str, Enum):
    EYE_LEVEL = "eye_level"
    LOW = "low"
    HIGH = "high"
    DUTCH = "dutch"
    TOP_DOWN = "top_down"


class CameraMovement(str, Enum):
    STATIC = "static"
    SLOW_PUSH = "slow_push"
    PULL_BACK = "pull_back"
    PAN = "pan"
    TILT = "tilt"
    TRACK = "track"
    ORBIT = "orbit"
    CRANE = "crane"


class TransitionType(str, Enum):
    CUT = "cut"
    FADE = "fade"
    DISSOLVE = "dissolve"
    MATCH_CUT = "match_cut"
    WIPE = "wipe"
    CROSSFADE = "crossfade"


class VisionPlanningConfig(BaseModel):
    """Validated runtime settings; values may come from AN-05 settings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    preferred_style: VisualStyle = VisualStyle.CINEMATIC
    maximum_scene_duration_seconds: float = Field(default=15.0, gt=0, le=300)
    target_total_duration_seconds: float | None = Field(default=None, gt=0, le=86_400)
    camera_preference: str = Field(default="balanced cinematic coverage", min_length=1, max_length=256)
    transition_style: TransitionType = TransitionType.CUT
    color_theme: str = Field(default="natural cinematic", min_length=1, max_length=128)
    realism_level: str = Field(default="high", min_length=1, max_length=64)
    prompt_verbosity: str = Field(default="detailed", min_length=1, max_length=64)
    language: str = Field(default="en", min_length=1, max_length=32)

    @classmethod
    def from_shared_config(cls) -> "VisionPlanningConfig":
        from shared.config import get_config

        settings = get_config().agents.get(AgentID.VISION_PLANNER.value)
        values = dict(settings.settings) if settings else {}
        return cls(**values)


class VisionPlanningRequest(BaseModel):
    """Input to AN-05; verified references are optional and never invented."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mission_id: UUID
    script: ScriptDocument
    seo_metadata: BaseModel | None = None
    verified_references: list[SourceRef] = Field(default_factory=list)
    config: VisionPlanningConfig | None = None
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)


class VisionScene(VisualShot):
    """Rich scene blueprint consumed by future visual-generation agents."""

    scene_number: int = Field(..., ge=1)
    narrative_goal: str = Field(..., min_length=1)
    visual_goal: str = Field(..., min_length=1)
    camera_type: ShotType
    camera_angle: CameraAngle
    camera_movement: CameraMovement
    subject: str = Field(..., min_length=1)
    characters: list[str] = Field(default_factory=list)
    character_description: str | None = None
    character_emotion: str | None = None
    character_pose: str | None = None
    costume_description: str | None = None
    environment: str = Field(..., min_length=1)
    historical_accuracy_notes: list[str] = Field(default_factory=list)
    architecture_style: str | None = None
    objects: list[str] = Field(default_factory=list)
    lighting: str = Field(..., min_length=1)
    time_of_day: str = Field(..., min_length=1)
    weather: str = Field(..., min_length=1)
    mood: str = Field(..., min_length=1)
    color_palette: str = Field(..., min_length=1)
    composition: str = Field(..., min_length=1)
    depth: str = Field(..., min_length=1)
    lens_suggestion: str = Field(..., min_length=1)
    animation_suggestion: str = Field(..., min_length=1)
    transition_type: TransitionType
    on_screen_text: str | None = None
    sound_suggestion: str = Field(..., min_length=1)
    music_mood: str = Field(..., min_length=1)
    image_prompt: str = Field(..., min_length=1)
    negative_prompt: str = Field(..., min_length=1)
    video_prompt: str = Field(..., min_length=1)
    asset_reuse_hint: str | None = None
    continuity_notes: list[str] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    uncertainty_notes: list[str] = Field(default_factory=list)
    b_roll_recommendations: list[str] = Field(default_factory=list)
    overlay_recommendations: list[str] = Field(default_factory=list)
    map_diagram_recommendations: list[str] = Field(default_factory=list)


class CharacterContinuity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_key: str
    appearance: str
    clothing: str
    hairstyle: str
    accessories: str
    emotional_progression: list[str] = Field(default_factory=list)
    scenes: list[int] = Field(default_factory=list)


class EnvironmentContinuity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_key: str
    description: str
    architecture: str
    geography: str
    period: str
    weather: str
    lighting: str
    props: list[str] = Field(default_factory=list)
    scenes: list[int] = Field(default_factory=list)


class ContinuityPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characters: list[CharacterContinuity] = Field(default_factory=list)
    environments: list[EnvironmentContinuity] = Field(default_factory=list)
    global_rules: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: list[int]
    timing: list[float]
    transitions: list[TransitionType]
    pacing: str
    emotional_rhythm: list[str]


class PromptPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_prompts: dict[int, str] = Field(default_factory=dict)
    video_prompts: dict[int, str] = Field(default_factory=dict)
    negative_prompts: dict[int, str] = Field(default_factory=dict)
    style: VisualStyle
    language: str


class AssetManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_key: str
    asset_type: str
    purpose: str
    scenes: list[int] = Field(default_factory=list)
    reusable: bool = True


class VisionPlan(VisualPlan):
    """AN-05 output extending the frozen shared VisualPlan contract."""

    storyboard: Storyboard
    scenes: list[VisionScene] = Field(default_factory=list)
    prompt_package: PromptPackage
    continuity_package: ContinuityPackage
    asset_manifest: list[AssetManifestItem] = Field(default_factory=list)
    estimated_runtime_seconds: float = Field(..., ge=0)
    production_metadata: dict[str, str] = Field(default_factory=dict)
    validation_issues: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    overall_confidence: float = Field(..., ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class VisionPlannerDefaults:
    """Immutable defaults used when no deployment override exists."""

    scene_duration_seconds: float = 10.0
    target_duration_seconds: float | None = None
    words_per_scene: int = 35
    default_environment: str = "unspecified environment; use only verified context"
    default_weather: str = "unspecified"
    default_time: str = "unspecified"
