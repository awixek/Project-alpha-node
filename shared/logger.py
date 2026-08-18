"""
shared/logger.py

Project Alpha Node — Shared Logging Layer (Logging Backbone)
================================================================

Single centralized logging system every agent, API call, workflow, and
mission must use. No module should call `logging.getLogger()` directly
or construct its own handlers — everything goes through `get_logger()`
or `get_agent_logger()` below.

Design rules enforced in this file:
    * Built on Python's stdlib `logging` — no reinvented dispatch.
    * Configuration-driven: handler setup reads LoggingConfig from
      shared.config.get_config(). No hardcoded paths.
    * Idempotent setup: handlers are attached to the root "alpha_node"
      logger exactly once per process, no matter how many times
      get_logger() is called (prevents duplicate log lines).
    * Never crashes the calling application: file-handler setup failures
      (bad path, permission error, disk issue) fall back to console-only
      logging with a single warning, instead of raising.

LOGGING ARCHITECTURE:
    "alpha_node" (root)                          <- configured once
        ├── "alpha_node.agent.<AN-XX>"            <- one per agent
        ├── "alpha_node.mission"
        ├── "alpha_node.workflow"
        ├── "alpha_node.api"
        └── ...                                   <- any category/module

    All child loggers propagate to the root, which owns the actual
    handlers (console + optional rotating file). Categories (Mission,
    Agent, Workflow, API, Retry, Performance, Memory, System, Quality,
    Error, Security — see LogCategory in constants.py) are attached to
    individual log records, not to separate logger instances, so a
    single agent can log across multiple categories through one logger.

FUTURE REMOTE LOGGING:
    Add a new `logging.Handler` subclass (e.g. a handler that ships
    JSON lines to a log aggregation service) and append it inside
    `_LoggingInitializer._configure()`. No other code in this file, or
    any call site, needs to change.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final, Iterator, Mapping
from uuid import UUID

from shared.config import ConfigError, LoggingConfig, REPO_ROOT, get_config
from shared.constants import AgentID, LogCategory, LogLevel, WorkflowStage

# ──────────────────────────────────────────────────────────────────────────
# Constants local to this module
# ──────────────────────────────────────────────────────────────────────────

ROOT_LOGGER_NAME: Final[str] = "alpha_node"
DEFAULT_LOG_FILENAME: Final[str] = "alpha_node.log"
BYTES_PER_MB: Final[int] = 1024 * 1024

_LEVEL_MAP: Final[dict[LogLevel, int]] = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}

# Extra LogRecord attribute names used to carry structured context.
# Prefixed with "alpha_" to avoid colliding with stdlib LogRecord fields.
_ATTR_MISSION_ID: Final[str] = "alpha_mission_id"
_ATTR_AGENT_ID: Final[str] = "alpha_agent_id"
_ATTR_WORKFLOW_STAGE: Final[str] = "alpha_workflow_stage"
_ATTR_CATEGORY: Final[str] = "alpha_category"
_ATTR_EXECUTION_TIME_MS: Final[str] = "alpha_execution_time_ms"
_ATTR_METADATA: Final[str] = "alpha_metadata"


# ──────────────────────────────────────────────────────────────────────────
# Formatters
# ──────────────────────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """
    Renders each log record as one structured JSON line.

    Used for file output (and, in future, remote log shipping) so
    records are machine-parseable. Falls back to a minimal plain-text
    line if a metadata value somehow isn't JSON-serializable — this
    formatter must never raise, since a formatting failure inside the
    logging pipeline must never crash the application.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
            "category": getattr(record, _ATTR_CATEGORY, None),
            "mission_id": getattr(record, _ATTR_MISSION_ID, None),
            "agent_id": getattr(record, _ATTR_AGENT_ID, None),
            "workflow_stage": getattr(record, _ATTR_WORKFLOW_STAGE, None),
            "execution_time_ms": getattr(record, _ATTR_EXECUTION_TIME_MS, None),
            "metadata": getattr(record, _ATTR_METADATA, None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            # Absolute last resort: never let a formatting error propagate.
            return f'{{"timestamp": "{payload["timestamp"]}", "level": "{payload["level"]}", ' \
                   f'"message": "log record could not be serialized"}}'


class ConsoleFormatter(logging.Formatter):
    """Renders each log record as one concise, human-readable line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        parts = [f"[{timestamp}]", f"[{record.levelname}]"]

        category = getattr(record, _ATTR_CATEGORY, None)
        if category:
            parts.append(f"[{category}]")
        parts.append(f"[{record.module}]")
        parts.append(str(record.getMessage()))

        context_bits = []
        for label, attr in (
            ("mission", _ATTR_MISSION_ID),
            ("agent", _ATTR_AGENT_ID),
            ("stage", _ATTR_WORKFLOW_STAGE),
            ("exec_ms", _ATTR_EXECUTION_TIME_MS),
        ):
            value = getattr(record, attr, None)
            if value is not None:
                context_bits.append(f"{label}={value}")
        if context_bits:
            parts.append(f"({', '.join(context_bits)})")

        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


# ──────────────────────────────────────────────────────────────────────────
# One-time, safe handler setup
# ──────────────────────────────────────────────────────────────────────────

class _LoggingInitializer:
    """
    Configures the root "alpha_node" logger exactly once per process.

    Double-checked locking mirrors ConfigManager's pattern, guaranteeing
    concurrent first-time callers don't attach duplicate handlers.
    """

    _initialized: bool = False
    _lock: Final[threading.Lock] = threading.Lock()

    @classmethod
    def ensure_initialized(cls) -> None:
        if cls._initialized:
            return
        with cls._lock:
            if cls._initialized:
                return
            cls._configure()
            cls._initialized = True

    @classmethod
    def reset(cls) -> None:
        """Clears initialization state so the next call reconfigures from
        scratch. Intended for tests and for reacting to a config reload."""
        with cls._lock:
            root = logging.getLogger(ROOT_LOGGER_NAME)
            for handler in list(root.handlers):
                root.removeHandler(handler)
                handler.close()
            cls._initialized = False

    @classmethod
    def _configure(cls) -> None:
        # Never let a broken handler crash the app: catch loudly, log a
        # single fallback warning, keep going.
        logging.raiseExceptions = False

        try:
            log_config: LoggingConfig = get_config().logging
        except ConfigError:
            log_config = LoggingConfig()  # safe built-in defaults

        root = logging.getLogger(ROOT_LOGGER_NAME)
        root.setLevel(_LEVEL_MAP.get(log_config.level, logging.INFO))
        root.propagate = False  # never bubble into the real Python root logger

        for handler in list(root.handlers):
            root.removeHandler(handler)
            handler.close()

        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setFormatter(ConsoleFormatter())
        root.addHandler(console_handler)

        try:
            log_dir = (REPO_ROOT / log_config.folder).resolve()
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / DEFAULT_LOG_FILENAME
            file_handler = RotatingFileHandler(
                filename=str(log_path),
                maxBytes=max(log_config.max_file_size_mb, 1) * BYTES_PER_MB,
                backupCount=max(log_config.backup_count, 0),
                encoding="utf-8",
            )
            file_handler.setFormatter(
                JSONFormatter() if log_config.json_format else ConsoleFormatter()
            )
            root.addHandler(file_handler)
        except (OSError, PermissionError) as exc:
            root.warning(
                "File logging disabled — falling back to console-only. Reason: %s", exc
            )


def configure_logging(*, force: bool = False) -> None:
    """
    Public hook to (re)configure logging, e.g. after `reload_config()`
    changes logging settings. Safe to call repeatedly.
    """
    if force:
        _LoggingInitializer.reset()
    _LoggingInitializer.ensure_initialized()


# ──────────────────────────────────────────────────────────────────────────
# AlphaLogger — the object every call site actually uses
# ──────────────────────────────────────────────────────────────────────────

class AlphaLogger:
    """
    Thin, ergonomic wrapper around a stdlib Logger that carries bound
    structured context (mission/agent/stage/category) and attaches it to
    every record via `extra=`.

    Not constructed directly — obtain one via get_logger() or
    get_agent_logger().
    """

    def __init__(
        self,
        stdlib_logger: logging.Logger,
        *,
        category: LogCategory | None = None,
        mission_id: str | UUID | None = None,
        agent_id: AgentID | None = None,
        workflow_stage: WorkflowStage | None = None,
    ) -> None:
        self._logger = stdlib_logger
        self._category = category
        self._mission_id = str(mission_id) if mission_id is not None else None
        self._agent_id = agent_id
        self._workflow_stage = workflow_stage

    def bind(
        self,
        *,
        category: LogCategory | None = None,
        mission_id: str | UUID | None = None,
        agent_id: AgentID | None = None,
        workflow_stage: WorkflowStage | None = None,
    ) -> "AlphaLogger":
        """Returns a new AlphaLogger with the given context merged over
        this one's — the original is left unchanged."""
        return AlphaLogger(
            self._logger,
            category=category or self._category,
            mission_id=mission_id if mission_id is not None else self._mission_id,
            agent_id=agent_id or self._agent_id,
            workflow_stage=workflow_stage or self._workflow_stage,
        )

    # -- core dispatch ----------------------------------------------------

    def _log(
        self,
        level: int,
        message: str,
        *args: Any,
        category: LogCategory | None = None,
        mission_id: str | UUID | None = None,
        agent_id: AgentID | None = None,
        workflow_stage: WorkflowStage | None = None,
        execution_time_ms: float | None = None,
        metadata: Mapping[str, Any] | None = None,
        exc_info: bool = False,
    ) -> None:
        _LoggingInitializer.ensure_initialized()

        resolved_mission_id = str(mission_id) if mission_id is not None else self._mission_id
        resolved_agent_id = agent_id or self._agent_id
        resolved_stage = workflow_stage or self._workflow_stage
        resolved_category = category or self._category or LogCategory.SYSTEM

        extra = {
            _ATTR_CATEGORY: resolved_category.value,
            _ATTR_MISSION_ID: resolved_mission_id,
            _ATTR_AGENT_ID: resolved_agent_id.value if resolved_agent_id else None,
            _ATTR_WORKFLOW_STAGE: resolved_stage.value if resolved_stage else None,
            _ATTR_EXECUTION_TIME_MS: execution_time_ms,
            _ATTR_METADATA: dict(metadata) if metadata else None,
        }

        try:
            self._logger.log(level, message, *args, extra=extra, exc_info=exc_info)
        except Exception:  # noqa: BLE001 — logging must never crash the caller
            # Absolute last line of defense. Deliberately swallow: a
            # failure to log is not a reason to fail the mission.
            pass

    # -- public level methods ---------------------------------------------

    def debug(self, message: str, *args: Any, **context: Any) -> None:
        self._log(logging.DEBUG, message, *args, **context)

    def info(self, message: str, *args: Any, **context: Any) -> None:
        self._log(logging.INFO, message, *args, **context)

    def warning(self, message: str, *args: Any, **context: Any) -> None:
        self._log(logging.WARNING, message, *args, **context)

    def error(self, message: str, *args: Any, **context: Any) -> None:
        self._log(logging.ERROR, message, *args, **context)

    def critical(self, message: str, *args: Any, **context: Any) -> None:
        self._log(logging.CRITICAL, message, *args, **context)

    def exception(self, message: str, *args: Any, **context: Any) -> None:
        """Convenience for use inside an `except` block — logs at ERROR
        with the current exception's traceback attached."""
        context.setdefault("exc_info", True)
        self._log(logging.ERROR, message, *args, **context)

    def security(self, message: str, *args: Any, **context: Any) -> None:
        """Convenience for security-relevant events (auth failures,
        input-sanitization rejections, etc.) — logs at WARNING under the
        SECURITY category by default."""
        context.setdefault("category", LogCategory.SECURITY)
        self._log(logging.WARNING, message, *args, **context)

    # -- performance timing -------------------------------------------------

    @contextmanager
    def timed(
        self,
        message: str,
        *,
        category: LogCategory = LogCategory.PERFORMANCE,
        **context: Any,
    ) -> Iterator[None]:
        """
        Context manager that logs `message` on exit with
        execution_time_ms populated automatically. Logs at INFO on
        success, ERROR (with traceback) if the block raises — then
        re-raises, since timing a failure must not hide it.
        """
        start = time.perf_counter()
        try:
            yield
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._log(
                logging.ERROR,
                message,
                category=category,
                execution_time_ms=elapsed_ms,
                exc_info=True,
                **context,
            )
            raise
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._log(
                logging.INFO,
                message,
                category=category,
                execution_time_ms=elapsed_ms,
                **context,
            )


# ──────────────────────────────────────────────────────────────────────────
# Access interface — every agent uses only this
# ──────────────────────────────────────────────────────────────────────────

def get_logger(name: str, *, category: LogCategory | None = None) -> AlphaLogger:
    """
    Returns an AlphaLogger for an arbitrary module/component name, e.g.
    get_logger("orchestrator", category=LogCategory.WORKFLOW).

    Safe to call repeatedly with the same name — stdlib `logging.getLogger`
    caches loggers by name, and handler setup happens at most once per
    process regardless of how many times this is called.
    """
    stdlib_logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
    return AlphaLogger(stdlib_logger, category=category)


def get_agent_logger(agent_id: AgentID) -> AlphaLogger:
    """
    Returns an AlphaLogger pre-bound to a specific agent, so every call
    site for that agent doesn't need to pass agent_id repeatedly.

    e.g. logger = get_agent_logger(AgentID.RESEARCH_CORE)
         logger.info("Starting research pass", mission_id=mission.mission_id)
    """
    stdlib_logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.agent.{agent_id.value}")
    return AlphaLogger(stdlib_logger, category=LogCategory.AGENT, agent_id=agent_id)


__all__ = [
    "ROOT_LOGGER_NAME",
    "JSONFormatter",
    "ConsoleFormatter",
    "AlphaLogger",
    "configure_logging",
    "get_logger",
    "get_agent_logger",
]
