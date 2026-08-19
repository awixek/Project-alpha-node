"""AN-17 workflow execution and mission-state coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from pydantic import BaseModel

from shared.constants import AgentID, EventName, LogCategory, MissionStatus, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AlphaBaseException, MissionError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ErrorReport, ExecutionStatus, Mission, WorkflowEvent
from shared.validators import WorkflowValidator

from .dispatcher import DispatchTask, Dispatcher
from .interfaces import MissionStateStore
from .mission_manager import MissionManager
from .state_manager import StateManager


@dataclass(frozen=True)
class WorkflowPlan:
    """Immutable execution plan supplied to the workflow engine."""

    tasks: tuple[DispatchTask, ...]

    @classmethod
    def from_tasks(cls, tasks: Iterable[DispatchTask]) -> "WorkflowPlan":
        return cls(tasks=tuple(tasks))


class WorkflowEngine:
    """Executes validated workflow plans and owns stage sequencing."""

    def __init__(
        self,
        *,
        mission_manager: MissionManager,
        state_store: MissionStateStore,
        state_manager: StateManager | None = None,
        dispatcher: Dispatcher | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._mission_manager = mission_manager
        self._state_store = state_store
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.ORCHESTRATOR)
        self._state_manager = state_manager or StateManager(
            state_store=state_store,
            event_bus=self._event_bus,
            logger=self._logger,
        )
        self._dispatcher = dispatcher or Dispatcher(event_bus=self._event_bus, logger=self._logger)

    @property
    def state_manager(self) -> StateManager:
        """Return the state manager used for every workflow mutation."""
        return self._state_manager

    @property
    def dispatcher(self) -> Dispatcher:
        """Return the dispatcher used by this engine."""
        return self._dispatcher

    def build_execution_order(
        self,
        plan: WorkflowPlan,
        *,
        current_stage: WorkflowStage = WorkflowStage.MISSION_CREATED,
        mission_id: UUID | None = None,
    ) -> tuple[DispatchTask, ...]:
        """Validate task dependencies and stage transitions from ``current_stage``."""
        external_task_ids = set(self._dispatcher.results(mission_id=mission_id).keys()) if mission_id else set()
        ordered = self._dispatcher.resolve_execution_order(
            plan.tasks,
            external_task_ids=external_task_ids,
        )
        stage = current_stage
        for task in ordered:
            if task.stage is not stage:
                WorkflowValidator.validate_transition(stage, task.stage)
            stage = task.stage
        return ordered

    def execute(self, mission: Mission, plan: WorkflowPlan) -> dict[str, AgentResult[BaseModel]]:
        """Execute a plan from the mission's persisted stage, stopping on failure."""
        state = self._mission_manager.get_state(mission.mission_id)
        if state is None:
            raise MissionError(
                "Cannot execute workflow for an unaccepted mission.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission.mission_id,
                context={"operation": "execute", "reason": "mission_state_not_found"},
            )

        ordered = self.build_execution_order(
            plan,
            current_stage=state.stage,
            mission_id=mission.mission_id,
        )
        if not ordered:
            raise MissionError(
                "Workflow plan must contain at least one task.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission.mission_id,
                context={"operation": "execute"},
            )

        if state.status is MissionStatus.PENDING:
            self._publish(EventName.MISSION_STARTED, mission.mission_id)

        collected: dict[str, AgentResult[BaseModel]] = {}
        current_state = state
        try:
            for task in ordered:
                if task.stage is not current_state.stage:
                    current_state = self._state_manager.transition(
                        mission.mission_id,
                        status=MissionStatus.RUNNING,
                        stage=task.stage,
                        current_agent=task.agent_id,
                        last_error=None,
                        reason=f"workflow_stage_{task.stage.value}",
                    )
                else:
                    current_state = self._state_manager.transition(
                        mission.mission_id,
                        status=MissionStatus.RUNNING,
                        current_agent=task.agent_id,
                        last_error=None,
                        reason=f"workflow_agent_{task.agent_id.value}",
                    )

                dependency_results = {}
                for name in task.dependencies:
                    dependency_results[name] = collected.get(
                        name,
                        self._dispatcher.get_result(name, mission_id=mission.mission_id),
                    )
                    if dependency_results[name] is None:
                        raise MissionError(
                            "Workflow dependency result is unavailable for resume.",
                            agent_id=AgentID.ORCHESTRATOR,
                            mission_id=mission.mission_id,
                            context={"operation": "execute", "task_id": task.task_id, "dependency": name},
                        )
                result = self._dispatcher.dispatch(task, mission=mission, dependency_results=dependency_results)
                collected[task.task_id] = result

                if result.status is not ExecutionStatus.SUCCESS:
                    self._state_manager.mark_agent_failed(
                        mission.mission_id,
                        task.agent_id,
                        result.error or self._error_from_result(mission, task),
                    )
                    self._state_manager.transition(
                        mission.mission_id,
                        status=MissionStatus.FAILED,
                        stage=current_state.stage,
                        current_agent=None,
                        reason="workflow_failed",
                    )
                    self._publish(EventName.MISSION_FAILED, mission.mission_id)
                    return collected

                current_state = self._state_manager.mark_agent_completed(
                    mission.mission_id,
                    task.agent_id,
                    reason="agent_completed",
                )

            current_state = self._state_manager.get_state(mission.mission_id) or current_state
            if current_state.stage is WorkflowStage.ANALYTICS:
                self._state_manager.transition(
                    mission.mission_id,
                    status=MissionStatus.COMPLETED,
                    stage=WorkflowStage.MISSION_COMPLETE,
                    current_agent=None,
                    last_error=None,
                    reason="mission_completed",
                )
                self._publish(EventName.MISSION_COMPLETED, mission.mission_id)
            else:
                self._logger.info(
                    "Workflow plan completed without reaching the terminal mission stage.",
                    category=LogCategory.WORKFLOW,
                    mission_id=mission.mission_id,
                    workflow_stage=current_state.stage,
                    metadata={"next_stage_required": True},
                )
            return collected
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - workflow boundary
            self._logger.exception(
                "Unexpected workflow execution failure.",
                category=LogCategory.WORKFLOW,
                mission_id=mission.mission_id,
            )
            wrapped = MissionError(
                "Workflow execution failed unexpectedly.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission.mission_id,
                context={"operation": "execute"},
                cause=exc,
            )
            self._state_manager.transition(
                mission.mission_id,
                status=MissionStatus.FAILED,
                stage=current_state.stage,
                current_agent=None,
                last_error=wrapped.to_error_report(),
                reason="workflow_unexpected_failure",
            )
            self._publish(EventName.MISSION_FAILED, mission.mission_id)
            return collected

    @staticmethod
    def _error_from_result(mission: Mission, task: DispatchTask) -> ErrorReport:
        return MissionError(
            "Agent returned a failure without a structured error.",
            agent_id=task.agent_id,
            mission_id=mission.mission_id,
            context={"task_id": task.task_id, "stage": task.stage.value},
        ).to_error_report()

    def _publish(self, event_name: EventName, mission_id: UUID) -> None:
        self._event_bus.publish(
            WorkflowEvent(
                mission_id=mission_id,
                agent_id=AgentID.ORCHESTRATOR,
                event_type=event_name.value,
                payload={"agent_id": AgentID.ORCHESTRATOR.value},
            )
        )
