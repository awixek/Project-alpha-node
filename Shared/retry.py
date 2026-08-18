"""
shared/retry.py

Project Alpha Node — Universal Retry Engine
================================================

Wraps any callable — sync or async — with configurable exponential
backoff. This module contains zero business logic: it has no concept of
"mission" or "agent," only "callable, policy, and which exceptions
count as retryable." Every agent that calls an external API should run
that call through this engine instead of hand-rolling its own retry loop.

Design rules enforced in this file:
    * No hardcoded retry numbers — defaults come from
      shared.config.RetryConfig; callers may override per-call.
    * Logging integration: every retry attempt and every exhaustion is
      logged via shared.logger under LogCategory.RETRY, matching
      constants.EventName's retry.started / retry.completed vocabulary
      (actual event *publishing* is shared/event_bus.py's job — this
      module only logs, it does not publish).
    * Exception integration: exhaustion always raises
      shared.exceptions.RetryExhaustedError, wrapping the last failure.
    * Sync and async paths share identical policy/backoff/logging logic
      — duplicated only where sync/async control flow genuinely differs
      (time.sleep vs. asyncio.sleep).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, Mapping, TypeVar

from shared.config import get_config
from shared.constants import LogCategory
from shared.exceptions import RetryExhaustedError
from shared.logger import AlphaLogger, get_logger

T = TypeVar("T")

_DEFAULT_LOGGER: AlphaLogger = get_logger("retry_engine", category=LogCategory.RETRY)


# ──────────────────────────────────────────────────────────────────────────
# Policy
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetryPolicy:
    """
    Immutable retry policy. Construct via `RetryPolicy.from_config()` to
    pick up platform defaults (Retry.MAX_ATTEMPTS etc., via
    shared.config.RetryConfig) rather than hardcoding numbers at call
    sites.
    """

    max_attempts: int
    delay_seconds: float
    backoff_multiplier: float
    timeout_seconds: float
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,)

    @classmethod
    def from_config(
        cls, *, retry_exceptions: tuple[type[BaseException], ...] = (Exception,)
    ) -> "RetryPolicy":
        retry_config = get_config().retry
        return cls(
            max_attempts=retry_config.max_attempts,
            delay_seconds=retry_config.delay_seconds,
            backoff_multiplier=retry_config.backoff_multiplier,
            timeout_seconds=retry_config.timeout_seconds,
            retry_exceptions=retry_exceptions,
        )

    def delay_for_attempt(self, attempt_number: int) -> float:
        """attempt_number is 1-indexed: the delay *before* this attempt."""
        return self.delay_seconds * (self.backoff_multiplier ** max(attempt_number - 1, 0))


@dataclass(frozen=True)
class RetryResult(Generic[T]):
    """Returned by execute_with_result() for callers that want attempt
    metadata alongside the value (mirrors schemas.AgentResult.retry_count)."""

    value: T
    attempts_used: int
    total_elapsed_seconds: float


# ──────────────────────────────────────────────────────────────────────────
# Executor
# ──────────────────────────────────────────────────────────────────────────

class RetryExecutor:
    """
    Executes a callable under a RetryPolicy, sleeping with exponential
    backoff between attempts and giving up once max_attempts or
    timeout_seconds is exceeded — whichever comes first.

    NOTE ON TIMEOUT: `timeout_seconds` bounds total *retry* time (it
    stops a new attempt from starting once exceeded); it does not
    interrupt a single call already in flight. True per-call
    cancellation for a blocking sync call isn't possible without
    threads/signals, and is out of scope for this generic engine —
    callers needing hard per-call timeouts should make the wrapped
    callable itself timeout-aware (e.g. an HTTP client's own timeout).
    """

    def __init__(self, policy: RetryPolicy | None = None, *, logger: AlphaLogger | None = None) -> None:
        self._policy = policy or RetryPolicy.from_config()
        self._logger = logger or _DEFAULT_LOGGER

    # -- sync -------------------------------------------------------------

    def execute(
        self,
        func: Callable[[], T],
        *,
        retry_if: Callable[[BaseException], bool] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> T:
        return self.execute_with_result(func, retry_if=retry_if, context=context).value

    def execute_with_result(
        self,
        func: Callable[[], T],
        *,
        retry_if: Callable[[BaseException], bool] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> RetryResult[T]:
        start = time.perf_counter()
        last_exc: BaseException | None = None
        ctx = dict(context) if context else {}

        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                value = func()
                elapsed = time.perf_counter() - start
                if attempt > 1:
                    self._logger.info(
                        "Retry succeeded",
                        metadata={**ctx, "attempt": attempt, "elapsed_seconds": elapsed},
                    )
                return RetryResult(value=value, attempts_used=attempt, total_elapsed_seconds=elapsed)
            except BaseException as exc:  # noqa: BLE001 — filtered below via retry_if/policy
                last_exc = exc
                if not self._should_retry(exc, retry_if):
                    raise

                elapsed = time.perf_counter() - start
                if attempt >= self._policy.max_attempts or elapsed >= self._policy.timeout_seconds:
                    break

                delay = self._policy.delay_for_attempt(attempt)
                self._logger.warning(
                    f"Attempt {attempt} failed, retrying in {delay:.1f}s: {exc}",
                    metadata={**ctx, "attempt": attempt, "exception": type(exc).__name__},
                )
                time.sleep(delay)

        elapsed = time.perf_counter() - start
        self._logger.error(
            f"Retry exhausted after {self._policy.max_attempts} attempts: {last_exc}",
            metadata={**ctx, "elapsed_seconds": elapsed},
            exc_info=True,
        )
        raise RetryExhaustedError(
            f"Operation failed after {self._policy.max_attempts} attempts.",
            context={**ctx, "attempts": self._policy.max_attempts, "elapsed_seconds": elapsed},
            cause=last_exc,
        )

    # -- async --------------------------------------------------------------

    async def execute_async(
        self,
        coro_func: Callable[[], Awaitable[T]],
        *,
        retry_if: Callable[[BaseException], bool] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> T:
        start = time.perf_counter()
        last_exc: BaseException | None = None
        ctx = dict(context) if context else {}

        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                value = await coro_func()
                if attempt > 1:
                    elapsed = time.perf_counter() - start
                    self._logger.info(
                        "Async retry succeeded",
                        metadata={**ctx, "attempt": attempt, "elapsed_seconds": elapsed},
                    )
                return value
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if not self._should_retry(exc, retry_if):
                    raise

                elapsed = time.perf_counter() - start
                if attempt >= self._policy.max_attempts or elapsed >= self._policy.timeout_seconds:
                    break

                delay = self._policy.delay_for_attempt(attempt)
                self._logger.warning(
                    f"Async attempt {attempt} failed, retrying in {delay:.1f}s: {exc}",
                    metadata={**ctx, "attempt": attempt, "exception": type(exc).__name__},
                )
                await asyncio.sleep(delay)

        elapsed = time.perf_counter() - start
        self._logger.error(
            f"Async retry exhausted after {self._policy.max_attempts} attempts: {last_exc}",
            metadata={**ctx, "elapsed_seconds": elapsed},
            exc_info=True,
        )
        raise RetryExhaustedError(
            f"Async operation failed after {self._policy.max_attempts} attempts.",
            context={**ctx, "attempts": self._policy.max_attempts, "elapsed_seconds": elapsed},
            cause=last_exc,
        )

    # -- shared -------------------------------------------------------------

    def _should_retry(self, exc: BaseException, retry_if: Callable[[BaseException], bool] | None) -> bool:
        if retry_if is not None:
            return retry_if(exc)
        return isinstance(exc, self._policy.retry_exceptions)


# ──────────────────────────────────────────────────────────────────────────
# Decorators
# ──────────────────────────────────────────────────────────────────────────

def with_retry(
    policy: RetryPolicy | None = None,
    *,
    retry_if: Callable[[BaseException], bool] | None = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form of RetryExecutor.execute for ordinary sync functions."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            executor = RetryExecutor(policy)
            return executor.execute(lambda: func(*args, **kwargs), retry_if=retry_if)

        wrapper.__name__ = getattr(func, "__name__", "wrapped")
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


def with_async_retry(
    policy: RetryPolicy | None = None,
    *,
    retry_if: Callable[[BaseException], bool] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator form of RetryExecutor.execute_async for async functions."""

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            executor = RetryExecutor(policy)
            return await executor.execute_async(lambda: func(*args, **kwargs), retry_if=retry_if)

        wrapper.__name__ = getattr(func, "__name__", "wrapped")
        wrapper.__doc__ = func.__doc__
        return wrapper

    return decorator


__all__ = [
    "RetryPolicy",
    "RetryResult",
    "RetryExecutor",
    "with_retry",
    "with_async_retry",
]
