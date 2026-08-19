from __future__ import annotations

from typing import Callable, TypeVar

from shared.exceptions import AlphaBaseException, RetryExhaustedError

T = TypeVar("T")


class PublishRetryManager:
    """Delegates retry execution to the frozen shared RetryExecutor."""

    def __init__(self, retry_executor) -> None:
        self._retry = retry_executor

    def execute(self, operation: Callable[[], T], *, retryable: Callable[[BaseException], bool], context: dict[str, str]) -> tuple[T, int]:
        result = self._retry.execute_with_result(operation, retry_if=retryable, context=context)
        return result.value, max(0, result.attempts_used - 1)
