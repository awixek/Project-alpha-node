"""AN-17 mission-state coordination and the single mutation boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading
from typing import Any
from uuid import UUID

from shared.constants import AgentID, LogCategory, MissionStatus, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import MissionError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import ErrorReport, MissionState
from shared.validators import MissionValidator, WorkflowValidator

from .interfaces import MissionStateStore

_UNSET = object()


class StateManagerError(MissionError):
    """State-manager operation could not be completed safely."""

    default_code = "state_manager_error"


@dataclass(frozen=True)
class StateTransitionRecord:
    """Immutable execution-history record for one successful state update."""

    mission_id: UUID
    previous_status: MissionStatus
    previous_stage: WorkflowStage
    status: MissionStatus
    stage: WorkflowStage
    current_agent: AgentID | None
    completed_agents: tuple[AgentID, ...]
    failed_agents: tuple[AgentID, ...]
    reason: str
    occurred_at: datetime


class StateManager:
    """Thread-safe, validated MissionState mutation boundary."""

    def __init__(
        self,
        *,
        state_store: MissionStateStore,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._state_store = state_store
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.ORCHESTRATOR)
        self._lock = threading.RLock()
        self._failed_agents: dict[UUID, set[AgentID]] = {}
        self._history: dict[UUID, list[StateTransitionRecord]] = {}

    def get_state(self, mission_id: UUID) -> MissionState | None:
        with self._lock:
            return self._state_store.get(mission_id)

    def transition(
        self,
        mission_id: UUID,
        *,
        status: MissionStatus | None = None,
        stage: WorkflowStage | None = None,
        current_agent: AgentID | None | object = _UNSET,
        completed_agent: AgentID | None = None,
        failed_agent: AgentID | None = None,
        last_error: ErrorReport | None | object = _UNSET,
        artifact_ids: dict[str, UUID] | None = None,
        reason: str = "state_transition",
    ) -> MissionState:
        """Validate, persist and record one atomic mission-state transition."""
        reason = reason.strip() or "state_transition"
        with self._lock:
            current = self._state_store.get(mission_id)
            if current is None:
                raise StateManagerError(
                    "Cannot transition state for an unknown mission.",
                    agent_id=AgentID.ORCHESTRATOR,
                    mission_id=mission_id,
                    context={"operation": "transition"},
                )

            target_stage = stage if stage is not None else current.stage
            if target_stage is not current.stage:
                WorkflowValidator.validate_transition(current.stage, target_stage)

            target_status = status if status is not None else current.status
            target_current_agent = current.current_agent if current_agent is _UNSET else current_agent
            target_error = current.last_error if last_error is _UNSET else last_error

            completed = list(current.completed_agents)
            failed = set(self._failed_agents.get(mission_id, set()))
            if completed_agent is not None:
                if completed_agent not in completed:
                    completed.append(completed_agent)
                failed.discard(completed_agent)
            if failed_agent is not None:
                failed.add(failed_agent)
                completed = [agent for agent in completed if agent != failed_agent]

            update: dict[str, Any] = {
                "status": target_status,
                "stage": target_stage,
                "current_agent": target_current_agent,
                "completed_agents": completed,
                "last_error": target_error,
                "updated_at": datetime.now(timezone.utc),
            }
            if artifact_ids is not None:
                update["artifact_ids"] = dict(artifact_ids)

            updated = current.model_copy(update=update)
            MissionValidator.validate_mission_state(updated)
            self._state_store.update(updated)
            self._failed_agents[mission_id] = failed
            self._history.setdefault(mission_id, []).append(
                StateTransitionRecord(
                    mission_id=mission_id,
                    previous_status=current.status,
                    previous_stage=current.stage,
                    status=updated.status,
                    stage=updated.stage,
                    current_agent=updated.current_agent,
                    completed_agents=tuple(updated.completed_agents),
                    failed_agents=tuple(sorted(failed, key=lambda agent: agent.value)),
                    reason=reason,
                    occurred_at=updated.updated_at,
                )
            )
            self._logger.info(
                "Mission state transition persisted.",
                category=LogCategory.WORKFLOW,
                mission_id=mission_id,
                agent_id=updated.current_agent or AgentID.ORCHESTRATOR,
                workflow_stage=updated.stage,
                metadata={
                    "previous_status": current.status.value,
                    "status": updated.status.value,
                    "previous_stage": current.stage.value,
                    "stage": updated.stage.value,
                    "reason": reason,
                },
            )
            return updated

    def mark_agent_completed(self, mission_id: UUID, agent_id: AgentID, *, reason: str = "agent_completed") -> MissionState:
        return self.transition(
            mission_id,
            current_agent=None,
            completed_agent=agent_id,
            last_error=None,
            reason=reason,
        )

    def mark_agent_failed(self, mission_id: UUID, agent_id: AgentID, error: ErrorReport, *, reason: str = "agent_failed") -> MissionState:
        return self.transition(
            mission_id,
            failed_agent=agent_id,
            last_error=error,
            current_agent=None,
            reason=reason,
        )

    def get_failed_agents(self, mission_id: UUID) -> frozenset[AgentID]:
        with self._lock:
            return frozenset(self._failed_agents.get(mission_id, set()))

    def get_history(self, mission_id: UUID) -> tuple[StateTransitionRecord, ...]:
        with self._lock:
            return tuple(self._history.get(mission_id, ()))
