"""
tests/conftest.py

Shared fixtures for the Foundation Smoke Test Suite.

shared.config, shared.logger, and shared.event_bus each hold a
process-wide singleton (ConfigManager cache, logging handler init flag,
EventBus instance). Left alone, whichever test runs first would "win"
and every later test would see its leftover state. The autouse fixture
below resets all three before every test, and also strips any
ALPHA_-prefixed environment variables from the host so a developer's
local shell environment can't leak into what should be deterministic
tests.
"""

from __future__ import annotations

import os

import pytest

import shared.config as config_module
import shared.event_bus as event_bus_module
import shared.logger as logger_module


@pytest.fixture(autouse=True)
def isolate_process_singletons(tmp_path, monkeypatch):
    # Strip any ALPHA_-prefixed vars from the real environment so host
    # configuration can never leak into a test run.
    for key in list(os.environ):
        if key.startswith("ALPHA_"):
            monkeypatch.delenv(key, raising=False)

    # Point the default config/.env file sources at paths that do not
    # exist, so get_config() resolves to pure field defaults unless a
    # test explicitly overrides one of these.
    monkeypatch.setenv(config_module.CONFIG_PATH_ENV_VAR, str(tmp_path / "no_such_config.json"))
    monkeypatch.setenv(config_module.ENV_FILE_PATH_ENV_VAR, str(tmp_path / "no_such.env"))

    # Fresh singletons per test.
    config_module._manager = config_module.ConfigManager()
    logger_module._LoggingInitializer.reset()
    logger_module._LoggingInitializer._initialized = False
    event_bus_module._bus_instance = None

    yield

    logger_module._LoggingInitializer.reset()


@pytest.fixture
def valid_config():
    """A minimal, business-rule-valid AlphaConfig (telegram/youtube left
    disabled, so no credentials are required)."""
    from shared.config import AlphaConfig

    return AlphaConfig()
