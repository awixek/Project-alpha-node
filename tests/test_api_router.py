"""
tests/test_api_router.py

Purpose
-------
shared/api_router.py provides the provider-agnostic fallback routing
every vendor-facing agent (Script Forge, Vision Creator, Voice Core,
...) will sit on top of. The smoke-test bar: the module imports, the
AIProvider interface can be implemented by a fake adapter, registration
and priority ordering work, a failing primary correctly falls back to
the next healthy provider, health tracking flips unhealthy at the
configured threshold and recovers on success, and AllProvidersFailedError
fires when every provider (or none) is available.

Strategy
--------
* A minimal FakeProvider implementing AIProvider; AIProvider itself
  cannot be instantiated directly (ABC).
* register_provider + call(): the single healthy provider handles the
  request.
* Fallback: a failing primary causes the router to fall through to a
  working secondary, and the response comes from the secondary.
* All providers failing raises AllProvidersFailedError with per-
  provider failure detail in context.
* No providers registered raises AllProvidersFailedError immediately.
* ProviderHealth.record_failure flips is_healthy False once
  consecutive_failures reaches unhealthy_threshold, and record_success
  resets it.
* set_primary() reorders a provider ahead of the others.
* unregister_provider() removes a provider from routing.
* get_health() raises ProviderUnavailableError for an unknown name.
"""

from __future__ import annotations

import pytest

from shared.api_router import AIProvider, APIRouter, ProviderHealth
from shared.exceptions import AllProvidersFailedError, ProviderUnavailableError
from shared.retry import RetryPolicy


NO_WAIT_POLICY = RetryPolicy(max_attempts=1, delay_seconds=0.0, backoff_multiplier=1.0, timeout_seconds=5.0)


class FakeProvider(AIProvider[str, str]):
    """Minimal AIProvider: echoes the request, or raises if configured to fail."""

    def __init__(self, provider_name: str, *, should_fail: bool = False) -> None:
        self._name = provider_name
        self.should_fail = should_fail
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def call(self, request: str) -> str:
        self.call_count += 1
        if self.should_fail:
            raise ConnectionError(f"{self._name} is down")
        return f"{self._name}:{request}"


def test_ai_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        AIProvider()  # type: ignore[abstract]


def test_single_healthy_provider_handles_request():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    provider = FakeProvider("primary")
    router.register_provider(provider, priority=1)

    response = router.call("hello")

    assert response == "primary:hello"
    assert provider.call_count == 1


def test_failing_primary_falls_back_to_secondary():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    primary = FakeProvider("primary", should_fail=True)
    secondary = FakeProvider("secondary")
    router.register_provider(primary, priority=1)
    router.register_provider(secondary, priority=2)

    response = router.call("hello")

    assert response == "secondary:hello"
    assert primary.call_count == 1
    assert secondary.call_count == 1


def test_all_providers_failing_raises_all_providers_failed():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    router.register_provider(FakeProvider("a", should_fail=True), priority=1)
    router.register_provider(FakeProvider("b", should_fail=True), priority=2)

    with pytest.raises(AllProvidersFailedError) as excinfo:
        router.call("hello")
    assert set(excinfo.value.context["failures"].keys()) == {"a", "b"}


def test_no_providers_registered_raises_all_providers_failed():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    with pytest.raises(AllProvidersFailedError):
        router.call("hello")


def test_provider_health_flips_unhealthy_at_threshold_and_recovers():
    health = ProviderHealth(unhealthy_threshold=2)
    assert health.is_healthy is True

    health.record_failure("err1")
    assert health.is_healthy is True  # 1 failure, threshold is 2

    health.record_failure("err2")
    assert health.is_healthy is False  # 2 consecutive failures hits threshold

    health.record_success()
    assert health.is_healthy is True
    assert health.consecutive_failures == 0


def test_unhealthy_provider_is_tried_last_not_excluded():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=1)
    flaky = FakeProvider("flaky", should_fail=True)
    reliable = FakeProvider("reliable")
    router.register_provider(flaky, priority=1)
    router.register_provider(reliable, priority=2)

    # First call: flaky fails once, crosses threshold=1, becomes unhealthy;
    # router falls back to reliable within the same call.
    response = router.call("first")
    assert response == "reliable:first"
    assert router.get_health("flaky").is_healthy is False

    # Second call: flaky is unhealthy but still registered, so it's tried
    # last (not excluded) — reliable (still healthy, lower sort key) goes first.
    response_2 = router.call("second")
    assert response_2 == "reliable:second"


def test_set_primary_reprioritizes_provider_ahead_of_others():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    first = FakeProvider("first")
    second = FakeProvider("second")
    router.register_provider(first, priority=1)
    router.register_provider(second, priority=5)

    router.set_primary("second")
    response = router.call("hi")

    assert response == "second:hi"
    assert second.call_count == 1
    assert first.call_count == 0


def test_set_primary_unknown_provider_raises():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    with pytest.raises(ProviderUnavailableError):
        router.set_primary("nonexistent")


def test_unregister_provider_removes_it_from_routing():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    provider = FakeProvider("solo")
    router.register_provider(provider, priority=1)
    router.unregister_provider("solo")

    with pytest.raises(AllProvidersFailedError):
        router.call("hello")


def test_get_health_unknown_provider_raises():
    router: APIRouter[str, str] = APIRouter(retry_policy=NO_WAIT_POLICY, unhealthy_failure_threshold=3)
    with pytest.raises(ProviderUnavailableError):
        router.get_health("nonexistent")
