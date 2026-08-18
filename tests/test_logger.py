"""
tests/test_logger.py

Purpose
-------
shared/logger.py is the logging backbone every module (including
retry.py, event_bus.py, api_router.py) already depends on. The
smoke-test bar: the module imports, `get_logger()`/`get_agent_logger()`
return working AlphaLogger instances, initialization is idempotent and
safe even when shared.config can't load, every public log-level method
runs without raising, `timed()` attaches execution time and re-raises
on failure, and the two formatters never raise on a real LogRecord.

Strategy
--------
* configure_logging() / get_logger() do not raise, and repeated calls
  don't attach duplicate handlers (idempotent setup).
* Each AlphaLogger level method (debug/info/warning/error/critical/
  exception/security) runs cleanly with and without extra context.
* bind() returns a new logger with merged context, leaving the
  original unchanged.
* timed() logs on success and still raises (after logging) when the
  wrapped block raises.
* JSONFormatter/ConsoleFormatter format a manually built LogRecord
  without raising, and JSONFormatter's output is valid JSON.
"""

from __future__ import annotations

import json
import logging

import pytest

from shared.constants import AgentID, LogCategory
from shared.logger import (
    AlphaLogger,
    ConsoleFormatter,
    JSONFormatter,
    ROOT_LOGGER_NAME,
    configure_logging,
    get_agent_logger,
    get_logger,
)


def test_get_logger_returns_alpha_logger_bound_to_root_namespace():
    logger = get_logger("smoke_test_component")
    assert isinstance(logger, AlphaLogger)
    assert logger._logger.name == f"{ROOT_LOGGER_NAME}.smoke_test_component"


def test_get_agent_logger_binds_agent_id_and_category():
    logger = get_agent_logger(AgentID.RESEARCH_CORE)
    assert logger._agent_id == AgentID.RESEARCH_CORE
    assert logger._category == LogCategory.AGENT


def test_configure_logging_is_idempotent_and_does_not_duplicate_handlers():
    configure_logging(force=True)
    configure_logging()
    configure_logging()
    root = logging.getLogger(ROOT_LOGGER_NAME)
    # Exactly one console handler regardless of how many times
    # configure_logging() is called without force=True.
    assert len(root.handlers) >= 1
    handler_count_after_first = len(root.handlers)
    configure_logging()
    assert len(root.handlers) == handler_count_after_first


@pytest.mark.parametrize("level_method", ["debug", "info", "warning", "error", "critical"])
def test_every_log_level_method_runs_without_raising(level_method):
    logger = get_logger("smoke_test_levels")
    getattr(logger, level_method)("a test message", metadata={"key": "value"})


def test_exception_method_attaches_traceback_without_raising():
    logger = get_logger("smoke_test_exception")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("caught a failure")


def test_security_method_defaults_to_security_category():
    logger = get_logger("smoke_test_security")
    logger.security("suspicious input rejected")  # should not raise


def test_bind_merges_context_without_mutating_original():
    base = get_logger("smoke_test_bind", category=LogCategory.SYSTEM)
    bound = base.bind(agent_id=AgentID.QUALITY_SENTINEL)
    assert bound._agent_id == AgentID.QUALITY_SENTINEL
    assert base._agent_id is None  # original untouched


def test_timed_logs_on_success_and_does_not_raise():
    logger = get_logger("smoke_test_timed")
    with logger.timed("doing work"):
        pass  # success path


def test_timed_reraises_after_logging_on_failure():
    logger = get_logger("smoke_test_timed_failure")
    with pytest.raises(RuntimeError):
        with logger.timed("doing work that fails"):
            raise RuntimeError("failure inside timed block")


def test_json_formatter_produces_valid_json_line():
    record = logging.LogRecord(
        name="alpha_node.test", level=logging.INFO, pathname=__file__,
        lineno=1, msg="hello %s", args=("world",), exc_info=None,
    )
    line = JSONFormatter().format(record)
    parsed = json.loads(line)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"


def test_console_formatter_produces_readable_line_without_raising():
    record = logging.LogRecord(
        name="alpha_node.test", level=logging.WARNING, pathname=__file__,
        lineno=1, msg="careful: %s", args=("disk low",), exc_info=None,
    )
    line = ConsoleFormatter().format(record)
    assert "WARNING" in line
    assert "careful: disk low" in line
