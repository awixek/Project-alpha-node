"""AN-17 mission lifecycle initialization."""

from __future__ import annotations

import threading
from uuid import UUID

from shared.constants import AgentID, LogCategory, MissionStatus, WorkflowStage
from shared.exceptions import AlphaBaseException, MissionError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import Mission, MissionState
from shared.validators import MissionValidator

from .interfaces import MissionStateStore


class _InMemoryMissionStateStore:
    """Thread-safe in-memory implementation of the MissionStateStore contract."""

    def __init__(self) -> None:
        self._states: dict[UUID, MissionState] = {}
        self._lock = threading.RLock()

    def create(self, state: MissionState) -> MissionState:
        with self._lock:
            if state.mission_id in self._states:
                raise MissionError(
                    "Mission state already exists.",
                    agent_id=AgentID.ORCHESTRATOR,
                    mission_id=state.mission_id,
                    context={"operation": "create_mission_state"},
                )
            self._states[state.mission_id] = state
            return state

    def get(self, mission_id: UUID) -> MissionState | None:
        with self._lock:
            return self._states.get(mission_id)

    def update(self, state: MissionState) -> None:
        with self._lock:
            if state.mission_id not in self._states:
                raise MissionError(
                    "Mission state does not exist.",
                    agent_id=AgentID.ORCHESTRATOR,
                    mission_id=state.mission_id,
                    context={"operation": "update_mission_state"},
                )
            self._states[state.mission_id] = state


class MissionManager:
    """Validates missions and initializes their lifecycle state."""

    def __init__(
        self,
        *,
        state_store: MissionStateStore | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._state_store = state_store or _InMemoryMissionStateStore()
        self._logger = logger or get_agent_logger(AgentID.ORCHESTRATOR)

    @property
    def state_store(self) -> MissionStateStore:
        """Return the persistence boundary shared by AN-17 components."""
        return self._state_store

    def accept(self, mission: Mission) -> MissionState:
        """Validate a mission and create its initial ``MissionState``."""
        try:
            validated_mission = MissionValidator.validate_mission(mission)
            state = MissionState(
                mission_id=validated_mission.mission_id,
                status=MissionStatus.PENDING,
                stage=WorkflowStage.MISSION_CREATED,
            )
            MissionValidator.validate_mission_state(state)
            created_state = self._state_store.create(state)
            self._logger.info(
                "Mission accepted and initial state created.",
                category=LogCategory.MISSION,
                mission_id=mission.mission_id,
                metadata={"priority": mission.priority},
            )
            return created_state
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - boundary converts unexpected failures
            self._logger.exception(
                "Unexpected failure while accepting mission.",
                category=LogCategory.MISSION,
                mission_id=mission.mission_id,
            )
            raise MissionError(
                "Mission acceptance failed.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission.mission_id,
                context={"operation": "accept"},
                cause=exc,
            ) from exc

    def get_state(self, mission_id: UUID) -> MissionState | None:
        """Return current state for a mission, if it has been accepted."""
        return self._state_store.get(mission_id)
