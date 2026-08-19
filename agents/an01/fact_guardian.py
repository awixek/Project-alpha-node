"""AN-02 public facade and frozen AN-17 AgentHandler adapter."""
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

from .coordinator import FactVerificationCoordinator
from .models import FactCheckRequest, FactVerificationReport


class FactGuardian:
    """Production facade for deterministic fact verification."""

    def __init__(
        self,
        *,
        coordinator: FactVerificationCoordinator,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._logger = logger or get_agent_logger(AgentID.FACT_GUARDIAN)

    def execute(self, request: FactCheckRequest) -> AgentResult[FactVerificationReport]:
        started = datetime.now(timezone.utc)
        try:
            report = self._coordinator.run(request)
            return AgentResult(
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
                status=ExecutionStatus.SUCCESS,
                payload=report,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except AlphaBaseException as exc:
            self._logger.exception(
                "Fact Guardian execution failed.",
                category=LogCategory.ERROR,
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
            )
            return AgentResult(
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=exc.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 - final agent boundary
            self._logger.exception(
                "Unexpected Fact Guardian failure.",
                category=LogCategory.ERROR,
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
            )
            wrapped = AgentExecutionError(
                "Fact Guardian execution failed unexpectedly.",
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "execute"},
                cause=exc,
            )
            return AgentResult(
                agent_id=AgentID.FACT_GUARDIAN,
                mission_id=request.mission_id,
                status=ExecutionStatus.FAILED,
                error=wrapped.to_error_report(),
                started_at=started,
                completed_at=datetime.now(timezone.utc),
            )

    def as_agent_handler(
        self,
        *,
        mission_resolver: Callable[[UUID], Mission | None] | None = None,
        request_builder: Callable[[Mission, AgentExecutionContext, BaseModel], FactCheckRequest] | None = None,
    ):
        """Adapt AN-02 to the existing AN-17 dispatcher contract.

        If no builder is supplied, the handler consumes the successful AN-01
        result found in ``dependency_results``. A custom builder can inject
        mission-specific language/search constraints without changing AN-02.
        """

        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            research_result = next(
                (
                    result
                    for result in context.dependency_results.values()
                    if result.agent_id == AgentID.RESEARCH_CORE
                    and result.status == ExecutionStatus.SUCCESS
                    and result.payload is not None
                ),
                None,
            )
            if research_result is None:
                raise AgentExecutionError(
                    "Fact Guardian requires a successful AN-01 Research Core result.",
                    agent_id=AgentID.FACT_GUARDIAN,
                    mission_id=context.mission_id,
                    context={"operation": "resolve_research_dependency"},
                )

            mission = mission_resolver(context.mission_id) if mission_resolver else None
            if request_builder is not None:
                if mission is None:
                    raise AgentExecutionError(
                        "A mission_resolver is required when using a custom FactCheckRequest builder.",
                        agent_id=AgentID.FACT_GUARDIAN,
                        mission_id=context.mission_id,
                        context={"operation": "build_request"},
                    )
                request = request_builder(mission, context, research_result.payload)
            else:
                request = FactCheckRequest(
                    mission_id=context.mission_id,
                    research=research_result.payload,
                )
            return self.execute(request)  # type: ignore[return-value]

        return handler
