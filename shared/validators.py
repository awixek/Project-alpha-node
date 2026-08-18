"""
shared/validators.py

Project Alpha Node — Centralized Validation Framework
==========================================================

Every validation check in Alpha Node — of configuration, schema data,
external API input, workflow transitions, missions, or files — should
go through this module instead of being reimplemented ad hoc inside an
agent.

Design rules enforced in this file:
    * Pure functions/classmethods only: input in, validated value back
      out, or a shared.exceptions.ValidationError subclass raised.
    * No logging, no I/O beyond what a check genuinely requires (e.g.
      FileValidator must stat() a file to check its size), no mutation
      of global state.
    * Never re-implement a check that already exists elsewhere:
        - Type/shape validation of schemas.py models -> pydantic itself
          (SchemaValidator just wraps the resulting error).
        - Business-required config combinations -> shared.config's own
          ConfigValidator (this module's ConfigValidator delegates to
          it, it does not duplicate its rules).
    * Validation logic must remain independent from business logic:
      these functions decide whether data is *well-formed*, never what
      an agent should *do* about it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ValidationError as PydanticValidationError

from shared.config import AlphaConfig
from shared.config import ConfigValidator as _ConfigBusinessRuleValidator
from shared.constants import ACTIVE_PLATFORMS, FileExtension, MissionStatus, Platform, WorkflowStage
from shared.exceptions import (
    FileValidationError,
    InputValidationError,
    MissionValidationError,
    SchemaValidationError,
    WorkflowValidationError,
)
from shared.schemas import Mission, MissionState

# ──────────────────────────────────────────────────────────────────────────
# Generic reusable validators
# ──────────────────────────────────────────────────────────────────────────

class GenericValidators:
    """Small, dependency-free checks reused across every other validator
    class in this module."""

    _UUID_PATTERN = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )

    @staticmethod
    def require_non_empty_string(
        value: str | None, *, field_name: str, max_length: int | None = None
    ) -> str:
        if value is None or not value.strip():
            raise InputValidationError(
                f"{field_name} must be a non-empty string.",
                context={"field": field_name},
            )
        cleaned = value.strip()
        if max_length is not None and len(cleaned) > max_length:
            raise InputValidationError(
                f"{field_name} exceeds maximum length of {max_length} characters.",
                context={"field": field_name, "length": len(cleaned), "max_length": max_length},
            )
        return cleaned

    @staticmethod
    def require_uuid(value: str | UUID, *, field_name: str) -> UUID:
        if isinstance(value, UUID):
            return value
        if not GenericValidators._UUID_PATTERN.match(str(value)):
            raise InputValidationError(
                f"{field_name} is not a valid UUID: {value!r}",
                context={"field": field_name},
            )
        return UUID(str(value))

    @staticmethod
    def require_url(value: str, *, field_name: str, allowed_schemes: tuple[str, ...] = ("http", "https")) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in allowed_schemes or not parsed.netloc:
            raise InputValidationError(
                f"{field_name} is not a valid URL with scheme in {allowed_schemes}: {value!r}",
                context={"field": field_name},
            )
        return value

    @staticmethod
    def require_positive_number(value: float, *, field_name: str) -> float:
        if value <= 0:
            raise InputValidationError(
                f"{field_name} must be a positive number (got {value}).",
                context={"field": field_name, "value": value},
            )
        return value

    @staticmethod
    def require_in_range(
        value: float, *, field_name: str, minimum: float, maximum: float
    ) -> float:
        if not (minimum <= value <= maximum):
            raise InputValidationError(
                f"{field_name} must be between {minimum} and {maximum} (got {value}).",
                context={"field": field_name, "value": value, "min": minimum, "max": maximum},
            )
        return value


# ──────────────────────────────────────────────────────────────────────────
# Schema validation
# ──────────────────────────────────────────────────────────────────────────

class SchemaValidator:
    """Validates raw data against any shared.schemas (or other pydantic)
    model, translating pydantic's ValidationError into Alpha Node's own
    SchemaValidationError so callers only ever catch one exception type."""

    @staticmethod
    def validate(model_cls: type[BaseModel], data: dict[str, Any]) -> BaseModel:
        try:
            return model_cls(**data)
        except PydanticValidationError as exc:
            raise SchemaValidationError(
                f"{model_cls.__name__} failed validation: {exc}",
                context={"model": model_cls.__name__, "error_count": exc.error_count()},
                cause=exc,
            ) from exc


# ──────────────────────────────────────────────────────────────────────────
# Configuration validation (delegates — does not duplicate config.py)
# ──────────────────────────────────────────────────────────────────────────

class ConfigValidator:
    """
    Thin delegation to shared.config.ConfigValidator, exposed here so
    callers that already import shared.validators for everything else
    don't also need a direct shared.config import just for this check.
    Business rules live in exactly one place: shared/config.py.
    """

    @staticmethod
    def validate_required(config: AlphaConfig) -> None:
        _ConfigBusinessRuleValidator.validate_required(config)


# ──────────────────────────────────────────────────────────────────────────
# API / external input validation
# ──────────────────────────────────────────────────────────────────────────

class APIInputValidator:
    """Sanitizes and validates raw input arriving from outside the
    platform (webhook payloads, user-submitted topics, etc.)."""

    _CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

    @staticmethod
    def sanitize_string(value: str, *, field_name: str, max_length: int = 10_000) -> str:
        if not isinstance(value, str):
            raise InputValidationError(
                f"{field_name} must be a string (got {type(value).__name__}).",
                context={"field": field_name},
            )
        cleaned = APIInputValidator._CONTROL_CHAR_PATTERN.sub("", value).strip()
        return GenericValidators.require_non_empty_string(cleaned, field_name=field_name, max_length=max_length)

    @staticmethod
    def validate_url(value: str, *, field_name: str = "url") -> str:
        return GenericValidators.require_url(value, field_name=field_name)

    @staticmethod
    def validate_uuid(value: str, *, field_name: str = "id") -> UUID:
        return GenericValidators.require_uuid(value, field_name=field_name)

    @staticmethod
    def validate_platform(value: str, *, field_name: str = "platform") -> Platform:
        try:
            platform = Platform(value)
        except ValueError as exc:
            raise InputValidationError(
                f"{field_name} is not a recognized platform: {value!r}",
                context={"field": field_name},
            ) from exc
        if platform not in ACTIVE_PLATFORMS:
            raise InputValidationError(
                f"{field_name} '{value}' is defined but not yet active.",
                context={"field": field_name, "platform": platform.value},
            )
        return platform


# ──────────────────────────────────────────────────────────────────────────
# Workflow validation
# ──────────────────────────────────────────────────────────────────────────

class WorkflowValidator:
    """
    Validates transitions between WorkflowStage values against the
    pipeline's allowed graph. Kept as an explicit adjacency map (rather
    than "any stage to any stage") so an agent accidentally skipping or
    reordering pipeline steps is caught immediately, not discovered
    downstream.
    """

    _ALLOWED_TRANSITIONS: dict[WorkflowStage, frozenset[WorkflowStage]] = {
        WorkflowStage.MISSION_CREATED: frozenset({WorkflowStage.RESEARCH}),
        WorkflowStage.RESEARCH: frozenset({WorkflowStage.FACT_CHECK}),
        WorkflowStage.FACT_CHECK: frozenset({WorkflowStage.SCRIPT, WorkflowStage.RESEARCH}),
        WorkflowStage.SCRIPT: frozenset({WorkflowStage.SEO}),
        WorkflowStage.SEO: frozenset({WorkflowStage.VISUAL_PLANNING}),
        WorkflowStage.VISUAL_PLANNING: frozenset({WorkflowStage.IMAGE_GENERATION}),
        WorkflowStage.IMAGE_GENERATION: frozenset({WorkflowStage.VOICE_GENERATION}),
        WorkflowStage.VOICE_GENERATION: frozenset({WorkflowStage.SUBTITLE}),
        WorkflowStage.SUBTITLE: frozenset({WorkflowStage.VIDEO_EDITING}),
        WorkflowStage.VIDEO_EDITING: frozenset({WorkflowStage.THUMBNAIL}),
        WorkflowStage.THUMBNAIL: frozenset({WorkflowStage.QUALITY_REVIEW}),
        WorkflowStage.QUALITY_REVIEW: frozenset({WorkflowStage.APPROVAL, WorkflowStage.SCRIPT}),
        WorkflowStage.APPROVAL: frozenset({WorkflowStage.PUBLISHING}),
        WorkflowStage.PUBLISHING: frozenset({WorkflowStage.ANALYTICS}),
        WorkflowStage.ANALYTICS: frozenset({WorkflowStage.MISSION_COMPLETE}),
        WorkflowStage.MISSION_COMPLETE: frozenset(),
    }

    @classmethod
    def validate_transition(cls, current: WorkflowStage, target: WorkflowStage) -> None:
        allowed = cls._ALLOWED_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise WorkflowValidationError(
                f"Illegal workflow transition: {current.value} -> {target.value}.",
                context={
                    "current_stage": current.value,
                    "target_stage": target.value,
                    "allowed_next_stages": sorted(s.value for s in allowed),
                },
            )

    @classmethod
    def is_terminal(cls, stage: WorkflowStage) -> bool:
        return len(cls._ALLOWED_TRANSITIONS.get(stage, frozenset())) == 0


# ──────────────────────────────────────────────────────────────────────────
# Mission validation
# ──────────────────────────────────────────────────────────────────────────

class MissionValidator:
    """Business-rule checks for Mission and MissionState beyond what
    pydantic's type validation already guarantees."""

    # MissionStatus values that pin the mission to one exact WorkflowStage.
    # Any other stage paired with one of these statuses is an invalid state.
    _EXACT_STAGE_FOR_STATUS: dict[MissionStatus, WorkflowStage] = {
        MissionStatus.PENDING: WorkflowStage.MISSION_CREATED,
        MissionStatus.WAITING_APPROVAL: WorkflowStage.APPROVAL,
        MissionStatus.COMPLETED: WorkflowStage.MISSION_COMPLETE,
        MissionStatus.ARCHIVED: WorkflowStage.MISSION_COMPLETE,
    }

    # MissionStatus values that imply the pipeline has already started —
    # these can never legitimately be paired with stage MISSION_CREATED.
    _STARTED_STATUSES: frozenset[MissionStatus] = frozenset(
        {MissionStatus.RUNNING, MissionStatus.RETRYING, MissionStatus.PAUSED}
    )

    @classmethod
    def validate_status_stage_combination(
        cls, status: MissionStatus, stage: WorkflowStage, *, mission_id: UUID | None = None
    ) -> None:
        """
        Checks that a MissionStatus (coarse lifecycle state) and a
        WorkflowStage (granular pipeline position) are a valid
        combination. The two are intentionally separate fields on
        MissionState (see shared.schemas), so nothing at the type level
        stops a caller from pairing e.g. status=COMPLETED with
        stage=RESEARCH — this is the check that catches that.

        Raises:
            MissionValidationError: if the combination is invalid.
        """
        expected_stage = cls._EXACT_STAGE_FOR_STATUS.get(status)
        if expected_stage is not None and stage is not expected_stage:
            raise MissionValidationError(
                f"status={status.value!r} requires stage={expected_stage.value!r}, "
                f"got stage={stage.value!r}.",
                mission_id=mission_id,
                context={"status": status.value, "stage": stage.value, "expected_stage": expected_stage.value},
            )

        if status in cls._STARTED_STATUSES and stage is WorkflowStage.MISSION_CREATED:
            raise MissionValidationError(
                f"status={status.value!r} implies the pipeline has started, "
                f"but stage is still {WorkflowStage.MISSION_CREATED.value!r}.",
                mission_id=mission_id,
                context={"status": status.value, "stage": stage.value},
            )

    @staticmethod
    def validate_mission(mission: Mission) -> Mission:
        GenericValidators.require_non_empty_string(mission.topic.title, field_name="topic.title")

        if not mission.target_platforms:
            raise MissionValidationError(
                "Mission must specify at least one target platform.",
                mission_id=mission.mission_id,
            )

        inactive = [p for p in mission.target_platforms if p not in ACTIVE_PLATFORMS]
        if inactive:
            raise MissionValidationError(
                f"Mission targets platform(s) not yet active: {[p.value for p in inactive]}",
                mission_id=mission.mission_id,
                context={"inactive_platforms": [p.value for p in inactive]},
            )

        return mission

    @classmethod
    def validate_mission_state(cls, state: MissionState) -> MissionState:
        """
        Validates a MissionState: artifact references, plus the
        status/stage relationship now that schemas.MissionState carries
        `status` (MissionStatus) and `stage` (WorkflowStage) as two
        separate fields (Phase 2.1 Foundation Review — previously a
        single conflated field, which is why this check used to be
        deferred).
        """
        for artifact_name, artifact_id in state.artifact_ids.items():
            GenericValidators.require_uuid(artifact_id, field_name=f"artifact_ids[{artifact_name}]")

        cls.validate_status_stage_combination(state.status, state.stage, mission_id=state.mission_id)

        return state


# ──────────────────────────────────────────────────────────────────────────
# File validation
# ──────────────────────────────────────────────────────────────────────────

class FileValidator:
    """Validates files before they're read, written, or handed to another
    agent — extension, path safety, and size."""

    @staticmethod
    def validate_extension(path: Path, *, allowed: Iterable[FileExtension]) -> Path:
        allowed_values = {ext.value for ext in allowed}
        if path.suffix.lower() not in allowed_values:
            raise FileValidationError(
                f"File extension '{path.suffix}' not permitted (allowed: {sorted(allowed_values)}).",
                context={"path": str(path), "suffix": path.suffix},
            )
        return path

    @staticmethod
    def validate_safe_path(base_dir: Path, candidate: Path) -> Path:
        """Resolves `candidate` and confirms it stays within `base_dir`,
        rejecting path-traversal attempts (e.g. '../../etc/passwd')."""
        resolved_base = base_dir.resolve()
        resolved_candidate = (base_dir / candidate).resolve()
        try:
            resolved_candidate.relative_to(resolved_base)
        except ValueError as exc:
            raise FileValidationError(
                f"Path escapes its allowed base directory: {candidate}",
                context={"base_dir": str(resolved_base), "candidate": str(resolved_candidate)},
            ) from exc
        return resolved_candidate

    @staticmethod
    def validate_max_size(path: Path, *, max_bytes: int) -> Path:
        if not path.exists():
            raise FileValidationError(
                f"File does not exist: {path}",
                context={"path": str(path)},
            )
        size = path.stat().st_size
        if size > max_bytes:
            raise FileValidationError(
                f"File exceeds maximum allowed size of {max_bytes} bytes (actual: {size}).",
                context={"path": str(path), "size_bytes": size, "max_bytes": max_bytes},
            )
        return path


__all__ = [
    "GenericValidators",
    "SchemaValidator",
    "ConfigValidator",
    "APIInputValidator",
    "WorkflowValidator",
    "MissionValidator",
    "FileValidator",
]
