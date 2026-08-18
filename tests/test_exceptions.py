"""
tests/test_exceptions.py

Purpose
-------
shared/exceptions.py is the single hierarchy every other module raises
through. The smoke-test bar: every declared exception is importable and
actually inherits from AlphaBaseException, each carries its documented
`default_code`/`default_retryable`, per-instance overrides work, and
`to_error_report()` produces a valid schemas.ErrorReport (the contract
several other modules — config.py, validators.py — depend on).

Strategy
--------
* Import + `__all__` completeness check.
* Confirm the whole hierarchy chains back to AlphaBaseException.
* Raise/catch a couple of concrete subclasses and check message, code,
  severity, retryable, context, and `__cause__` propagate correctly.
* Confirm `to_error_report()` returns a schemas.ErrorReport with the
  right fields, including the ORCHESTRATOR fallback when no agent_id
  was supplied.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared import exceptions as exc_module
from shared.constants import AgentID
from shared.schemas import ErrorReport, Severity


ALL_EXCEPTION_NAMES = [name for name in exc_module.__all__ if name != "AlphaBaseException"]


def test_module_imports_and_exports_everything_declared():
    for name in exc_module.__all__:
        assert hasattr(exc_module, name), f"{name} listed in __all__ but missing from module"


@pytest.mark.parametrize("name", ALL_EXCEPTION_NAMES)
def test_every_exception_inherits_alpha_base_exception(name):
    cls = getattr(exc_module, name)
    assert issubclass(cls, exc_module.AlphaBaseException)
    assert issubclass(cls, Exception)


def test_base_exception_applies_documented_defaults():
    err = exc_module.AlphaBaseException("something went wrong")
    assert err.message == "something went wrong"
    assert err.code == "alpha_error"
    assert err.severity == Severity.ERROR
    assert err.retryable is False
    assert err.context == {}
    assert str(err) == "[alpha_error] something went wrong"


def test_subclass_default_code_and_retryable_are_distinct_from_base():
    err = exc_module.ValidationError("bad input")
    assert err.code == "validation_error"
    assert err.retryable is False

    err2 = exc_module.APIProviderError("provider timed out")
    assert err2.code == "api_provider_error"
    assert err2.retryable is True  # provider failures are retryable by default


def test_per_instance_overrides_win_over_class_defaults():
    err = exc_module.ValidationError("bad input", retryable=True, code="custom_code")
    assert err.retryable is True
    assert err.code == "custom_code"


def test_context_and_mission_id_and_cause_are_captured():
    cause = ValueError("root cause")
    mission_id = uuid4()
    err = exc_module.AgentExecutionError(
        "agent blew up",
        agent_id=AgentID.SCRIPT_FORGE,
        mission_id=mission_id,
        context={"attempt": 2},
        cause=cause,
    )
    assert err.agent_id == AgentID.SCRIPT_FORGE
    assert err.mission_id == str(mission_id)
    assert err.context == {"attempt": 2}
    assert err.__cause__ is cause


def test_raise_from_preserves_chain():
    with pytest.raises(exc_module.RetryExhaustedError) as excinfo:
        try:
            raise ValueError("inner failure")
        except ValueError as inner:
            raise exc_module.RetryExhaustedError("gave up retrying") from inner
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_to_error_report_defaults_agent_id_to_orchestrator():
    err = exc_module.MissionError("mission-level failure")
    report = err.to_error_report()
    assert isinstance(report, ErrorReport)
    assert report.agent_id == AgentID.ORCHESTRATOR
    assert report.code == "mission_error"
    assert report.message == "mission-level failure"
    assert report.retryable is False


def test_to_error_report_stringifies_context_values():
    err = exc_module.SchemaValidationError(
        "bad payload",
        context={"error_count": 3, "model": "Mission"},
    )
    report = err.to_error_report()
    assert report.context == {"error_count": "3", "model": "Mission"}


def test_security_error_defaults_to_critical_and_not_retryable():
    err = exc_module.SecurityError("path traversal attempt blocked")
    assert err.severity == Severity.CRITICAL
    assert err.retryable is False
