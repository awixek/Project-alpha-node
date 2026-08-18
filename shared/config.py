"""
shared/config.py

Project Alpha Node — Shared Configuration Layer (Configuration Backbone)
==========================================================================

Single entry point every agent uses to obtain validated runtime
configuration. No agent should read a JSON file, an environment variable,
or a `.env` file directly — every configuration request passes through
this module, via `get_config()`.

Design rules enforced in this file:
    * No business logic beyond loading/merging/validating configuration.
    * No calls to the shared logger (see note below on why that would
      be circular).
    * No secrets are ever printed or logged in plain text.
    * Every config value is typed and validated via pydantic — nothing is
      trusted as a raw string past the boundary of this module.

WHY NO LOGGING HERE:
    The Logging Configuration (level, folder, rotation) is itself a
    section this module produces. If this module called the shared
    logger, the logger would depend on config and config would depend on
    the logger — a circular import. Callers are expected to catch
    `ConfigError` (or a specific subclass) and log it themselves once the
    logger is initialized — which happens only after config has loaded.

CONFIGURATION SOURCES, LOWEST TO HIGHEST PRECEDENCE:
    1. Pydantic model field defaults   (Default Configuration Handler)
    2. JSON configuration file         (configs/config.json by default)
    3. `.env` file                     (project root/.env by default)
    4. Real process environment variables, prefixed ALPHA_
    5. Runtime overrides passed programmatically to get_config()

MODULE RESPONSIBILITIES (functional-requirement mapping):
    Configuration Loader            -> ConfigLoader
    Configuration Validator         -> ConfigValidator
    Configuration Cache             -> ConfigCache
    Configuration Manager           -> ConfigManager
    Environment Loader              -> EnvironmentVariableConfigSource,
                                        DotEnvFileConfigSource
    Default Configuration Handler   -> DefaultConfigSource
    Configuration Access Interface  -> get_config() / reload_config()

Each source implements the small `ConfigSource` interface, so a future
cloud-based source (AWS Secrets Manager, Azure App Configuration, GCP
Secret Manager, ...) can be added by writing one new class and appending
it to `_default_sources()` — nothing else in this file changes.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Final, Mapping

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from shared.constants import FolderName, LogLevel, Priority, Project, Quality, Retry
from shared.exceptions import ConfigurationError

# ──────────────────────────────────────────────────────────────────────────
# Path & naming conventions
# ──────────────────────────────────────────────────────────────────────────

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

ENV_PREFIX: Final[str] = "ALPHA_"
"""Prefix required on any environment variable to be picked up as config,
e.g. ALPHA_TELEGRAM__BOT_TOKEN. Double underscore denotes nesting."""

CONFIG_PATH_ENV_VAR: Final[str] = "ALPHA_CONFIG_PATH"
ENV_FILE_PATH_ENV_VAR: Final[str] = "ALPHA_ENV_FILE"

DEFAULT_CONFIG_PATH: Final[Path] = REPO_ROOT / FolderName.CONFIGS.value / "config.json"
DEFAULT_ENV_FILE_PATH: Final[Path] = REPO_ROOT / ".env"


# ──────────────────────────────────────────────────────────────────────────
# Structured exception hierarchy
# ──────────────────────────────────────────────────────────────────────────

class ConfigError(ConfigurationError):
    """
    Base class for every configuration failure in Alpha Node.

    Inherits from shared.exceptions.ConfigurationError (itself an
    AlphaBaseException subclass), so every configuration failure fits
    the platform-wide exception hierarchy — callers can catch narrowly
    (ConfigFileNotFoundError), by domain (ConfigError /
    ConfigurationError), or generically (AlphaBaseException), and can
    always call .to_error_report() to get a schemas.ErrorReport.

    Messages on this hierarchy are always safe to log or print: pydantic
    renders SecretStr fields as '**********' in validation error text,
    and no subclass here attaches the raw merged config dict or a raw
    secret value.
    """

    default_code = "configuration_error"


class ConfigFileNotFoundError(ConfigError):
    """A configuration file was required but does not exist on disk."""

    default_code = "config_file_not_found"


class ConfigPermissionError(ConfigError):
    """The process lacks permission to read a required configuration file."""

    default_code = "config_permission_denied"


class CorruptedConfigError(ConfigError):
    """A configuration file exists but could not be parsed (e.g. invalid JSON)."""

    default_code = "corrupted_config"


class InvalidConfigError(ConfigError):
    """Configuration values were present but failed type/shape validation."""

    default_code = "invalid_config"


class MissingConfigError(ConfigError):
    """A value required by business rules (not just by type) was absent."""

    default_code = "missing_config"


class MissingEnvironmentVariableError(ConfigError):
    """A specifically required environment variable was not set."""

    default_code = "missing_environment_variable"


# ──────────────────────────────────────────────────────────────────────────
# Config sources (strategy pattern — future cloud sources plug in here)
# ──────────────────────────────────────────────────────────────────────────

class ConfigSource(ABC):
    """Common interface for anything that can contribute configuration."""

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """Return a (possibly nested) dict of configuration values.

        Raises:
            ConfigError: or a specific subclass, on any failure reading
                or parsing this source.
        """
        raise NotImplementedError


class DefaultConfigSource(ConfigSource):
    """
    Default Configuration Handler.

    Intentional no-op: defaults already live exactly once, as field
    defaults on the pydantic models below (several sourced from
    constants.py). Duplicating those same literals into a dict here
    would violate "no duplicate values". This class exists only to make
    the precedence chain explicit and self-documenting.
    """

    def load(self) -> dict[str, Any]:
        return {}


class JSONFileConfigSource(ConfigSource):
    """Loads configuration from a JSON file. Missing + optional -> {}."""

    def __init__(self, path: Path, *, required: bool = False) -> None:
        self._path = path
        self._required = required

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            if self._required:
                raise ConfigFileNotFoundError(f"Required config file not found: {self._path}")
            return {}

        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except PermissionError as exc:
            raise ConfigPermissionError(f"Permission denied reading config file: {self._path}") from exc
        except OSError as exc:
            raise ConfigError(f"Failed to read config file {self._path}: {exc}") from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise CorruptedConfigError(f"Invalid JSON in config file {self._path}: {exc}") from exc

        if not isinstance(data, dict):
            raise CorruptedConfigError(f"Config file {self._path} must contain a JSON object at the root.")
        return data


class DotEnvFileConfigSource(ConfigSource):
    """
    Environment Loader (file-backed half).

    Loads KEY=VALUE pairs from a `.env` file and applies the same
    ALPHA_-prefixed, double-underscore-nested convention as real
    environment variables. Missing file -> empty dict (a `.env` file is
    always optional).
    """

    def __init__(self, path: Path, *, prefix: str = ENV_PREFIX) -> None:
        self._path = path
        self._prefix = prefix

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}

        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except PermissionError as exc:
            raise ConfigPermissionError(f"Permission denied reading env file: {self._path}") from exc
        except OSError as exc:
            raise ConfigError(f"Failed to read env file {self._path}: {exc}") from exc

        flat: dict[str, str] = {}
        for raw_line in raw_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            flat[key] = value
        return _unflatten_prefixed(flat, self._prefix)


class EnvironmentVariableConfigSource(ConfigSource):
    """
    Environment Loader (process-env half).

    Loads configuration from real process environment variables, and
    optionally enforces that a caller-specified set of variables exist.
    """

    def __init__(self, *, prefix: str = ENV_PREFIX, required_vars: tuple[str, ...] = ()) -> None:
        self._prefix = prefix
        self._required_vars = required_vars

    def load(self) -> dict[str, Any]:
        missing = [name for name in self._required_vars if name not in os.environ]
        if missing:
            raise MissingEnvironmentVariableError(
                f"Required environment variable(s) not set: {', '.join(missing)}"
            )
        return _unflatten_prefixed(dict(os.environ), self._prefix)


class RuntimeOverrideConfigSource(ConfigSource):
    """Wraps caller-supplied overrides — always the highest precedence."""

    def __init__(self, overrides: Mapping[str, Any] | None) -> None:
        self._overrides = dict(overrides) if overrides else {}

    def load(self) -> dict[str, Any]:
        return dict(self._overrides)


def _unflatten_prefixed(flat: Mapping[str, str], prefix: str) -> dict[str, Any]:
    """
    Converts flat, prefixed, double-underscore-nested keys into a nested
    dict. e.g. {"ALPHA_TELEGRAM__BOT_TOKEN": "x"} with prefix "ALPHA_"
    becomes {"telegram": {"bot_token": "x"}}.
    """
    result: dict[str, Any] = {}
    for raw_key, value in flat.items():
        if not raw_key.startswith(prefix):
            continue
        remainder = raw_key[len(prefix):]
        parts = [p.lower() for p in remainder.split("__") if p]
        if not parts:
            continue
        cursor = result
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return result


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merges `override` onto `base`, without mutating either."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ──────────────────────────────────────────────────────────────────────────
# Configuration section models
# ──────────────────────────────────────────────────────────────────────────

class _StrictSection(BaseModel):
    """Base for individual config sections: typos should fail loudly."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectConfig(_StrictSection):
    name: str = Project.NAME
    version: str = Project.VERSION
    environment: str = "development"  # "development" | "staging" | "production"


class APIConfig(_StrictSection):
    """
    Generic bucket for external API credentials, keyed by provider name
    so new providers never require a schema change (no vendor lock-in).
    """

    default_timeout_seconds: float = Retry.TIMEOUT_SECONDS
    default_max_retries: int = Retry.MAX_ATTEMPTS
    unhealthy_failure_threshold: int = Retry.UNHEALTHY_FAILURE_THRESHOLD
    """Consecutive failures before shared.api_router.APIRouter marks a
    provider unhealthy. Was hardcoded as a module constant in
    api_router.py; now config-driven per the Phase 2.1 Foundation
    Review (item 4: no hardcoded provider/retry/timeout values)."""
    keys: dict[str, SecretStr] = {}


class TelegramConfig(_StrictSection):
    enabled: bool = False
    bot_token: SecretStr | None = None
    chat_id: str | None = None


class YouTubeConfig(_StrictSection):
    enabled: bool = False
    client_id: str | None = None
    client_secret: SecretStr | None = None
    refresh_token: SecretStr | None = None
    default_visibility: str = "private"  # "public" | "unlisted" | "private"


class LoggingConfig(_StrictSection):
    level: LogLevel = LogLevel.INFO
    folder: str = FolderName.LOGS.value
    json_format: bool = True
    max_file_size_mb: int = 10
    backup_count: int = 5


class MemoryConfig(_StrictSection):
    backend: str = "local"  # "local" | "redis" | "cloud"
    folder: str = FolderName.MEMORY.value
    default_ttl_seconds: int | None = None


class StorageConfig(_StrictSection):
    backend: str = "local"  # "local" | "s3" | "gcs" | "azure_blob"
    root_folder: str = FolderName.STORAGE.value
    outputs_folder: str = FolderName.OUTPUTS.value


class QualityConfig(_StrictSection):
    min_score: float = Quality.MIN_SCORE
    recommended_score: float = Quality.RECOMMENDED_SCORE
    max_score: float = Quality.MAX_SCORE


class RetryConfig(_StrictSection):
    max_attempts: int = Retry.MAX_ATTEMPTS
    delay_seconds: float = Retry.DELAY_SECONDS
    backoff_multiplier: float = Retry.BACKOFF_MULTIPLIER
    timeout_seconds: float = Retry.TIMEOUT_SECONDS


class WorkflowConfig(_StrictSection):
    require_human_approval: bool = True
    default_priority: Priority = Priority.MEDIUM
    max_parallel_missions: int = 1


class AgentConfig(_StrictSection):
    """
    Generic per-agent settings bucket, keyed by AgentID value in
    AlphaConfig.agents. Any current or future agent (AN-01..AN-17, and
    beyond) gets configuration without a schema change here.
    """

    enabled: bool = True
    settings: dict[str, Any] = {}


class AlphaConfig(BaseModel):
    """
    The complete, type-validated configuration for one running instance
    of Project Alpha Node. This is the only object `get_config()` ever
    returns to a caller.

    extra="ignore" at the root (only) lets an older codebase tolerate an
    unrecognized future top-level section in config.json without
    crashing; each section above still forbids unknown fields so typos
    within a known section are caught immediately.

    NOTE: this model enforces *type* validity only. Business-required
    combinations (e.g. "telegram.bot_token is required when
    telegram.enabled=True") are enforced separately by ConfigValidator,
    so that rule can evolve without touching the schema itself.
    """

    model_config = ConfigDict(extra="ignore")

    project: ProjectConfig = ProjectConfig()
    api: APIConfig = APIConfig()
    telegram: TelegramConfig = TelegramConfig()
    youtube: YouTubeConfig = YouTubeConfig()
    logging: LoggingConfig = LoggingConfig()
    memory: MemoryConfig = MemoryConfig()
    storage: StorageConfig = StorageConfig()
    quality: QualityConfig = QualityConfig()
    retry: RetryConfig = RetryConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    agents: dict[str, AgentConfig] = {}

    def safe_dict(self) -> dict[str, Any]:
        """
        Returns this config as a plain dict safe to log or print: every
        SecretStr field is serialized as a masked placeholder, never the
        real value.
        """
        return self.model_dump(mode="json")


# ──────────────────────────────────────────────────────────────────────────
# Loader
# ──────────────────────────────────────────────────────────────────────────

class ConfigLoader:
    """
    Configuration Loader.

    Assembles an ordered list of ConfigSource objects, merges them by
    precedence, and constructs an AlphaConfig (type/shape validation
    only — see ConfigValidator for business-required-value checks).

    Holds no cache itself, so it stays trivially unit-testable in
    isolation: construct one with fake sources, call .load(), assert.
    """

    def __init__(self, sources: list[ConfigSource]) -> None:
        self._sources = sources

    @classmethod
    def with_default_sources(
        cls,
        *,
        config_path: Path | None = None,
        env_file_path: Path | None = None,
        runtime_overrides: Mapping[str, Any] | None = None,
    ) -> "ConfigLoader":
        """Builds a loader using the standard precedence chain described
        in this module's docstring."""
        resolved_config_path = config_path or Path(
            os.environ.get(CONFIG_PATH_ENV_VAR, str(DEFAULT_CONFIG_PATH))
        )
        resolved_env_file_path = env_file_path or Path(
            os.environ.get(ENV_FILE_PATH_ENV_VAR, str(DEFAULT_ENV_FILE_PATH))
        )
        return cls(
            [
                DefaultConfigSource(),
                JSONFileConfigSource(resolved_config_path),
                DotEnvFileConfigSource(resolved_env_file_path),
                EnvironmentVariableConfigSource(),
                RuntimeOverrideConfigSource(runtime_overrides),
            ]
        )

    def load(self) -> AlphaConfig:
        merged: dict[str, Any] = {}
        for source in self._sources:
            merged = _deep_merge(merged, source.load())
        try:
            return AlphaConfig(**merged)
        except ValidationError as exc:
            raise InvalidConfigError(f"Configuration failed validation: {exc}") from exc


# ──────────────────────────────────────────────────────────────────────────
# Validator
# ──────────────────────────────────────────────────────────────────────────

class ConfigValidator:
    """
    Configuration Validator.

    Runs *business*-required-value checks on an already type-valid
    AlphaConfig — the kind of rule that depends on more than one field
    at once (e.g. a feature flag implying a credential is mandatory).
    Kept separate from ConfigLoader so these rules can grow without
    touching the loading/merging mechanics.
    """

    @staticmethod
    def validate_required(config: AlphaConfig) -> None:
        """
        Raises:
            MissingConfigError: if a value required by business rules is absent.
            InvalidConfigError: if a value is present but semantically invalid.
        """
        if config.project.environment not in {"development", "staging", "production"}:
            raise InvalidConfigError(
                f"project.environment must be one of 'development', 'staging', "
                f"'production' (got: {config.project.environment!r})."
            )

        if config.telegram.enabled and (config.telegram.bot_token is None or not config.telegram.chat_id):
            raise MissingConfigError(
                "telegram.bot_token and telegram.chat_id are required when telegram.enabled=True."
            )

        if config.youtube.enabled and (
            not config.youtube.client_id
            or config.youtube.client_secret is None
            or config.youtube.refresh_token is None
        ):
            raise MissingConfigError(
                "youtube.client_id, youtube.client_secret, and youtube.refresh_token "
                "are all required when youtube.enabled=True."
            )

        if config.quality.min_score > config.quality.max_score:
            raise InvalidConfigError("quality.min_score cannot exceed quality.max_score.")


# ──────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────

class ConfigCache:
    """
    Configuration Cache.

    Thread-safe holder for the current AlphaConfig singleton. Isolated
    from ConfigManager so cache behavior (get/set/clear) can be unit
    tested without triggering an actual load.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value: AlphaConfig | None = None

    def get(self) -> AlphaConfig | None:
        with self._lock:
            return self._value

    def set(self, value: AlphaConfig) -> None:
        with self._lock:
            self._value = value

    def clear(self) -> None:
        with self._lock:
            self._value = None


# ──────────────────────────────────────────────────────────────────────────
# Manager (facade)
# ──────────────────────────────────────────────────────────────────────────

class ConfigManager:
    """
    Configuration Manager.

    The facade every agent talks to (indirectly, through get_config() /
    reload_config() below). Combines the Loader, Validator, and Cache,
    and guarantees — via a dedicated lock, checked before and after
    acquiring it — that concurrent first-time callers trigger exactly
    one load ("prevent duplicate loading") rather than racing.
    """

    def __init__(self, *, config_path: Path | None = None, env_file_path: Path | None = None) -> None:
        self._config_path = config_path
        self._env_file_path = env_file_path
        self._cache = ConfigCache()
        self._load_lock = threading.Lock()

    def get(
        self,
        *,
        force_reload: bool = False,
        runtime_overrides: Mapping[str, Any] | None = None,
    ) -> AlphaConfig:
        cached = self._cache.get()
        if cached is not None and not force_reload:
            return cached

        with self._load_lock:
            # Re-check: another thread may have already loaded while we
            # were waiting for the lock.
            cached = self._cache.get()
            if cached is not None and not force_reload:
                return cached

            loader = ConfigLoader.with_default_sources(
                config_path=self._config_path,
                env_file_path=self._env_file_path,
                runtime_overrides=runtime_overrides,
            )
            config = loader.load()
            ConfigValidator.validate_required(config)
            self._cache.set(config)
            return config

    def reload(self, runtime_overrides: Mapping[str, Any] | None = None) -> AlphaConfig:
        return self.get(force_reload=True, runtime_overrides=runtime_overrides)


# ──────────────────────────────────────────────────────────────────────────
# Configuration Access Interface — every agent uses only this
# ──────────────────────────────────────────────────────────────────────────

_manager = ConfigManager()


def get_config(
    *,
    force_reload: bool = False,
    runtime_overrides: Mapping[str, Any] | None = None,
) -> AlphaConfig:
    """
    Returns the process-wide validated configuration singleton.

    This is the ONLY function any agent should call to obtain
    configuration. Every agent that calls this within the same process
    receives the same validated AlphaConfig instance.

    Args:
        force_reload: re-reads all sources instead of using the cache.
        runtime_overrides: highest-precedence values, applied only when
            (re)loading — ignored on a cache hit unless force_reload=True.

    Raises:
        ConfigFileNotFoundError: a required config file is missing.
        ConfigPermissionError: a config file exists but can't be read.
        CorruptedConfigError: a config file exists but isn't valid JSON.
        MissingEnvironmentVariableError: a required env var isn't set.
        InvalidConfigError: values are present but fail type/semantic checks.
        MissingConfigError: a business-required value is absent.
    """
    return _manager.get(force_reload=force_reload, runtime_overrides=runtime_overrides)


def reload_config(runtime_overrides: Mapping[str, Any] | None = None) -> AlphaConfig:
    """Convenience wrapper: forces a fresh load and re-caches the result."""
    return _manager.reload(runtime_overrides=runtime_overrides)


__all__ = [
    "REPO_ROOT",
    "ENV_PREFIX",
    "CONFIG_PATH_ENV_VAR",
    "ENV_FILE_PATH_ENV_VAR",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENV_FILE_PATH",
    "ConfigurationError",
    "ConfigError",
    "ConfigFileNotFoundError",
    "ConfigPermissionError",
    "CorruptedConfigError",
    "InvalidConfigError",
    "MissingConfigError",
    "MissingEnvironmentVariableError",
    "ConfigSource",
    "DefaultConfigSource",
    "JSONFileConfigSource",
    "DotEnvFileConfigSource",
    "EnvironmentVariableConfigSource",
    "RuntimeOverrideConfigSource",
    "ProjectConfig",
    "APIConfig",
    "TelegramConfig",
    "YouTubeConfig",
    "LoggingConfig",
    "MemoryConfig",
    "StorageConfig",
    "QualityConfig",
    "RetryConfig",
    "WorkflowConfig",
    "AgentConfig",
    "AlphaConfig",
    "ConfigLoader",
    "ConfigValidator",
    "ConfigCache",
    "ConfigManager",
    "get_config",
    "reload_config",
]
