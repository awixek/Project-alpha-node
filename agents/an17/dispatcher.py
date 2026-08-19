"""AN-17 task dispatcher.

The dispatcher is the execution boundary between the workflow engine and
future agents. It knows how to order tasks, invoke an injected agent handler,
retry retryable failures through the shared Retry Engine, and collect the
shared ``AgentResult`` contract. It deliberately contains no content or
provider-specific business logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol
import threading
from uuid import UUID

from pydantic import BaseModel

from shared.constants import AgentID, EventName, LogCategory, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AlphaBaseException, AgentExecutionError, RetryExhaustedError
from shared.logger import AlphaLogger, get_agent_logger
from shared.retry import RetryExecutor, RetryResult
from shared.schemas import AgentResult, ExecutionStatus, Mission, WorkflowEvent


class AgentExecutionContext(BaseModel):
    """Immutable input envelope supplied to an injected agent handler."""

    mission_id: UUID
    agent_id: AgentID
    stage: WorkflowStage
    dependency_results: dict[str, AgentResult[BaseModel]]

    model_config = {"extra": "forbid", "arbitrary_types_allowed": True}


class AgentHandler(Protocol):
    """Callable contract implemented by a future agent adapter."""

    def __call__(self, context: AgentExecutionContext) -> AgentResult[BaseModel]:
        """Execute one agent task and return the shared AgentResult envelope."""
        ...


@dataclass(frozen=True)
class DispatchTask:
    """One executable node in a workflow dependency graph."""

    task_id: str
    stage: WorkflowStage
    agent_id: AgentID
    handler: AgentHandler
    dependencies: tuple[str, ...] = ()


class _RetryableAgentFailure(AgentExecutionError):
    """Internal bridge from a retryable AgentResult to RetryExecutor."""

    default_code = "retryable_agent_result"
    default_retryable = True

    def __init__(self, result: AgentResult[BaseModel]) -> None:
        super().__init__(
            "Agent returned a retryable failure result.",
            agent_id=result.agent_id,
            mission_id=result.mission_id,
            retryable=True,
            context={"status": result.status.value},
        )
        self.result = result


class Dispatcher:
    """Dispatches independent workflow tasks without owning business logic."""

    def __init__(
        self,
        *,
        retry_executor: RetryExecutor | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._retry_executor = retry_executor or RetryExecutor()
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.ORCHESTRATOR)
        self._results: dict[str, AgentResult[BaseModel]] = {}
        self._mission_results: dict[UUID, dict[str, AgentResult[BaseModel]]] = {}
        self._lock = threading.RLock()

    def resolve_execution_order(
        self,
        tasks: tuple[DispatchTask, ...],
        *,
        external_task_ids: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[DispatchTask, ...]:
        """Validate task identifiers/dependencies and return topological order."""
        if not tasks:
            return ()

        task_map: dict[str, DispatchTask] = {}
        for task in tasks:
            if not task.task_id.strip():
                raise AgentExecutionError(
                    "Workflow task_id must not be empty.",
                    agent_id=AgentID.ORCHESTRATOR,
                    context={"operation": "resolve_execution_order"},
                )
            if task.task_id in task_map:
                raise AgentExecutionError(
                    f"Duplicate workflow task_id: {task.task_id!r}.",
                    agent_id=AgentID.ORCHESTRATOR,
                    context={"task_id": task.task_id},
                )
            task_map[task.task_id] = task

        for task in tasks:
            missing = [
                dependency
                for dependency in task.dependencies
                if dependency not in task_map and dependency not in external_task_ids
            ]
            if missing:
                raise AgentExecutionError(
                    "Workflow task has unresolved dependencies.",
                    agent_id=AgentID.ORCHESTRATOR,
                    context={"task_id": task.task_id, "missing_dependencies": ",".join(missing)},
                )
            if task.task_id in task.dependencies:
                raise AgentExecutionError(
                    "Workflow task cannot depend on itself.",
                    agent_id=AgentID.ORCHESTRATOR,
                    context={"task_id": task.task_id},
                )

        indegree = {
            task_id: sum(1 for dependency in task.dependencies if dependency in task_map)
            for task_id, task in task_map.items()
        }
        dependents: dict[str, list[str]] = {task_id: [] for task_id in task_map}
        for task in tasks:
            for dependency in task.dependencies:
                if dependency in dependents:
                    dependents[dependency].append(task.task_id)

        ready = [task.task_id for task in tasks if indegree[task.task_id] == 0]
        ordered: list[DispatchTask] = []

        while ready:
            task_id = ready.pop(0)
            ordered.append(task_map[task_id])
            for dependent in dependents[task_id]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)

        if len(ordered) != len(tasks):
            raise AgentExecutionError(
                "Workflow task dependency graph contains a cycle.",
                agent_id=AgentID.ORCHESTRATOR,
                context={"task_count": len(tasks)},
            )
        return tuple(ordered)

    def dispatch(
        self,
        task: DispatchTask,
        *,
        mission: Mission,
        dependency_results: Mapping[str, AgentResult[BaseModel]],
    ) -> AgentResult[BaseModel]:
        """Dispatch one task after dependency resolution has completed."""
        started_at = datetime.now(timezone.utc)
        context = AgentExecutionContext(
            mission_id=mission.mission_id,
            agent_id=task.agent_id,
            stage=task.stage,
            dependency_results=dict(dependency_results),
        )

        self._publish(EventName.AGENT_STARTED, mission.mission_id, task.agent_id, task.stage)
        self._logger.info(
            "Dispatching workflow task.",
            category=LogCategory.AGENT,
            mission_id=mission.mission_id,
            agent_id=task.agent_id,
            workflow_stage=task.stage,
            metadata={"task_id": task.task_id, "dependency_count": len(dependency_results)},
        )

        try:
            retry_result: RetryResult[AgentResult[BaseModel]] = self._retry_executor.execute_with_result(
                lambda: self._invoke(task, context),
                retry_if=self._is_retryable_exception,
                context={
                    "mission_id": str(mission.mission_id),
                    "agent_id": task.agent_id.value,
                    "task_id": task.task_id,
                    "stage": task.stage.value,
                },
            )
            result = retry_result.value.model_copy(update={"retry_count": retry_result.attempts_used - 1})
            self._validate_result(result, task, mission.mission_id)
            with self._lock:
                self._results[task.task_id] = result
                self._mission_results.setdefault(mission.mission_id, {})[task.task_id] = result
            self._publish_result_event(result, task.stage)
            return result
        except RetryExhaustedError as exc:
            result = self._failure_result_from_exception(
                exc,
                task=task,
                mission_id=mission.mission_id,
                started_at=started_at,
            )
            with self._lock:
                self._results[task.task_id] = result
                self._mission_results.setdefault(mission.mission_id, {})[task.task_id] = result
            self._publish_result_event(result, task.stage)
            return result
        except AlphaBaseException as exc:
            result = self._failure_result_from_exception(
                exc,
                task=task,
                mission_id=mission.mission_id,
                started_at=started_at,
            )
            with self._lock:
                self._results[task.task_id] = result
                self._mission_results.setdefault(mission.mission_id, {})[task.task_id] = result
            self._publish_result_event(result, task.stage)
            return result
        except Exception as exc:  # noqa: BLE001 - dispatcher is a failure boundary
            self._logger.exception(
                "Unexpected agent dispatch failure.",
                category=LogCategory.AGENT,
                mission_id=mission.mission_id,
                agent_id=task.agent_id,
                workflow_stage=task.stage,
            )
            wrapped = AgentExecutionError(
                "Agent execution failed unexpectedly.",
                agent_id=task.agent_id,
                mission_id=mission.mission_id,
                retryable=False,
                context={"task_id": task.task_id, "stage": task.stage.value},
                cause=exc,
            )
            result = self._failure_result_from_exception(
                wrapped,
                task=task,
                mission_id=mission.mission_id,
                started_at=started_at,
            )
            with self._lock:
                self._results[task.task_id] = result
                self._mission_results.setdefault(mission.mission_id, {})[task.task_id] = result
            self._publish_result_event(result, task.stage)
            return result

    def get_result(self, task_id: str, *, mission_id: UUID | None = None) -> AgentResult[BaseModel] | None:
        """Return a dispatched result, optionally scoped to one mission."""
        with self._lock:
            if mission_id is not None:
                return self._mission_results.get(mission_id, {}).get(task_id)
            return self._results.get(task_id)

    def results(self, *, mission_id: UUID | None = None) -> dict[str, AgentResult[BaseModel]]:
        """Return defensive copies of collected execution results."""
        with self._lock:
            if mission_id is not None:
                return dict(self._mission_results.get(mission_id, {}))
            return dict(self._results)

    @staticmethod
    def _is_retryable_exception(exc: BaseException) -> bool:
        return isinstance(exc, AlphaBaseException) and exc.retryable

    @staticmethod
    def _invoke(task: DispatchTask, context: AgentExecutionContext) -> AgentResult[BaseModel]:
        result = task.handler(context)
        if result.status in {ExecutionStatus.FAILED, ExecutionStatus.PARTIAL_SUCCESS}:
            if result.error is None:
                raise AgentExecutionError(
                    "AgentResult failure status requires a structured error.",
                    agent_id=task.agent_id,
                    mission_id=context.mission_id,
                    context={"status": result.status.value},
                )
            if result.error.retryable:
                raise _RetryableAgentFailure(result)
        return result

    @staticmethod
    def _validate_result(
        result: AgentResult[BaseModel], task: DispatchTask, mission_id: UUID
    ) -> None:
        if result.agent_id is not task.agent_id:
            raise AgentExecutionError(
                "AgentResult agent_id does not match dispatched task.",
                agent_id=task.agent_id,
                mission_id=mission_id,
                context={"task_id": task.task_id, "returned_agent_id": result.agent_id.value},
            )
        if result.mission_id != mission_id:
            raise AgentExecutionError(
                "AgentResult mission_id does not match dispatched mission.",
                agent_id=task.agent_id,
                mission_id=mission_id,
                context={"task_id": task.task_id, "returned_mission_id": str(result.mission_id)},
            )

    @staticmethod
    def _failure_result_from_exception(
        exc: AlphaBaseException,
        *,
        task: DispatchTask,
        mission_id: UUID,
        started_at: datetime,
    ) -> AgentResult[BaseModel]:
        cause = exc.__cause__
        if isinstance(cause, _RetryableAgentFailure):
            report = cause.result.error or exc.to_error_report()
            retry_count = max(cause.result.retry_count, 0)
        elif isinstance(exc, RetryExhaustedError) and isinstance(cause, AlphaBaseException):
            report = cause.to_error_report()
            retry_count = max(0, int(exc.context.get("attempts", 1)) - 1)
        else:
            report = exc.to_error_report()
            retry_count = 0

        return AgentResult[BaseModel](
            agent_id=task.agent_id,
            mission_id=mission_id,
            status=ExecutionStatus.FAILED,
            error=report,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            retry_count=retry_count,
        )

    def _publish_result_event(self, result: AgentResult[BaseModel], stage: WorkflowStage) -> None:
        event = EventName.AGENT_COMPLETED if result.status is ExecutionStatus.SUCCESS else EventName.AGENT_FAILED
        self._publish(event, result.mission_id, result.agent_id, stage)

    def _publish(
        self,
        event_name: EventName,
        mission_id: UUID,
        agent_id: AgentID,
        stage: WorkflowStage,
    ) -> None:
        self._event_bus.publish(
            WorkflowEvent(
                mission_id=mission_id,
                agent_id=agent_id,
                event_type=event_name.value,
                payload={"stage": stage.value},
            )
        )
