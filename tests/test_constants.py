"""
tests/test_constants.py

Purpose
-------
shared/constants.py is pure, dependency-free data (enums + Final-typed
namespace classes). It has no behavior to exercise — the smoke-test bar
here is "the module imports, every enum/namespace is well-formed, and
the cross-module cross-checks other files rely on actually hold."

Strategy
--------
* Import the module and confirm every name in `__all__` is present.
* Spot-check a handful of enum members resolve to the expected string
  value (catches accidental renames that would silently break every
  consumer that constructs e.g. AgentID("AN-17")).
* Verify the invariants other modules lean on: PRIORITY_WEIGHT covers
  every Priority member, ACTIVE_PLATFORMS/ACTIVE_LANGUAGES are subsets
  of their full enums, DEFAULT_LANGUAGE is itself active.
* Confirm every enum used as a str Enum truly behaves like `str` (this
  is what lets pydantic/JSON serialize them directly).
"""

from __future__ import annotations

from enum import Enum

import pytest

from shared import constants


def test_module_imports_and_exports_everything_declared():
    for name in constants.__all__:
        assert hasattr(constants, name), f"{name} listed in __all__ but missing from module"


def test_agent_id_orchestrator_is_an17():
    assert constants.AgentID.ORCHESTRATOR.value == "AN-17"
    assert constants.AgentID.ORCHESTRATOR == "AN-17"  # str Enum equality


def test_agent_id_has_no_duplicate_values():
    values = [member.value for member in constants.AgentID]
    assert len(values) == len(set(values))


@pytest.mark.parametrize(
    "enum_cls",
    [
        constants.AgentID,
        constants.MissionStatus,
        constants.WorkflowStage,
        constants.LogCategory,
        constants.Priority,
        constants.LogLevel,
        constants.MemoryCategory,
        constants.EventName,
        constants.Platform,
        constants.Language,
        constants.FileExtension,
        constants.ConfigKey,
        constants.FolderName,
    ],
)
def test_every_platform_enum_is_a_str_enum(enum_cls):
    assert issubclass(enum_cls, str)
    assert issubclass(enum_cls, Enum)
    assert len(list(enum_cls)) > 0


def test_priority_weight_covers_every_priority_member():
    for member in constants.Priority:
        assert member in constants.PRIORITY_WEIGHT
    # Lower weight must mean higher priority (critical processed first).
    assert (
        constants.PRIORITY_WEIGHT[constants.Priority.CRITICAL]
        < constants.PRIORITY_WEIGHT[constants.Priority.LOW]
    )


def test_active_platforms_is_a_subset_of_platform():
    assert constants.ACTIVE_PLATFORMS.issubset(set(constants.Platform))
    assert constants.Platform.TELEGRAM in constants.ACTIVE_PLATFORMS
    assert constants.Platform.YOUTUBE in constants.ACTIVE_PLATFORMS


def test_active_languages_is_a_subset_of_language_and_includes_default():
    assert constants.ACTIVE_LANGUAGES.issubset(set(constants.Language))
    assert constants.DEFAULT_LANGUAGE in constants.ACTIVE_LANGUAGES


def test_quality_thresholds_are_internally_consistent():
    assert constants.Quality.MIN_SCORE <= constants.Quality.RECOMMENDED_SCORE <= constants.Quality.MAX_SCORE


def test_retry_defaults_are_positive():
    assert constants.Retry.MAX_ATTEMPTS > 0
    assert constants.Retry.DELAY_SECONDS >= 0
    assert constants.Retry.BACKOFF_MULTIPLIER >= 1
    assert constants.Retry.TIMEOUT_SECONDS > 0
    assert constants.Retry.UNHEALTHY_FAILURE_THRESHOLD > 0
