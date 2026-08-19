"""AN-01 Research Core agent facade and AN-17 Dispatcher adapter."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from pydantic import BaseModel

from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, LogCategory
from shared.exceptions import AgentExecutionError, AlphaBaseException
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ExecutionStatus, Mission

from .coordinator import ResearchCoordinator
from .models import ResearchBatch, ResearchRequest


class ResearchCore:
    """Production facade for AN-01 research discovery."""

    def __init__(self, *, coordinator: ResearchCoordinator, logger: AlphaLogger | None = None) -> None:
        self._coordinator = coordinator
        self._logger = logger or get_agent_logger(AgentID.RESEARCH_CORE)

    def execute(self, request: ResearchRequest) -> AgentResult[ResearchBatch]:
        started = datetime.now(timezone.utc)
        try:
            payload = self._coordinator.run(request)
            return AgentResult(
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                status=ExecutionStatus.SUCCESS,
                payload=payload,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except AlphaBaseException as exc:
            self._logger.exception(
                "Research Core execution failed.",
                category=LogCategory.ERROR,
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
            )
            return AgentResult(
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=exc.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - agent boundary
            self._logger.exception(
                "Unexpected Research Core failure.",
                category=LogCategory.ERROR,
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
            )
            wrapped = AgentExecutionError(
                "Research Core execution failed unexpectedly.",
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "execute"},
                cause=exc,
            )
            return AgentResult(
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=wrapped.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

    def as_agent_handler(
        self,
        *,
        mission_resolver: Callable[[UUID], Mission | None],
        request_builder: Callable[[Mission, AgentExecutionContext], ResearchRequest],
    ):
        """Adapt AN-01 to the frozen AN-17 AgentHandler contract."""

        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            mission = mission_resolver(context.mission_id)
            if mission is None:
                raise AgentExecutionError(
                    "Mission not found for Research Core execution.",
                    agent_id=AgentID.RESEARCH_CORE,
                    mission_id=context.mission_id,
                    context={"operation": "resolve_mission"},
                )
            request = request_builder(mission, context)
            return self.execute(request)  # type: ignore[return-value]

        return handler
