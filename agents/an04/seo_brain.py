"""AN-04 public facade and AN-17 AgentHandler adapter."""
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

from .coordinator import SEOBrainCoordinator
from .models import SEORequest, SEOResult


class SEOBrain:
    """Public AN-04 facade."""

    def __init__(self, *, coordinator: SEOBrainCoordinator, logger: AlphaLogger | None = None) -> None:
        self._coordinator = coordinator
        self._logger = logger or get_agent_logger(AgentID.SEO_BRAIN)

    def execute(self, request: SEORequest) -> AgentResult[SEOResult]:
        started = datetime.now(timezone.utc)
        try:
            result = self._coordinator.run(request)
            return AgentResult(
                agent_id=AgentID.SEO_BRAIN,
                mission_id=request.mission_id,
                status=ExecutionStatus.SUCCESS,
                payload=result,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except AlphaBaseException as exc:
            self._logger.exception(
                "SEO Brain execution failed.",
                category=LogCategory.ERROR,
                agent_id=AgentID.SEO_BRAIN,
                mission_id=request.mission_id,
            )
            return AgentResult(
                agent_id=AgentID.SEO_BRAIN,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=exc.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - final agent boundary
            wrapped = AgentExecutionError(
                "SEO Brain execution failed unexpectedly.",
                agent_id=AgentID.SEO_BRAIN,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "execute"},
                cause=exc,
            )
            self._logger.exception(
                "Unexpected SEO Brain execution failure.",
                category=LogCategory.ERROR,
                agent_id=AgentID.SEO_BRAIN,
                mission_id=request.mission_id,
            )
            return AgentResult(
                agent_id=AgentID.SEO_BRAIN,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=wrapped.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

    def as_agent_handler(
        self,
        *,
        request_builder: Callable[[Mission, AgentExecutionContext, BaseModel], SEORequest] | None = None,
        mission_resolver: Callable[[UUID], Mission | None] | None = None,
    ):
        """Return an AN-17-compatible handler consuming the AN-03 result."""

        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            script_result = next(
                (
                    result
                    for result in context.dependency_results.values()
                    if result.agent_id is AgentID.SCRIPT_FORGE
                    and result.status is ExecutionStatus.SUCCESS
                    and result.payload is not None
                ),
                None,
            )
            if script_result is None:
                raise AgentExecutionError(
                    "SEO Brain requires a successful AN-03 dependency.",
                    agent_id=AgentID.SEO_BRAIN,
                    mission_id=context.mission_id,
                    context={"operation": "resolve_dependencies"},
                )
            mission = mission_resolver(context.mission_id) if mission_resolver else None
            if request_builder is not None:
                if mission is None:
                    raise AgentExecutionError(
                        "mission_resolver is required when request_builder is supplied.",
                        agent_id=AgentID.SEO_BRAIN,
                        mission_id=context.mission_id,
                    )
                request = request_builder(mission, context, script_result.payload)
            else:
                request = SEORequest(mission_id=context.mission_id, script=script_result.payload)
            return self.execute(request)  # type: ignore[return-value]

        return handler


# Correct public spelling.

