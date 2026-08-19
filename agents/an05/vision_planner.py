"""AN-05 public facade and AN-17 integration adapter."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from pydantic import BaseModel

from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, EventName, LogCategory, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, InputValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ExecutionStatus, Mission

from .coordinator import VisionPlannerCoordinator
from .models import VisionPlan, VisionPlanningRequest


class VisionPlanner:
    """Production facade for AN-05 visual planning."""

    def __init__(self, *, coordinator: VisionPlannerCoordinator, logger: AlphaLogger | None = None) -> None:
        self._coordinator = coordinator
        self._logger = logger or get_agent_logger(AgentID.VISION_PLANNER)

    def execute(self, request: VisionPlanningRequest) -> AgentResult[VisionPlan]:
        started = datetime.now(timezone.utc)
        try:
            plan = self._coordinator.run(request)
            return AgentResult(
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                status=ExecutionStatus.SUCCESS,
                payload=plan,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except AlphaBaseException as exc:
            self._logger.exception(
                "Vision Planner execution failed.",
                category=LogCategory.ERROR,
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
            )
            return AgentResult(
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=exc.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - final agent boundary
            wrapped = AgentExecutionError(
                "Vision Planner execution failed unexpectedly.",
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "execute"},
                cause=exc,
            )
            self._logger.exception(
                "Unexpected Vision Planner execution failure.",
                category=LogCategory.ERROR,
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
            )
            return AgentResult(
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=wrapped.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

    def as_agent_handler(
        self,
        *,
        request_builder: Callable[[Mission, AgentExecutionContext, BaseModel, BaseModel | None], VisionPlanningRequest] | None = None,
        mission_resolver: Callable[[UUID], Mission | None] | None = None,
    ):
        """Return an AN-17-compatible handler consuming AN-03 and optional AN-04."""

        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            script_result = next(
                (result for result in context.dependency_results.values()
                 if result.agent_id is AgentID.SCRIPT_FORGE and result.status is ExecutionStatus.SUCCESS and result.payload is not None),
                None,
            )
            if script_result is None:
                raise AgentExecutionError(
                    "Vision Planner requires a successful AN-03 dependency.",
                    agent_id=AgentID.VISION_PLANNER,
                    mission_id=context.mission_id,
                    context={"operation": "resolve_dependencies"},
                )
            seo_result = next(
                (result for result in context.dependency_results.values()
                 if result.agent_id is AgentID.SEO_BRAIN and result.status is ExecutionStatus.SUCCESS and result.payload is not None),
                None,
            )
            mission = mission_resolver(context.mission_id) if mission_resolver else None
            if request_builder is not None:
                if mission is None:
                    raise AgentExecutionError(
                        "mission_resolver is required when request_builder is supplied.",
                        agent_id=AgentID.VISION_PLANNER,
                        mission_id=context.mission_id,
                    )
                request = request_builder(
                    mission,
                    context,
                    script_result.payload,
                    seo_result.payload if seo_result else None,
                )
            else:
                request = VisionPlanningRequest(
                    mission_id=context.mission_id,
                    script=script_result.payload,
                    seo_metadata=seo_result.payload if seo_result else None,
                )
            return self.execute(request)  # type: ignore[return-value]

        return handler
