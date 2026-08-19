"""AN-17 mission recovery coordination for Milestone 3.

RecoveryManager classifies recoverability, restores safe MissionState
snapshots, and delegates any actual resume operation to an injected callback.
It never reimplements retry/backoff policy and never communicates with an AI
provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Generic, TypeVar
from uuid import UUID

from shared.constants import AgentID, EventName, LogCategory, MissionStatus, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AlphaBaseException, MissionError, RetryExhaustedError
from shared.logger import AlphaLogger, get_agent_logger
from shared.retry import RetryExecutor, RetryResult
from shared.schemas import MissionState, WorkflowEvent

from .state_manager import StateManager

T = TypeVar("T")


class RecoveryManagerError(MissionError):
    """Recovery operation could not be completed."""

    default_code = "recovery_manager_error"


class RecoveryDisposition(str, Enum):
    """Classification of a mission's ability to resume safely."""

    RECOVERABLE = "recoverable"
    NON_RECOVERABLE = "non_recoverable"
    ALREADY_ACTIVE = "already_active"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"


@dataclass(frozen=True)
class RecoveryAssessment:
    """Immutable recovery decision for a mission snapshot."""

    mission_id: UUID
    disposition: RecoveryDisposition
    stage: WorkflowStage
    reason: str


@dataclass(frozen=True)
class RecoveryResult(Generic[T]):
    """Outcome of a recovery callback executed under the shared Retry Engine."""

    state: MissionState
    value: T
    attempts_used: int


ResumeOperation = Callable[[MissionState], T]


class RecoveryManager:
    """Thread-safe recovery boundary for interrupted and failed missions."""

    _NON_RECOVERABLE_STATUSES = frozenset(
        {MissionStatus.COMPLETED, MissionStatus.CANCELLED, MissionStatus.ARCHIVED}
    )

    def __init__(
        self,
        *,
        state_manager: StateManager,
        retry_executor: RetryExecutor | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._state_manager = state_manager
        self._retry_executor = retry_executor or RetryExecutor()
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.ORCHESTRATOR)

    def assess(self, mission_id: UUID) -> RecoveryAssessment:
        """Classify a mission without mutating it."""
        state = self._require_state(mission_id)

        if state.status in self._NON_RECOVERABLE_STATUSES or state.stage is WorkflowStage.MISSION_COMPLETE:
            return RecoveryAssessment(
                mission_id, RecoveryDisposition.COMPLETED, state.stage, "mission_is_terminal"
            )
        if state.status is MissionStatus.WAITING_APPROVAL or state.stage is WorkflowStage.APPROVAL:
            return RecoveryAssessment(
                mission_id, RecoveryDisposition.WAITING_APPROVAL, state.stage, "approval_checkpoint_active"
            )
        if state.status is MissionStatus.RUNNING:
            return RecoveryAssessment(
                mission_id, RecoveryDisposition.ALREADY_ACTIVE, state.stage, "mission_is_running"
            )
        if state.stage is WorkflowStage.MISSION_CREATED:
            return RecoveryAssessment(
                mission_id, RecoveryDisposition.NON_RECOVERABLE, state.stage, "mission_has_not_started"
            )
        if state.status in {MissionStatus.PAUSED, MissionStatus.FAILED, MissionStatus.RETRYING}:
            return RecoveryAssessment(
                mission_id, RecoveryDisposition.RECOVERABLE, state.stage, "mission_can_resume_from_current_stage"
            )
        return RecoveryAssessment(
            mission_id, RecoveryDisposition.NON_RECOVERABLE, state.stage, "unsupported_recovery_status"
        )

    def restore(self, mission_id: UUID) -> MissionState:
        """Restore a recoverable mission to RETRYING without changing stage."""
        assessment = self.assess(mission_id)
        if assessment.disposition is not RecoveryDisposition.RECOVERABLE:
            raise RecoveryManagerError(
                "Mission is not recoverable in its current state.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission_id,
                context={"disposition": assessment.disposition.value, "stage": assessment.stage.value},
            )

        state = self._state_manager.transition(
            mission_id,
            status=MissionStatus.RETRYING,
            stage=assessment.stage,
            current_agent=None,
            reason="recovery_restore",
        )
        self._publish(EventName.RETRY_STARTED, state, operation="recovery_restore")
        return state

    def resume(
        self,
        mission_id: UUID,
        operation: ResumeOperation[T],
    ) -> RecoveryResult[T]:
        """Restore a mission and execute the supplied resume operation with shared retries.

        The operation is an injected workflow boundary. RecoveryManager does
        not know how a workflow engine resumes work and therefore cannot become
        coupled to agent/provider business logic.
        """
        state = self.restore(mission_id)
        try:
            retry_result: RetryResult[T] = self._retry_executor.execute_with_result(
                lambda: operation(state),
                retry_if=self._is_retryable,
                context={
                    "mission_id": str(mission_id),
                    "operation": "mission_recovery",
                    "stage": state.stage.value,
                },
            )
            self._publish(EventName.RETRY_COMPLETED, state, operation="mission_recovery")
            self._logger.info(
                "Mission recovery completed.",
                category=LogCategory.WORKFLOW,
                mission_id=mission_id,
                workflow_stage=state.stage,
                metadata={"attempts_used": retry_result.attempts_used},
            )
            latest = self._state_manager.get_state(mission_id) or state
            return RecoveryResult(latest, retry_result.value, retry_result.attempts_used)
        except RetryExhaustedError as exc:
            error = exc.to_error_report()
            self._state_manager.transition(
                mission_id,
                status=MissionStatus.FAILED,
                stage=state.stage,
                current_agent=None,
                last_error=error,
                reason="recovery_retry_exhausted",
            )
            self._publish(EventName.MISSION_FAILED, state, operation="recovery_retry_exhausted")
            raise
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - recovery boundary
            wrapped = RecoveryManagerError(
                "Mission recovery operation failed unexpectedly.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission_id,
                context={"operation": "resume", "stage": state.stage.value},
                cause=exc,
            )
            self._state_manager.transition(
                mission_id,
                status=MissionStatus.FAILED,
                stage=state.stage,
                current_agent=None,
                last_error=wrapped.to_error_report(),
                reason="recovery_failed",
            )
            self._publish(EventName.MISSION_FAILED, state, operation="recovery_failed")
            raise wrapped from exc

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        return isinstance(exc, AlphaBaseException) and exc.retryable

    def _require_state(self, mission_id: UUID) -> MissionState:
        state = self._state_manager.get_state(mission_id)
        if state is None:
            raise RecoveryManagerError(
                "Cannot recover an unknown mission.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission_id,
                context={"operation": "assess"},
            )
        return state

    def _publish(self, event_name: EventName, state: MissionState, *, operation: str) -> None:
        self._event_bus.publish(
            WorkflowEvent(
                mission_id=state.mission_id,
                agent_id=AgentID.ORCHESTRATOR,
                event_type=event_name.value,
                payload={"operation": operation, "stage": state.stage.value},
            )
        )
