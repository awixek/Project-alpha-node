"""
shared/exceptions.py

Project Alpha Node — Centralized Exception Hierarchy
========================================================

Every custom exception raised anywhere in Alpha Node inherits from
AlphaBaseException. This module defines that hierarchy only — no
business logic, no I/O, no logging (raising is not logging; the catcher
decides whether/how to log via shared.logger).

Design rules enforced in this file:
    * One shallow hierarchy: a handful of domain roots, not one class
      per micro-case. Fine-grained distinction happens via the `code`
      and `context` fields, not via class explosion.
    * Every exception carries enough structured metadata to build a
      schemas.ErrorReport without the catcher needing to know the
      concrete subclass.
    * `retryable` defaults sensibly per domain (e.g. a validation error
      is never retryable; a provider timeout usually is) but can always
      be overridden per-instance.

NOTE ON shared/config.py:
    shared/config.py's own ConfigError hierarchy now inherits from
    ConfigurationError below, so every configuration failure is also an
    AlphaBaseException (unified in the Phase 2.1 Foundation Review).
    config.py does not import this module's other exceptions and does
    not need to — it only reaches up to ConfigurationError, keeping the
    coupling minimal and one-directional (config.py depends on
    exceptions.py, never the reverse).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from shared.constants import AgentID
from shared.schemas import ErrorReport, Severity

# ──────────────────────────────────────────────────────────────────────────
# Root exception
# ──────────────────────────────────────────────────────────────────────────

class AlphaBaseException(Exception):
    """
    Base class for every custom exception in Project Alpha Node.

    Args:
        message: human-readable description, safe to log (never put
            secret values in here — callers are responsible for that,
            same convention as shared.config's ConfigError hierarchy).
        code: short machine-readable identifier, e.g. "provider_timeout".
        severity: how serious this is, from schemas.Severity.
        retryable: whether the failed operation may reasonably be retried.
        agent_id: the agent that raised this, if applicable.
        mission_id: the mission being processed, if applicable.
        context: additional structured key/value context for debugging.
            Values must be safe to log — never place secrets here.
        cause: the original exception being wrapped, if any (also set
            automatically when raised with `from`).
    """

    default_code: str = "alpha_error"
    default_severity: Severity = Severity.ERROR
    default_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        severity: Severity | None = None,
        retryable: bool | None = None,
        agent_id: AgentID | None = None,
        mission_id: str | UUID | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.severity = severity or self.default_severity
        self.retryable = self.default_retryable if retryable is None else retryable
        self.agent_id = agent_id
        self.mission_id = str(mission_id) if mission_id is not None else None
        self.context: dict[str, Any] = dict(context) if context else {}
        self.occurred_at = datetime.now(timezone.utc)
        if cause is not None:
            self.__cause__ = cause

    def to_error_report(self) -> ErrorReport:
        """Converts this exception into a schemas.ErrorReport for logging
        or inclusion in an AgentResult."""
        return ErrorReport(
            agent_id=self.agent_id or AgentID.ORCHESTRATOR,
            severity=self.severity,
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            occurred_at=self.occurred_at,
            context={k: str(v) for k, v in self.context.items()},
        )

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


# ──────────────────────────────────────────────────────────────────────────
# Domain roots
# ──────────────────────────────────────────────────────────────────────────

class ConfigurationError(AlphaBaseException):
    """Configuration could not be loaded, was missing, or was invalid.

    shared/config.py's own ConfigError (and every subclass of it) now
    inherits from this class, so any config failure can be caught
    either as the specific config.py subclass or generically as
    ConfigurationError / AlphaBaseException.
    """

    default_code = "configuration_error"
    default_retryable = False


class ValidationError(AlphaBaseException):
    """Base class for every validation failure raised by shared/validators.py."""

    default_code = "validation_error"
    default_retryable = False


class SchemaValidationError(ValidationError):
    """Data failed to validate against a shared.schemas model."""

    default_code = "schema_validation_error"


class WorkflowValidationError(ValidationError):
    """An invalid or disallowed workflow-stage transition was attempted."""

    default_code = "workflow_validation_error"


class MissionValidationError(ValidationError):
    """A Mission or MissionState failed a business-rule check."""

    default_code = "mission_validation_error"


class FileValidationError(ValidationError):
    """A file failed extension, path-safety, or size validation."""

    default_code = "file_validation_error"


class InputValidationError(ValidationError):
    """Raw external/API input failed sanitization or format validation."""

    default_code = "input_validation_error"


class RetryExhaustedError(AlphaBaseException):
    """A retried operation failed on every attempt permitted by its policy."""

    default_code = "retry_exhausted"
    default_severity = Severity.ERROR
    default_retryable = False  # the retry layer itself already gave up


class EventBusError(AlphaBaseException):
    """The event bus could not publish, subscribe, or dispatch an event."""

    default_code = "event_bus_error"
    default_retryable = False


class APIProviderError(AlphaBaseException):
    """Base class for AI-provider call failures raised by shared/api_router.py."""

    default_code = "api_provider_error"
    default_retryable = True


class ProviderUnavailableError(APIProviderError):
    """A specific provider is unhealthy or unreachable."""

    default_code = "provider_unavailable"


class AllProvidersFailedError(APIProviderError):
    """Every registered provider failed for a given request."""

    default_code = "all_providers_failed"
    default_retryable = False  # nothing left to fall back to


class AgentExecutionError(AlphaBaseException):
    """An agent's core execution logic failed in a way not covered by a
    more specific exception above."""

    default_code = "agent_execution_error"
    default_retryable = True


class MissionError(AlphaBaseException):
    """A mission-level failure not specific to any single agent."""

    default_code = "mission_error"
    default_retryable = False


class QualityGateError(AlphaBaseException):
    """Content failed to clear a required quality or fact-check threshold."""

    default_code = "quality_gate_error"
    default_retryable = False


class SecurityError(AlphaBaseException):
    """A security-relevant rejection: unsafe input, path traversal attempt,
    unauthorized action, etc."""

    default_code = "security_error"
    default_severity = Severity.CRITICAL
    default_retryable = False


__all__ = [
    "AlphaBaseException",
    "ConfigurationError",
    "ValidationError",
    "SchemaValidationError",
    "WorkflowValidationError",
    "MissionValidationError",
    "FileValidationError",
    "InputValidationError",
    "RetryExhaustedError",
    "EventBusError",
    "APIProviderError",
    "ProviderUnavailableError",
    "AllProvidersFailedError",
    "AgentExecutionError",
    "MissionError",
    "QualityGateError",
    "SecurityError",
]
