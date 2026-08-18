"""
shared/event_bus.py

Project Alpha Node — Centralized Event Communication System
=================================================================

Agents communicate through this event bus instead of calling each other
directly, per the frozen architecture rule that every agent talks only
through the Alpha Orchestrator / shared infrastructure. Events are
always typed as shared.schemas.WorkflowEvent.

Design rules enforced in this file:
    * Dispatch is delegated through a small EventTransport interface.
      The default is in-process (InMemoryEventTransport); a future
      distributed transport (Redis pub/sub, Kafka, etc.) is a new class
      implementing the same interface — EventBus itself never changes.
    * Thread-safe: the subscriber registry is guarded by a lock. Publish
      takes a snapshot of matching subscribers before invoking them, so
      a handler that subscribes/unsubscribes mid-dispatch can't corrupt
      the registry it's iterating.
    * One bad subscriber never breaks another: each handler invocation
      is individually try/excepted and logged; publish() always finishes
      dispatching to every matching subscriber.
    * Both sync and async handlers are supported, for future async
      agents — a coroutine-function handler is scheduled via asyncio,
      a plain callable is invoked directly.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID, uuid4

from shared.constants import EventName, LogCategory
from shared.exceptions import EventBusError
from shared.logger import AlphaLogger, get_logger
from shared.schemas import AgentID, WorkflowEvent

EventHandler = Callable[[WorkflowEvent], None] | Callable[[WorkflowEvent], Awaitable[None]]

WILDCARD_EVENT_TYPE = "*"
"""Subscribing with this event_type receives every event — intended for
monitoring/logging subscribers, not business logic."""

_logger: AlphaLogger = get_logger("event_bus", category=LogCategory.WORKFLOW)


# ──────────────────────────────────────────────────────────────────────────
# Subscription record
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Subscription:
    """One registered (event_type, handler) pair."""

    subscription_id: UUID
    event_type: str
    handler: EventHandler
    is_async: bool


# ──────────────────────────────────────────────────────────────────────────
# Transport interface (extension point for future distributed backends)
# ──────────────────────────────────────────────────────────────────────────

class EventTransport(ABC):
    """
    Delivers an event to a set of already-resolved subscriptions.

    EventBus owns *who is subscribed to what*; a transport only owns
    *how a matched event reaches each handler*. A distributed transport
    would still receive the same resolved subscription list from
    EventBus for local delivery, while additionally forwarding the
    event to remote nodes.
    """

    @abstractmethod
    def dispatch(self, event: WorkflowEvent, subscriptions: list[Subscription]) -> int:
        """Delivers `event` to each subscription. Returns the number of
        handlers successfully invoked (best-effort; failures are caught
        and logged, not re-raised)."""
        raise NotImplementedError


class InMemoryEventTransport(EventTransport):
    """Default transport: invokes each handler in-process. Sync handlers
    run inline; async handlers are scheduled onto a running event loop
    if one exists, otherwise run via asyncio.run()."""

    def dispatch(self, event: WorkflowEvent, subscriptions: list[Subscription]) -> int:
        delivered = 0
        for subscription in subscriptions:
            try:
                if subscription.is_async:
                    self._dispatch_async(subscription, event)
                else:
                    subscription.handler(event)  # type: ignore[arg-type]
                delivered += 1
            except Exception as exc:  # noqa: BLE001 — one bad handler must not break others
                _logger.error(
                    f"Event handler raised for event_type={subscription.event_type!r}: {exc}",
                    metadata={
                        "event_type": subscription.event_type,
                        "subscription_id": str(subscription.subscription_id),
                    },
                    exc_info=True,
                )
        return delivered

    @staticmethod
    def _dispatch_async(subscription: Subscription, event: WorkflowEvent) -> None:
        coro = subscription.handler(event)  # type: ignore[misc]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)  # type: ignore[arg-type]
        else:
            loop.create_task(coro)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# Event bus
# ──────────────────────────────────────────────────────────────────────────

class EventBus:
    """
    The single event bus every agent publishes to and subscribes
    through. Obtain the process-wide instance via get_event_bus() below
    rather than constructing this directly, so all agents share one
    subscriber registry.
    """

    def __init__(self, *, transport: EventTransport | None = None) -> None:
        self._transport = transport or InMemoryEventTransport()
        self._subscriptions: dict[str, list[Subscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: EventName | str, handler: EventHandler) -> UUID:
        """Registers `handler` for `event_type` (or WILDCARD_EVENT_TYPE for
        every event). Returns a subscription_id usable with unsubscribe()."""
        type_value = event_type.value if isinstance(event_type, EventName) else event_type
        subscription = Subscription(
            subscription_id=uuid4(),
            event_type=type_value,
            handler=handler,
            is_async=inspect.iscoroutinefunction(handler),
        )
        with self._lock:
            self._subscriptions.setdefault(type_value, []).append(subscription)
        return subscription.subscription_id

    def unsubscribe(self, subscription_id: UUID) -> bool:
        """Removes a subscription by id. Returns True if it was found."""
        with self._lock:
            for handlers in self._subscriptions.values():
                for i, subscription in enumerate(handlers):
                    if subscription.subscription_id == subscription_id:
                        del handlers[i]
                        return True
        return False

    def publish(self, event: WorkflowEvent) -> int:
        """
        Dispatches `event` to every subscriber of its event_type plus
        every wildcard subscriber. Returns the number of handlers
        successfully invoked.

        Raises:
            EventBusError: if `event` is malformed (defensive — in
                practice pydantic already guarantees this at construction).
        """
        if not event.event_type:
            raise EventBusError("Cannot publish a WorkflowEvent with an empty event_type.")

        with self._lock:
            matched = list(self._subscriptions.get(event.event_type, []))
            matched += list(self._subscriptions.get(WILDCARD_EVENT_TYPE, []))

        _logger.debug(
            f"Publishing event: {event.event_type}",
            mission_id=event.mission_id,
            agent_id=event.agent_id,
            metadata={"subscriber_count": len(matched)},
        )
        return self._transport.dispatch(event, matched)

    def emit(
        self,
        event_type: EventName,
        *,
        mission_id: Any | None = None,
        agent_id: AgentID | None = None,
        payload: Mapping[str, str] | None = None,
    ) -> WorkflowEvent:
        """Convenience: builds a WorkflowEvent and publishes it in one call,
        so call sites don't need to import schemas.WorkflowEvent directly."""
        event = WorkflowEvent(
            mission_id=mission_id,
            agent_id=agent_id,
            event_type=event_type.value,
            payload=dict(payload) if payload else {},
        )
        self.publish(event)
        return event

    def subscriber_count(self, event_type: EventName | str) -> int:
        type_value = event_type.value if isinstance(event_type, EventName) else event_type
        with self._lock:
            return len(self._subscriptions.get(type_value, []))


# ──────────────────────────────────────────────────────────────────────────
# Process-wide singleton access
# ──────────────────────────────────────────────────────────────────────────

_bus_lock = threading.Lock()
_bus_instance: EventBus | None = None


def get_event_bus() -> EventBus:
    """Returns the process-wide EventBus singleton every agent shares."""
    global _bus_instance
    if _bus_instance is not None:
        return _bus_instance
    with _bus_lock:
        if _bus_instance is None:
            _bus_instance = EventBus()
        return _bus_instance


__all__ = [
    "WILDCARD_EVENT_TYPE",
    "EventHandler",
    "Subscription",
    "EventTransport",
    "InMemoryEventTransport",
    "EventBus",
    "get_event_bus",
]
