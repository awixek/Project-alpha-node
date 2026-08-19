"""AN-03 public facade and AN-17 AgentHandler adapter."""
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

from .coordinator import ScriptForgeCoordinator
from .models import ScriptDocument, ScriptRequest


class ScriptForge:
    """Production facade for AN-03 script generation."""

    def __init__(self, *, coordinator: ScriptForgeCoordinator, logger: AlphaLogger | None = None) -> None:
        self._coordinator = coordinator
        self._logger = logger or get_agent_logger(AgentID.SCRIPT_FORGE)

    def execute(self, request: ScriptRequest) -> AgentResult[ScriptDocument]:
        started = datetime.now(timezone.utc)
        try:
            document = self._coordinator.run(request)
            return AgentResult(
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                status=ExecutionStatus.SUCCESS,
                payload=document,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except AlphaBaseException as exc:
            self._logger.exception("Script Forge execution failed.", category=LogCategory.ERROR, agent_id=AgentID.SCRIPT_FORGE, mission_id=request.mission_id)
            return AgentResult(
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=exc.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - final agent boundary
            wrapped = AgentExecutionError(
                "Script Forge execution failed unexpectedly.",
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "execute"},
                cause=exc,
            )
            self._logger.exception("Unexpected Script Forge execution failure.", category=LogCategory.ERROR, agent_id=AgentID.SCRIPT_FORGE, mission_id=request.mission_id)
            return AgentResult(
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=wrapped.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

    def as_agent_handler(
        self,
        *,
        request_builder: Callable[[Mission, AgentExecutionContext, BaseModel, BaseModel], ScriptRequest] | None = None,
        mission_resolver: Callable[[UUID], Mission | None] | None = None,
    ):
        """Return an AN-17-compatible handler consuming AN-01 and AN-02 results."""

        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            research_result = next((r for r in context.dependency_results.values() if r.agent_id is AgentID.RESEARCH_CORE and r.status is ExecutionStatus.SUCCESS and r.payload is not None), None)
            fact_result = next((r for r in context.dependency_results.values() if r.agent_id is AgentID.FACT_GUARDIAN and r.status is ExecutionStatus.SUCCESS and r.payload is not None), None)
            if research_result is None or fact_result is None:
                raise AgentExecutionError(
                    "Script Forge requires successful AN-01 and AN-02 dependencies.",
                    agent_id=AgentID.SCRIPT_FORGE,
                    mission_id=context.mission_id,
                    context={"operation": "resolve_dependencies"},
                )
            mission = mission_resolver(context.mission_id) if mission_resolver else None
            if request_builder is not None:
                if mission is None:
                    raise AgentExecutionError(
                        "mission_resolver is required when request_builder is supplied.",
                        agent_id=AgentID.SCRIPT_FORGE,
                        mission_id=context.mission_id,
                    )
                request = request_builder(mission, context, research_result.payload, fact_result.payload)
            else:
                request = ScriptRequest(
                    mission_id=context.mission_id,
                    research=research_result.payload,
                    fact_check=fact_result.payload,
                )
            return self.execute(request)  # type: ignore[return-value]

        return handler
