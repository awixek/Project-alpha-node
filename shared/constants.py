"""
shared/constants.py

Project Alpha Node — Shared Constants Layer
==============================================

Single source of truth for every constant used across the platform.

Design rules enforced in this file:
    * No business logic, no API calls, no logging, no processing.
    * Categorical/closed-set values -> str Enum.
    * Scalar thresholds/limits/timeouts -> Final-typed namespace classes.
    * Every agent imports from here instead of redefining its own copy.
    * Nothing here reads environment variables or external config; this
      module holds *defaults and identifiers*, not deployment-specific
      overrides (those belong in the shared config loader, which may use
      these values as its fallback defaults).

NOTE ON AgentID / MissionStatus / WorkflowStage / Platform:
    These are defined here as the canonical enums. `shared/schemas.py`
    imports all four from this module rather than redefining them, to
    avoid the two files drifting out of sync (this was previously the
    case for all four and caused several real bugs — see the Phase 2.1
    Foundation Review notes). `shared/constants.py` itself stays
    dependency-free (stdlib only), so `shared/schemas.py` depending on
    it does not violate schemas.py's own "stdlib + pydantic only" rule
    in any way that pulls in I/O, config, or logging.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

# ──────────────────────────────────────────────────────────────────────────
# 1. Project information
# ──────────────────────────────────────────────────────────────────────────

class Project:
    """Top-level identity/version constants for the platform."""

    NAME: Final[str] = "Project Alpha Node"
    VERSION: Final[str] = "1.0.0"
    ARCHITECTURE_VERSION: Final[str] = "1.0.0"
    REPOSITORY_VERSION: Final[str] = "1.0.0"


# ──────────────────────────────────────────────────────────────────────────
# 2. Agent IDs
# ──────────────────────────────────────────────────────────────────────────

class AgentID(str, Enum):
    """Canonical identifiers for every agent in the platform."""

    ORCHESTRATOR = "AN-17"
    MEMORY_CORE = "AN-16"
    RESEARCH_CORE = "AN-01"
    FACT_GUARDIAN = "AN-02"
    SCRIPT_FORGE = "AN-03"
    SEO_BRAIN = "AN-04"
    VISION_PLANNER = "AN-05"
    VISION_CREATOR = "AN-06"
    VOICE_CORE = "AN-07"
    SUBTITLE_ENGINE = "AN-08"
    VIDEO_FORGE = "AN-09"
    THUMBNAIL_STUDIO = "AN-10"
    QUALITY_SENTINEL = "AN-11"
    PUBLISHER = "AN-12"
    ANALYTICS_BRAIN = "AN-13"
    EVOLUTION_ENGINE = "AN-14"
    OMNI_REPUBLISHER = "AN-15"


# ──────────────────────────────────────────────────────────────────────────
# 3. Mission status (coarse-grained lifecycle)
# ──────────────────────────────────────────────────────────────────────────

class MissionStatus(str, Enum):
    """High-level mission lifecycle state, independent of pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"
    RETRYING = "retrying"
    ARCHIVED = "archived"


# ──────────────────────────────────────────────────────────────────────────
# 4. Workflow stages (granular pipeline position)
# ──────────────────────────────────────────────────────────────────────────

class WorkflowStage(str, Enum):
    """Position of a mission within the content-production pipeline."""

    MISSION_CREATED = "mission_created"
    RESEARCH = "research"
    FACT_CHECK = "fact_check"
    SCRIPT = "script"
    SEO = "seo"
    VISUAL_PLANNING = "visual_planning"
    IMAGE_GENERATION = "image_generation"
    VOICE_GENERATION = "voice_generation"
    SUBTITLE = "subtitle"
    VIDEO_EDITING = "video_editing"
    THUMBNAIL = "thumbnail"
    QUALITY_REVIEW = "quality_review"
    APPROVAL = "approval"
    PUBLISHING = "publishing"
    ANALYTICS = "analytics"
    MISSION_COMPLETE = "mission_complete"


# ──────────────────────────────────────────────────────────────────────────
# 5. Priority levels
# ──────────────────────────────────────────────────────────────────────────

class LogCategory(str, Enum):
    """
    Canonical logging categories, attached to individual log records by
    shared/logger.py (see that module's docstring for the full list).
    This enum is the single source of truth for category names — every
    module that logs (logger.py, retry.py, event_bus.py, api_router.py,
    validators.py, and future agents) imports it from here.
    """

    MISSION = "mission"
    AGENT = "agent"
    WORKFLOW = "workflow"
    API = "api"
    RETRY = "retry"
    PERFORMANCE = "performance"
    MEMORY = "memory"
    SYSTEM = "system"
    QUALITY = "quality"
    ERROR = "error"
    SECURITY = "security"


class Priority(str, Enum):
    """Mission priority levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PRIORITY_WEIGHT: Final[dict[Priority, int]] = {
    Priority.CRITICAL: 1,
    Priority.HIGH: 3,
    Priority.MEDIUM: 5,
    Priority.LOW: 8,
}
"""Lower weight = processed first. Mirrors Mission.priority (1=highest, 10=lowest) in schemas.py."""


# ──────────────────────────────────────────────────────────────────────────
# 6. Quality constants
# ──────────────────────────────────────────────────────────────────────────

class Quality:
    """Score thresholds used by Quality Sentinel and Evolution Engine."""

    MIN_SCORE: Final[float] = 90.0
    RECOMMENDED_SCORE: Final[float] = 95.0
    MAX_SCORE: Final[float] = 100.0


# ──────────────────────────────────────────────────────────────────────────
# 7. Retry / timeout constants
# ──────────────────────────────────────────────────────────────────────────

class Retry:
    """Default retry behavior for the shared Retry Engine."""

    MAX_ATTEMPTS: Final[int] = 3
    DELAY_SECONDS: Final[float] = 5.0
    BACKOFF_MULTIPLIER: Final[float] = 2.0
    TIMEOUT_SECONDS: Final[float] = 30.0
    UNHEALTHY_FAILURE_THRESHOLD: Final[int] = 3
    """Consecutive provider failures (shared.api_router) before a
    provider is marked unhealthy and sorted behind healthy ones."""


# ──────────────────────────────────────────────────────────────────────────
# 8. Log levels
# ──────────────────────────────────────────────────────────────────────────

class LogLevel(str, Enum):
    """Standard log severity levels used by the shared logger."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ──────────────────────────────────────────────────────────────────────────
# 9. Memory categories
# ──────────────────────────────────────────────────────────────────────────

class MemoryCategory(str, Enum):
    """Namespaces used when Memory Core stores a MemoryRecord."""

    MISSION = "mission"
    KNOWLEDGE = "knowledge"
    TOPIC = "topic"
    LEARNING = "learning"
    ANALYTICS = "analytics"
    ERROR = "error"
    DECISION = "decision"
    ARCHIVE = "archive"


# ──────────────────────────────────────────────────────────────────────────
# 10. Event names (Event Bus)
# ──────────────────────────────────────────────────────────────────────────

class EventName(str, Enum):
    """Canonical, dot-namespaced event names published on the Event Bus."""

    MISSION_CREATED = "mission.created"
    MISSION_STARTED = "mission.started"
    MISSION_COMPLETED = "mission.completed"
    MISSION_FAILED = "mission.failed"
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    API_FAILED = "api.failed"
    RETRY_STARTED = "retry.started"
    RETRY_COMPLETED = "retry.completed"
    APPROVAL_RECEIVED = "approval.received"
    VIDEO_UPLOADED = "video.uploaded"


# ──────────────────────────────────────────────────────────────────────────
# 11. Supported platforms
# ──────────────────────────────────────────────────────────────────────────

class Platform(str, Enum):
    """All platforms known to the platform, active or planned."""

    TELEGRAM = "telegram"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"  # future
    TIKTOK = "tiktok"  # future
    FACEBOOK = "facebook"  # future
    X = "x"  # future
    LINKEDIN = "linkedin"  # future
    WEBSITE = "website"  # future


ACTIVE_PLATFORMS: Final[frozenset[Platform]] = frozenset(
    {Platform.TELEGRAM, Platform.YOUTUBE}
)
"""Platforms with a live integration today. Others are defined for forward
compatibility but should be rejected by validation until implemented."""


# ──────────────────────────────────────────────────────────────────────────
# 12. Supported languages
# ──────────────────────────────────────────────────────────────────────────

class Language(str, Enum):
    """ISO-639-1 language codes supported for generated content."""

    ENGLISH = "en"
    HINDI = "hi"


ACTIVE_LANGUAGES: Final[frozenset[Language]] = frozenset(
    {Language.ENGLISH, Language.HINDI}
)
"""Languages fully supported today. Add future codes to Language above and
here, in one place, as multilingual support expands."""

DEFAULT_LANGUAGE: Final[Language] = Language.ENGLISH


# ──────────────────────────────────────────────────────────────────────────
# 13. File extensions
# ──────────────────────────────────────────────────────────────────────────

class FileExtension(str, Enum):
    """Canonical file extensions used across storage and outputs."""

    JSON = ".json"
    TXT = ".txt"
    MD = ".md"
    PNG = ".png"
    JPG = ".jpg"
    MP4 = ".mp4"
    MP3 = ".mp3"
    SRT = ".srt"
    LOG = ".log"


# ──────────────────────────────────────────────────────────────────────────
# 14. Configuration key namespaces
# ──────────────────────────────────────────────────────────────────────────

class ConfigKey(str, Enum):
    """
    Top-level namespaces within the shared configuration file/store.
    e.g. config.get(ConfigKey.YOUTUBE, "upload_visibility")
    """

    API = "api"
    LOGGING = "logging"
    STORAGE = "storage"
    MEMORY = "memory"
    QUALITY = "quality"
    TELEGRAM = "telegram"
    YOUTUBE = "youtube"


# ──────────────────────────────────────────────────────────────────────────
# 15. Default folder names
# ──────────────────────────────────────────────────────────────────────────

class FolderName(str, Enum):
    """Default top-level repository folder names."""

    AGENTS = "agents"
    LOGS = "logs"
    CONFIGS = "configs"
    MEMORY = "memory"
    STORAGE = "storage"
    OUTPUTS = "outputs"
    TESTS = "tests"
    DOCS = "docs"
    SHARED = "shared"


__all__ = [
    "Project",
    "AgentID",
    "MissionStatus",
    "WorkflowStage",
    "LogCategory",
    "Priority",
    "PRIORITY_WEIGHT",
    "Quality",
    "Retry",
    "LogLevel",
    "MemoryCategory",
    "EventName",
    "Platform",
    "ACTIVE_PLATFORMS",
    "Language",
    "ACTIVE_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "FileExtension",
    "ConfigKey",
    "FolderName",
]
