"""
tests/test_event_bus.py

Purpose
-------
shared/event_bus.py is the only channel through which agents are
allowed to communicate (per the frozen "agents talk only through
shared infra" architecture rule). The smoke-test bar: the module
imports, subscribe/publish/unsubscribe work end to end, wildcard
subscribers receive every event, a handler that raises never breaks
delivery to other handlers, both sync and async handlers are
delivered, and emit()/subscriber_count()/get_event_bus() behave as
documented.

Strategy
--------
* subscribe() then publish() delivers to exactly the matching handler;
  publish() returns the count of successfully invoked handlers.
* A wildcard subscription additionally receives every event type.
* unsubscribe() removes a handler so it stops receiving events.
* A handler that raises does not prevent delivery to a second handler
  for the same event (isolation guarantee).
* An async handler is invoked and actually runs (via the no-running-
  loop asyncio.run() path in InMemoryEventTransport).
* emit() builds and publishes a WorkflowEvent in one call.
* publish() with an empty event_type raises EventBusError.
* get_event_bus() returns the same process-wide instance every call.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.constants import AgentID, EventName
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import EventBusError
from shared.schemas import WorkflowEvent


def _make_event(event_type: str = EventName.MISSION_CREATED.value) -> WorkflowEvent:
    return WorkflowEvent(mission_id=uuid4(), agent_id=AgentID.ORCHESTRATOR, event_type=event_type)


def test_subscribe_and_publish_delivers_to_matching_handler():
    bus = EventBus()
    received = []
    bus.subscribe(EventName.MISSION_CREATED, lambda evt: received.append(evt))

    delivered = bus.publish(_make_event(EventName.MISSION_CREATED.value))

    assert delivered == 1
    assert len(received) == 1


def test_publish_does_not_deliver_to_subscribers_of_other_event_types():
    bus = EventBus()
    received = []
    bus.subscribe(EventName.MISSION_FAILED, lambda evt: received.append(evt))

    bus.publish(_make_event(EventName.MISSION_CREATED.value))

    assert received == []


def test_wildcard_subscriber_receives_every_event_type():
    from shared.event_bus import WILDCARD_EVENT_TYPE

    bus = EventBus()
    received = []
    bus.subscribe(WILDCARD_EVENT_TYPE, lambda evt: received.append(evt))

    bus.publish(_make_event(EventName.MISSION_CREATED.value))
    bus.publish(_make_event(EventName.AGENT_STARTED.value))

    assert len(received) == 2


def test_unsubscribe_stops_further_delivery():
    bus = EventBus()
    received = []
    sub_id = bus.subscribe(EventName.MISSION_CREATED, lambda evt: received.append(evt))

    removed = bus.unsubscribe(sub_id)
    bus.publish(_make_event(EventName.MISSION_CREATED.value))

    assert removed is True
    assert received == []


def test_unsubscribe_unknown_id_returns_false():
    bus = EventBus()
    assert bus.unsubscribe(uuid4()) is False


def test_one_raising_handler_does_not_block_another():
    bus = EventBus()
    received = []

    def bad_handler(evt):
        raise RuntimeError("handler blew up")

    def good_handler(evt):
        received.append(evt)

    bus.subscribe(EventName.MISSION_CREATED, bad_handler)
    bus.subscribe(EventName.MISSION_CREATED, good_handler)

    delivered = bus.publish(_make_event(EventName.MISSION_CREATED.value))

    assert delivered == 1  # only the good handler counted as successful
    assert len(received) == 1


def test_async_handler_is_invoked():
    bus = EventBus()
    received = []

    async def async_handler(evt):
        received.append(evt)

    bus.subscribe(EventName.MISSION_CREATED, async_handler)
    delivered = bus.publish(_make_event(EventName.MISSION_CREATED.value))

    assert delivered == 1
    assert len(received) == 1


def test_emit_builds_and_publishes_in_one_call():
    bus = EventBus()
    received = []
    bus.subscribe(EventName.AGENT_COMPLETED, lambda evt: received.append(evt))

    event = bus.emit(EventName.AGENT_COMPLETED, agent_id=AgentID.SCRIPT_FORGE, payload={"note": "done"})

    assert isinstance(event, WorkflowEvent)
    assert event.event_type == EventName.AGENT_COMPLETED.value
    assert len(received) == 1


def test_publish_with_empty_event_type_raises():
    bus = EventBus()
    with pytest.raises(EventBusError):
        bus.publish(WorkflowEvent(event_type=" ".strip()))


def test_subscriber_count_reflects_current_subscriptions():
    bus = EventBus()
    assert bus.subscriber_count(EventName.MISSION_CREATED) == 0
    bus.subscribe(EventName.MISSION_CREATED, lambda evt: None)
    bus.subscribe(EventName.MISSION_CREATED, lambda evt: None)
    assert bus.subscriber_count(EventName.MISSION_CREATED) == 2


def test_get_event_bus_returns_process_wide_singleton():
    first = get_event_bus()
    second = get_event_bus()
    assert first is second
