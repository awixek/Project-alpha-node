"""AN-17 interfaces.

These protocols define replaceable boundaries for mission-state storage and
future workflow execution. They deliberately depend only on shared schemas
and standard typing primitives, keeping AN-17 provider-independent.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from shared.schemas import Mission, MissionState


class MissionStateStore(Protocol):
    """Persistence boundary for mission state.

    Milestone 1 uses an in-memory implementation inside MissionManager.
    A later persistence adapter can implement this protocol without changing
    the orchestrator's public contract.
    """

    def create(self, state: MissionState) -> MissionState:
        """Persist a newly initialized mission state."""
        ...

    def get(self, mission_id: UUID) -> MissionState | None:
        """Return mission state when present, otherwise ``None``."""
        ...

    def update(self, state: MissionState) -> None:
        """Persist an updated mission state."""
        ...


class WorkflowExecutor(Protocol):
    """Future workflow execution boundary.

    Milestone 1 intentionally does not execute agents. This interface exists
    only so orchestration can remain decoupled from future execution engines.
    """

    def prepare(self, mission: Mission, state: MissionState) -> None:
        """Prepare a mission for future workflow execution."""
        ...
