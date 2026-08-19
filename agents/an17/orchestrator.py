"""AN-17 Alpha Orchestrator — Milestone 4 hardened integration boundary."""

from __future__ import annotations

from uuid import UUID

from shared.config import AlphaConfig, get_config
from shared.constants import AgentID, EventName, LogCategory, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AlphaBaseException, MissionError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import ApprovalDecision, ApprovalRequest, Mission, MissionState, WorkflowEvent

from .approval_manager import ApprovalManager
from .dispatcher import Dispatcher
from .interfaces import MissionStateStore, WorkflowExecutor
from .mission_manager import MissionManager
from .recovery_manager import RecoveryManager, RecoveryResult
from .state_manager import StateManager
from .workflow_engine import WorkflowEngine, WorkflowPlan


class AlphaOrchestrator:
    """Central dependency-injection and lifecycle coordination boundary for AN-17."""

    def __init__(
        self,
        *,
        config: AlphaConfig | None = None,
        logger: AlphaLogger | None = None,
        event_bus: EventBus | None = None,
        mission_manager: MissionManager | None = None,
        state_store: MissionStateStore | None = None,
        state_manager: StateManager | None = None,
        dispatcher: Dispatcher | None = None,
        workflow_engine: WorkflowEngine | None = None,
        recovery_manager: RecoveryManager | None = None,
        approval_manager: ApprovalManager | None = None,
        workflow_executor: WorkflowExecutor | None = None,
    ) -> None:
        self._config = config or get_config()
        self._logger = logger or get_agent_logger(AgentID.ORCHESTRATOR)
        self._event_bus = event_bus or get_event_bus()
        self._mission_manager = mission_manager or MissionManager(
            state_store=state_store,
            logger=self._logger,
        )
        shared_store = self._mission_manager.state_store
        if state_store is not None and shared_store is not state_store:
            raise MissionError(
                "MissionManager and state_store must share the same persistence boundary.",
                agent_id=AgentID.ORCHESTRATOR,
                context={"operation": "initialize"},
            )

        self._state_manager = state_manager or StateManager(
            state_store=shared_store,
            event_bus=self._event_bus,
            logger=self._logger,
        )
        self._dispatcher = dispatcher or Dispatcher(
            event_bus=self._event_bus,
            logger=self._logger,
        )
        self._workflow_engine = workflow_engine or WorkflowEngine(
            mission_manager=self._mission_manager,
            state_store=shared_store,
            state_manager=self._state_manager,
            dispatcher=self._dispatcher,
            event_bus=self._event_bus,
            logger=self._logger,
        )
        self._recovery_manager = recovery_manager or RecoveryManager(
            state_manager=self._state_manager,
            event_bus=self._event_bus,
            logger=self._logger,
        )
        self._approval_manager = approval_manager or ApprovalManager(
            state_manager=self._state_manager,
            event_bus=self._event_bus,
            logger=self._logger,
        )
        self._workflow_executor = workflow_executor

        self._logger.info(
            "AN-17 production integration initialized.",
            category=LogCategory.SYSTEM,
            agent_id=AgentID.ORCHESTRATOR,
            metadata={
                "foundation_version": self._config.project.version,
                "workflow_execution_enabled": True,
                "recovery_enabled": True,
                "approval_enabled": True,
            },
        )

    @property
    def config(self) -> AlphaConfig:
        return self._config

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def mission_manager(self) -> MissionManager:
        return self._mission_manager

    @property
    def state_manager(self) -> StateManager:
        return self._state_manager

    @property
    def workflow_engine(self) -> WorkflowEngine:
        return self._workflow_engine

    @property
    def recovery_manager(self) -> RecoveryManager:
        return self._recovery_manager

    @property
    def approval_manager(self) -> ApprovalManager:
        return self._approval_manager

    def receive_mission(self, mission: Mission) -> MissionState:
        """Validate and register a mission without executing its workflow."""
        try:
            with self._logger.timed(
                "AN-17 mission intake completed.",
                category=LogCategory.PERFORMANCE,
                mission_id=mission.mission_id,
                agent_id=AgentID.ORCHESTRATOR,
            ):
                state = self._mission_manager.accept(mission)
                if self._workflow_executor is not None:
                    self._workflow_executor.prepare(mission, state)
                self._publish_mission_created(mission, state)
                return state
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - orchestration boundary
            self._logger.exception(
                "Unexpected failure during mission intake.",
                category=LogCategory.MISSION,
                mission_id=mission.mission_id,
                agent_id=AgentID.ORCHESTRATOR,
            )
            raise MissionError(
                "Orchestrator failed to initialize mission.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission.mission_id,
                context={"operation": "receive_mission"},
                cause=exc,
            ) from exc

    def execute_workflow(self, mission: Mission, plan: WorkflowPlan) -> dict:
        """Execute a supplied workflow plan through WorkflowEngine."""
        try:
            self._logger.info(
                "Mission workflow execution started.",
                category=LogCategory.WORKFLOW,
                mission_id=mission.mission_id,
            )
            return self._workflow_engine.execute(mission, plan)
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - orchestration boundary
            self._logger.exception(
                "Unexpected workflow orchestration failure.",
                category=LogCategory.WORKFLOW,
                mission_id=mission.mission_id,
            )
            raise MissionError(
                "Orchestrator failed during workflow execution.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission.mission_id,
                context={"operation": "execute_workflow"},
                cause=exc,
            ) from exc

    def run_mission(self, mission: Mission, plan: WorkflowPlan) -> dict:
        """Run a mission and pause at the approval checkpoint when required."""
        self.receive_mission(mission)
        executable = plan
        if mission.requires_human_approval:
            executable = self._plan_before_approval(plan)

        results = self.execute_workflow(mission, executable)
        state = self.get_mission_state(mission.mission_id)
        if mission.requires_human_approval and state is not None and state.stage is WorkflowStage.QUALITY_REVIEW:
            self._approval_manager.create_request(mission.mission_id)
        return results

    def resume_mission(self, mission: Mission, plan: WorkflowPlan) -> dict:
        """Resume an approved/recoverable mission using its persisted stage."""
        state = self.get_mission_state(mission.mission_id)
        if state is None:
            raise MissionError(
                "Cannot resume an unknown mission.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission.mission_id,
                context={"operation": "resume_mission"},
            )
        return self.execute_workflow(mission, self._plan_from_stage(plan, state.stage))

    def create_approval_request(self, mission_id: UUID) -> ApprovalRequest:
        return self._approval_manager.create_request(mission_id)

    def receive_approval(
        self,
        approval_id: UUID,
        decision: ApprovalDecision,
        *,
        reviewer: str,
        comments: str | None = None,
    ) -> ApprovalRequest:
        return self._approval_manager.receive_decision(
            approval_id,
            decision,
            reviewer=reviewer,
            comments=comments,
        )

    def recover_mission(self, mission_id: UUID, operation) -> RecoveryResult:
        """Resume an interrupted mission through RecoveryManager and shared retry policy."""
        try:
            self._logger.info(
                "Mission recovery started.",
                category=LogCategory.WORKFLOW,
                mission_id=mission_id,
            )
            return self._recovery_manager.resume(mission_id, operation)
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - orchestration boundary
            self._logger.exception(
                "Unexpected recovery orchestration failure.",
                category=LogCategory.WORKFLOW,
                mission_id=mission_id,
            )
            raise MissionError(
                "Orchestrator failed during mission recovery.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission_id,
                context={"operation": "recover_mission"},
                cause=exc,
            ) from exc

    def get_mission_state(self, mission_id: UUID) -> MissionState | None:
        return self._mission_manager.get_state(mission_id)

    @staticmethod
    def _plan_before_approval(plan: WorkflowPlan) -> WorkflowPlan:
        tasks = []
        for task in plan.tasks:
            if task.stage in {WorkflowStage.APPROVAL, WorkflowStage.PUBLISHING}:
                break
            tasks.append(task)
        return WorkflowPlan.from_tasks(tasks)


    @staticmethod
    def _plan_from_stage(plan: WorkflowPlan, current_stage: WorkflowStage) -> WorkflowPlan:
        """Return the remaining plan beginning at the persisted stage."""
        tasks = list(plan.tasks)
        if current_stage is WorkflowStage.MISSION_CREATED:
            return plan
        for index, task in enumerate(tasks):
            if task.stage is current_stage:
                return WorkflowPlan.from_tasks(tasks[index:])
        for index, task in enumerate(tasks):
            try:
                WorkflowStage(task.stage.value)
            except ValueError:
                continue
            if task.stage is WorkflowStage.PUBLISHING and current_stage is WorkflowStage.APPROVAL:
                return WorkflowPlan.from_tasks(tasks[index:])
        raise MissionError(
            "Workflow plan does not contain a task compatible with the persisted mission stage.",
            agent_id=AgentID.ORCHESTRATOR,
            context={"operation": "resume_mission", "stage": current_stage.value},
        )

    def _publish_mission_created(self, mission: Mission, state: MissionState) -> None:
        self._event_bus.publish(
            WorkflowEvent(
                mission_id=mission.mission_id,
                agent_id=AgentID.ORCHESTRATOR,
                event_type=EventName.MISSION_CREATED.value,
                payload={
                    "mission_id": str(mission.mission_id),
                    "topic": mission.topic.title,
                    "status": state.status.value,
                    "stage": state.stage.value,
                },
            )
        )
