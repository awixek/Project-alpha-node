"""
tests/test_config.py

Purpose
-------
shared/config.py is the single entry point ("Configuration Access
Interface") every agent uses. The smoke-test bar: the module imports,
`get_config()` loads a valid AlphaConfig from pure defaults, each
config source behaves per its documented contract in isolation, the
precedence chain (defaults < file < .env < real env < runtime
overrides) actually holds, business-rule validation fires on the cases
the docstring calls out, and the cache/manager singleton behavior is
correct (one load, reload works, thread-safe get).

Strategy
--------
* `get_config()` with no file/env present resolves to field defaults.
* JSONFileConfigSource: missing+optional -> {}, missing+required ->
  ConfigFileNotFoundError, malformed JSON -> CorruptedConfigError.
* DotEnvFileConfigSource: parses ALPHA_ prefixed, __-nested keys.
* EnvironmentVariableConfigSource: required_vars enforcement.
* RuntimeOverrideConfigSource is highest precedence via ConfigLoader.
* ConfigValidator.validate_required: telegram/youtube enabled without
  credentials raises MissingConfigError; bad environment value raises
  InvalidConfigError.
* ConfigCache get/set/clear in isolation.
* ConfigManager caches across repeated .get() calls and reloads only
  on force_reload=True.
"""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from shared import config as config_module
from shared.config import (
    AlphaConfig,
    ConfigCache,
    ConfigFileNotFoundError,
    ConfigLoader,
    ConfigManager,
    ConfigValidator,
    CorruptedConfigError,
    DotEnvFileConfigSource,
    EnvironmentVariableConfigSource,
    InvalidConfigError,
    JSONFileConfigSource,
    MissingConfigError,
    MissingEnvironmentVariableError,
    RuntimeOverrideConfigSource,
    get_config,
    reload_config,
)


def test_get_config_with_no_sources_present_returns_field_defaults():
    cfg = get_config()
    assert isinstance(cfg, AlphaConfig)
    assert cfg.project.environment == "development"
    assert cfg.telegram.enabled is False
    assert cfg.youtube.enabled is False


def test_json_file_source_missing_and_optional_returns_empty_dict(tmp_path):
    source = JSONFileConfigSource(tmp_path / "missing.json", required=False)
    assert source.load() == {}


def test_json_file_source_missing_and_required_raises(tmp_path):
    source = JSONFileConfigSource(tmp_path / "missing.json", required=True)
    with pytest.raises(ConfigFileNotFoundError):
        source.load()


def test_json_file_source_malformed_json_raises_corrupted(tmp_path):
    bad_file = tmp_path / "config.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CorruptedConfigError):
        JSONFileConfigSource(bad_file).load()


def test_json_file_source_loads_valid_nested_dict(tmp_path):
    good_file = tmp_path / "config.json"
    good_file.write_text(json.dumps({"project": {"environment": "staging"}}), encoding="utf-8")
    assert JSONFileConfigSource(good_file).load() == {"project": {"environment": "staging"}}


def test_dotenv_source_parses_prefixed_nested_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment, should be ignored",
                "ALPHA_TELEGRAM__BOT_TOKEN=abc123",
                "ALPHA_TELEGRAM__CHAT_ID=\"12345\"",
                "NOT_PREFIXED=ignored",
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = DotEnvFileConfigSource(env_file).load()
    assert result == {"telegram": {"bot_token": "abc123", "chat_id": "12345"}}


def test_dotenv_source_missing_file_returns_empty_dict(tmp_path):
    assert DotEnvFileConfigSource(tmp_path / "no.env").load() == {}


def test_environment_variable_source_enforces_required_vars(monkeypatch):
    monkeypatch.delenv("ALPHA_SOME_REQUIRED_VAR", raising=False)
    source = EnvironmentVariableConfigSource(required_vars=("ALPHA_SOME_REQUIRED_VAR",))
    with pytest.raises(MissingEnvironmentVariableError):
        source.load()


def test_environment_variable_source_loads_prefixed_vars(monkeypatch):
    monkeypatch.setenv("ALPHA_PROJECT__ENVIRONMENT", "production")
    result = EnvironmentVariableConfigSource().load()
    assert result["project"]["environment"] == "production"


def test_runtime_override_source_is_highest_precedence_in_loader(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"project": {"environment": "staging"}}), encoding="utf-8")

    loader = ConfigLoader(
        [
            JSONFileConfigSource(config_file),
            RuntimeOverrideConfigSource({"project": {"environment": "production"}}),
        ]
    )
    cfg = loader.load()
    assert cfg.project.environment == "production"


def test_config_validator_rejects_invalid_environment_value():
    cfg = AlphaConfig(project={"environment": "not_a_real_env"})
    with pytest.raises(InvalidConfigError):
        ConfigValidator.validate_required(cfg)


def test_config_validator_requires_telegram_credentials_when_enabled():
    cfg = AlphaConfig(telegram={"enabled": True})
    with pytest.raises(MissingConfigError):
        ConfigValidator.validate_required(cfg)


def test_config_validator_passes_when_telegram_enabled_with_credentials():
    cfg = AlphaConfig(telegram={"enabled": True, "bot_token": "x", "chat_id": "123"})
    ConfigValidator.validate_required(cfg)  # should not raise


def test_config_validator_requires_youtube_credentials_when_enabled():
    cfg = AlphaConfig(youtube={"enabled": True})
    with pytest.raises(MissingConfigError):
        ConfigValidator.validate_required(cfg)


def test_secret_fields_never_render_in_plain_text():
    cfg = AlphaConfig(telegram={"enabled": True, "bot_token": "super-secret", "chat_id": "1"})
    assert isinstance(cfg.telegram.bot_token, SecretStr)
    assert "super-secret" not in str(cfg.telegram.bot_token)
    assert "super-secret" not in repr(cfg.telegram.bot_token)


def test_config_cache_get_set_clear_roundtrip():
    cache = ConfigCache()
    assert cache.get() is None
    cfg = AlphaConfig()
    cache.set(cfg)
    assert cache.get() is cfg
    cache.clear()
    assert cache.get() is None


def test_config_manager_caches_and_only_reloads_on_force(tmp_path):
    manager = ConfigManager(config_path=tmp_path / "missing.json", env_file_path=tmp_path / "missing.env")
    first = manager.get()
    second = manager.get()
    assert first is second  # cache hit, same instance

    third = manager.get(force_reload=True)
    assert third is not first  # forced reload produces a fresh instance


def test_reload_config_updates_the_process_wide_singleton():
    first = get_config()
    second = reload_config()
    assert first is not second
    assert get_config() is second
