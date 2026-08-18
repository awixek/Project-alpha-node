"""
shared/api_router.py

Project Alpha Node — Centralized API Routing Framework
============================================================

Routes a request to one of several registered AI providers, in priority
order, with automatic fallback when a provider is unhealthy or fails.
This module knows nothing about any specific vendor — it defines only
the AIProvider interface every concrete provider adapter implements
elsewhere. Replacing a provider means registering a different
AIProvider instance; this file never changes (fulfills the "No Vendor
Lock-In" foundation rule).

Design rules enforced in this file:
    * Provider-agnostic: no OpenAI/Anthropic/ElevenLabs-specific code
      lives here, only the abstract AIProvider interface.
    * Each provider attempt goes through shared.retry.RetryExecutor
      (retry integration) before the router marks that provider
      unhealthy and falls back to the next one.
    * Every call outcome is logged via shared.logger under
      LogCategory.API (logging integration).
    * Provider health is tracked in-memory and updated after every call,
      so a provider that's currently failing sorts behind healthy ones
      without being permanently removed (it gets a chance to recover).
    * Future provider plugins: registering a new AIProvider subclass is
      the entire integration surface — no router changes required.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Generic, TypeVar

from shared.config import get_config
from shared.constants import LogCategory
from shared.exceptions import AllProvidersFailedError, ProviderUnavailableError
from shared.logger import AlphaLogger, get_logger
from shared.retry import RetryExecutor, RetryPolicy

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")

_logger: AlphaLogger = get_logger("api_router", category=LogCategory.API)


# ──────────────────────────────────────────────────────────────────────────
# Provider interface (the only vendor-facing surface)
# ──────────────────────────────────────────────────────────────────────────

class AIProvider(ABC, Generic[RequestT, ResponseT]):
    """
    Interface every concrete AI provider adapter must implement.
    Concrete adapters (e.g. an OpenAIScriptProvider, an
    ElevenLabsVoiceProvider) live in agent-specific modules, not here.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable, unique identifier for this provider, e.g. 'openai-gpt'."""
        raise NotImplementedError

    @abstractmethod
    def call(self, request: RequestT) -> ResponseT:
        """Performs the actual provider call. May raise any exception;
        the router treats any exception as a failed attempt."""
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────
# Health tracking
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderHealth:
    """
    Mutable, in-memory health record for one registered provider.

    `unhealthy_threshold` is supplied by the owning APIRouter at
    registration time (sourced from shared.config.APIConfig, not
    hardcoded here — see APIRouter.__init__).
    """

    unhealthy_threshold: int
    consecutive_failures: int = 0
    last_error: str | None = None
    last_checked_at: datetime | None = None
    is_healthy: bool = True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_error = None
        self.last_checked_at = datetime.now(timezone.utc)
        self.is_healthy = True

    def record_failure(self, error: str) -> None:
        self.consecutive_failures += 1
        self.last_error = error
        self.last_checked_at = datetime.now(timezone.utc)
        self.is_healthy = self.consecutive_failures < self.unhealthy_threshold


@dataclass
class ProviderRegistration(Generic[RequestT, ResponseT]):
    """One provider's registration entry: the provider itself, its
    priority (lower = tried first), and its current health."""

    provider: AIProvider[RequestT, ResponseT]
    priority: int
    health: ProviderHealth


# ──────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────

class APIRouter(Generic[RequestT, ResponseT]):
    """
    Routes a request to registered providers in priority order, retrying
    each one via shared.retry before falling back to the next. One
    APIRouter instance should be created per *kind* of provider (e.g.
    one for script-generation providers, one for voice providers) —
    routers are not shared across unrelated provider types.
    """

    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        unhealthy_failure_threshold: int | None = None,
    ) -> None:
        self._providers: dict[str, ProviderRegistration[RequestT, ResponseT]] = {}
        self._lock = threading.Lock()
        self._retry_policy = retry_policy
        # Config-driven default (shared.config.APIConfig.unhealthy_failure_threshold),
        # overridable per-router by callers that genuinely need a different
        # value — never hardcoded here.
        self._unhealthy_threshold = (
            unhealthy_failure_threshold
            if unhealthy_failure_threshold is not None
            else get_config().api.unhealthy_failure_threshold
        )

    # -- registration -------------------------------------------------------

    def register_provider(self, provider: AIProvider[RequestT, ResponseT], *, priority: int = 10) -> None:
        with self._lock:
            self._providers[provider.name] = ProviderRegistration(
                provider=provider,
                priority=priority,
                health=ProviderHealth(unhealthy_threshold=self._unhealthy_threshold),
            )
        _logger.info(f"Registered provider '{provider.name}' at priority {priority}.")

    def unregister_provider(self, name: str) -> None:
        with self._lock:
            self._providers.pop(name, None)
        _logger.info(f"Unregistered provider '{name}'.")

    def set_primary(self, name: str) -> None:
        """Reassigns `name` to the lowest priority value among registered
        providers (minus one), so it is always tried first."""
        with self._lock:
            if name not in self._providers:
                raise ProviderUnavailableError(f"Cannot set primary: provider '{name}' is not registered.")
            lowest = min((reg.priority for reg in self._providers.values()), default=0)
            self._providers[name].priority = lowest - 1

    def get_health(self, name: str) -> ProviderHealth:
        with self._lock:
            if name not in self._providers:
                raise ProviderUnavailableError(f"Unknown provider: '{name}'.")
            return self._providers[name].health

    # -- calling --------------------------------------------------------------

    def call(
        self,
        request: RequestT,
        *,
        exclude: set[str] | None = None,
        retry_if: Callable[[BaseException], bool] | None = None,
    ) -> ResponseT:
        """
        Tries each registered provider in priority order (healthy
        providers first, then unhealthy ones as a last resort), retrying
        each individual attempt per this router's retry policy.

        Raises:
            AllProvidersFailedError: if every provider fails, or no
                provider is registered at all.
        """
        ordered = self._ordered_providers(exclude=exclude or set())
        if not ordered:
            raise AllProvidersFailedError(
                "No providers available to handle this request.",
                context={"excluded": sorted(exclude or set())},
            )

        failures: dict[str, str] = {}
        executor = RetryExecutor(self._retry_policy)

        for registration in ordered:
            provider = registration.provider
            try:
                response = executor.execute(
                    lambda: provider.call(request),
                    retry_if=retry_if,
                    context={"provider": provider.name},
                )
                registration.health.record_success()
                _logger.info(
                    f"Provider '{provider.name}' handled request successfully.",
                    metadata={"provider": provider.name},
                )
                return response
            except Exception as exc:  # noqa: BLE001 — this is exactly the fallback boundary
                registration.health.record_failure(str(exc))
                failures[provider.name] = str(exc)
                _logger.warning(
                    f"Provider '{provider.name}' failed, trying next provider if available: {exc}",
                    metadata={"provider": provider.name, "healthy": registration.health.is_healthy},
                )

        _logger.error(
            "All providers failed for this request.",
            metadata={"failures": failures},
        )
        raise AllProvidersFailedError(
            f"All {len(ordered)} provider(s) failed to handle the request.",
            context={"failures": failures},
        )

    # -- internals ------------------------------------------------------------

    def _ordered_providers(self, *, exclude: set[str]) -> list[ProviderRegistration[RequestT, ResponseT]]:
        with self._lock:
            candidates = [
                reg for name, reg in self._providers.items() if name not in exclude
            ]
        # Healthy providers first (by priority), then unhealthy ones as a
        # last resort (also by priority) rather than being excluded
        # entirely — a provider that recovers should be usable again
        # without a manual re-registration.
        return sorted(candidates, key=lambda reg: (not reg.health.is_healthy, reg.priority))


__all__ = [
    "AIProvider",
    "ProviderHealth",
    "ProviderRegistration",
    "APIRouter",
]
