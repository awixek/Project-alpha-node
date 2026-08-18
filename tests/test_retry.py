"""
tests/test_retry.py

Purpose
-------
shared/retry.py is the universal retry engine api_router.py (and any
future agent making external calls) builds on. The smoke-test bar: the
module imports, RetryPolicy math is correct, RetryExecutor succeeds
immediately when the wrapped call succeeds, actually retries and then
succeeds on a transient failure, exhausts and raises RetryExhaustedError
with the original cause attached, respects `retry_if` filtering, and
both decorator forms and the async path work.

Strategy
--------
* RetryPolicy.delay_for_attempt: exponential backoff formula.
* RetryExecutor.execute: success on first try (no sleep involved).
* RetryExecutor.execute: fails twice then succeeds — uses a policy with
  delay_seconds=0 so the test doesn't actually sleep, and asserts the
  wrapped function was called the expected number of times.
* RetryExecutor.execute: exhausts all attempts -> RetryExhaustedError,
  with the last real exception attached as __cause__.
* retry_if filtering: a non-matching exception is raised immediately,
  without consuming further attempts.
* with_retry decorator applies the same behavior to a plain function.
* execute_async / with_async_retry: same success/retry/exhaustion shape
  on the async path, run via asyncio.run() (no pytest-asyncio needed).
"""

from __future__ import annotations

import asyncio

import pytest

from shared.exceptions import RetryExhaustedError
from shared.retry import RetryExecutor, RetryPolicy, with_async_retry, with_retry


FAST_POLICY = RetryPolicy(
    max_attempts=3, delay_seconds=0.0, backoff_multiplier=2.0, timeout_seconds=30.0
)


def test_delay_for_attempt_follows_exponential_backoff():
    policy = RetryPolicy(max_attempts=5, delay_seconds=1.0, backoff_multiplier=2.0, timeout_seconds=60.0)
    assert policy.delay_for_attempt(1) == 1.0
    assert policy.delay_for_attempt(2) == 2.0
    assert policy.delay_for_attempt(3) == 4.0


def test_execute_succeeds_on_first_attempt_without_retrying():
    calls = []

    def func():
        calls.append(1)
        return "ok"

    executor = RetryExecutor(FAST_POLICY)
    result = executor.execute_with_result(func)
    assert result.value == "ok"
    assert result.attempts_used == 1
    assert len(calls) == 1


def test_execute_retries_transient_failures_then_succeeds():
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    executor = RetryExecutor(FAST_POLICY)
    result = executor.execute_with_result(flaky)
    assert result.value == "recovered"
    assert result.attempts_used == 3
    assert calls["count"] == 3


def test_execute_raises_retry_exhausted_after_max_attempts():
    def always_fails():
        raise ConnectionError("permanent outage")

    executor = RetryExecutor(FAST_POLICY)
    with pytest.raises(RetryExhaustedError) as excinfo:
        executor.execute(always_fails)
    assert isinstance(excinfo.value.__cause__, ConnectionError)
    assert excinfo.value.context["attempts"] == FAST_POLICY.max_attempts


def test_retry_if_filters_which_exceptions_are_retried():
    calls = {"count": 0}

    def fails_with_value_error():
        calls["count"] += 1
        raise ValueError("not retryable per policy")

    executor = RetryExecutor(FAST_POLICY)
    with pytest.raises(ValueError):
        executor.execute(fails_with_value_error, retry_if=lambda exc: isinstance(exc, ConnectionError))
    assert calls["count"] == 1  # raised immediately, no retries consumed


def test_with_retry_decorator_wraps_plain_function():
    calls = {"count": 0}

    @with_retry(FAST_POLICY)
    def sometimes_fails(x):
        calls["count"] += 1
        if calls["count"] < 2:
            raise ConnectionError("transient")
        return x * 2

    assert sometimes_fails(21) == 42
    assert calls["count"] == 2


def test_execute_async_succeeds_after_retry():
    calls = {"count": 0}

    async def flaky_async():
        calls["count"] += 1
        if calls["count"] < 2:
            raise ConnectionError("transient")
        return "async-ok"

    executor = RetryExecutor(FAST_POLICY)
    result = asyncio.run(executor.execute_async(flaky_async))
    assert result == "async-ok"
    assert calls["count"] == 2


def test_with_async_retry_decorator_exhausts_and_raises():
    @with_async_retry(FAST_POLICY)
    async def always_fails_async():
        raise ConnectionError("permanent async outage")

    with pytest.raises(RetryExhaustedError):
        asyncio.run(always_fails_async())
