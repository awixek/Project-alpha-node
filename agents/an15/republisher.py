"""AN-15 business service for cross-platform repurposing."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .adapters import AdapterRegistry
from pydantic import BaseModel
from agents.an12.models import PublishPackage
from agents.an13.models import AnalyticsReport
from agents.an14.models import EvolutionReport
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ExecutionStatus
from .formatter import MetadataFormatter
from .models import (
    DistributionPlan,
    DistributionStatus,
    ExecutionMetrics,
    PlatformDistribution,
    RepurposeConfig,
    RepurposeRequest,
    RepublisherPackage,
)
from .planner import RepurposePlanner
from .transformer import ContentTransformer
from .validator import DistributionValidator


class RepublisherService:
    def __init__(self, *, planner: RepurposePlanner | None = None,
                 transformer: ContentTransformer | None = None,
                 formatter: MetadataFormatter | None = None,
                 validator: DistributionValidator | None = None,
                 adapters: AdapterRegistry | None = None) -> None:
        registry = adapters or AdapterRegistry()
        self.planner = planner or RepurposePlanner()
        self.transformer = transformer or ContentTransformer(registry)
        self.formatter = formatter or MetadataFormatter()
        self.validator = validator or DistributionValidator()

    def execute(self, request: RepurposeRequest, config: RepurposeConfig) -> RepublisherPackage:
        started = datetime.now(timezone.utc)
        plan = self.planner.plan(request.publish, request.analytics, request.evolution, config)
        distributions: list[PlatformDistribution] = []
        all_assets = []
        validation_results = {}
        optimization_summaries = {}
        for platform in plan.ordered_platforms:
            profile = self.planner.profile(platform, config)
            transformation = self.planner.transformation_for(platform, config)
            title, source_text, source_ref = self._source(request)
            asset = self.transformer.transform(
                platform=platform,
                transformation=transformation,
                source_title=title,
                source_text=source_text,
                profile=profile,
                source_reference=source_ref,
            )
            metadata = self.formatter.build(request, title=asset.title, body=asset.body, profile=profile)
            distribution = PlatformDistribution(
                platform=platform,
                status=DistributionStatus.READY,
                profile=profile,
                assets=[asset],
                metadata=metadata,
                optimization_summary=self._optimization_summary(request, platform.value),
                priority=plan.ordered_platforms.index(platform) + 1,
            )
            issues = self.validator.validate(distribution)
            status = self.validator.status(issues)
            distribution = distribution.model_copy(update={
                "status": DistributionStatus(status),
                "validation_issues": issues,
            })
            distributions.append(distribution)
            all_assets.extend(distribution.assets)
            validation_results[platform.value] = issues
            optimization_summaries[platform.value] = distribution.optimization_summary

        completed = datetime.now(timezone.utc)
        failed = sum(d.status is DistributionStatus.FAILED for d in distributions)
        errors = sum(sum(issue.severity == "error" for issue in d.validation_issues) for d in distributions)
        return RepublisherPackage(
            mission_id=request.mission_id,
            distributions=distributions,
            transformed_assets=all_assets,
            platform_metadata={d.platform.value: d.metadata for d in distributions},
            validation_results=validation_results,
            optimization_summaries=optimization_summaries,
            distribution_plan=plan,
            execution_metrics=ExecutionMetrics(
                execution_time_ms=(completed - started).total_seconds() * 1000,
                platforms_requested=len(plan.ordered_platforms),
                platforms_processed=len(distributions),
                platforms_failed=failed,
                assets_generated=len(all_assets),
                validation_errors=errors,
            ),
            generated_at=completed,
        )

    @staticmethod
    def _source(request: RepurposeRequest) -> tuple[str, str, str]:
        if request.script:
            title = request.script.title
            text_parts: list[str] = []
            for section in request.script.sections:
                content = getattr(section, "content", None) or getattr(section, "text", None) or ""
                if content:
                    text_parts.append(content)
            return title, " ".join(text_parts) or "Source script contains no section text.", f"script:{request.script.script_id}"
        if request.video and request.video.video_uri:
            metadata = next(iter(request.publish.platform_metadata.values()), None)
            return (metadata.title if metadata else "Repurposed Content", metadata.description if metadata else "Published content.", request.video.video_uri)
        metadata = next(iter(request.publish.platform_metadata.values()), None)
        return (metadata.title if metadata else "Repurposed Content", metadata.description if metadata else "Published content.", "publish_metadata")

    @staticmethod
    def _optimization_summary(request: RepurposeRequest, platform: str) -> list[str]:
        summaries = [f"Preserved canonical publication metadata for {platform}."]
        for recommendation in request.evolution.optimization_recommendations[:3]:
            summaries.append(f"Evolution guidance: {recommendation.action}")
        for trend in request.analytics.trend_analysis.reports[:2]:
            summaries.append(f"Analytics signal: {trend.name} is {trend.direction}.")
        return summaries

# Agent boundary lives in this module so the repository has a single public
class OmniRepublisher:
    """AN-17-compatible orchestration boundary for AN-15."""

    def __init__(self, *, config: RepurposeConfig | None = None, logger: AlphaLogger | None = None,
                 event_bus: EventBus | None = None, service: RepublisherService | None = None,
                 adapters: AdapterRegistry | None = None) -> None:
        self.settings = config or RepurposeConfig.from_shared_config()
        self._logger = logger or get_agent_logger(AgentID.OMNI_REPUBLISHER)
        self._event_bus = event_bus or get_event_bus()
        self._service = service or RepublisherService(adapters=adapters)

    def execute(self, request: RepurposeRequest) -> RepublisherPackage:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        config = self._effective_config(request.runtime_overrides)
        self._logger.info("Omni repurposing started.", category=LogCategory.AGENT,
                          mission_id=request.mission_id, agent_id=AgentID.OMNI_REPUBLISHER)
        self._event_bus.emit(EventName.AGENT_STARTED, mission_id=request.mission_id,
                             agent_id=AgentID.OMNI_REPUBLISHER, payload={"stage": "repurposing"})
        try:
            package = self._service.execute(request, config)
        except AlphaBaseException:
            raise
        except Exception as exc:
            raise AgentExecutionError("AN-15 repurposing failed unexpectedly.", agent_id=AgentID.OMNI_REPUBLISHER,
                                      mission_id=request.mission_id, retryable=False, cause=exc) from exc
        elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        package = package.model_copy(update={
            "execution_metrics": package.execution_metrics.model_copy(update={"execution_time_ms": elapsed})
        })
        self._logger.info("Omni repurposing completed.", category=LogCategory.AGENT,
                          mission_id=request.mission_id, agent_id=AgentID.OMNI_REPUBLISHER,
                          execution_time_ms=elapsed, metadata={"platforms": len(package.distributions)})
        self._event_bus.emit(EventName.AGENT_COMPLETED, mission_id=request.mission_id,
                             agent_id=AgentID.OMNI_REPUBLISHER,
                             payload={"platforms": str(len(package.distributions))})
        return package

    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started = datetime.now(timezone.utc)
            try:
                request = self._request_from_dependencies(context)
                package = self.execute(request)
                status = ExecutionStatus.PARTIAL_SUCCESS if package.execution_metrics.platforms_failed else ExecutionStatus.SUCCESS
                return AgentResult(agent_id=AgentID.OMNI_REPUBLISHER, mission_id=context.mission_id,
                                   status=status, payload=package, started_at=started,
                                   completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:
                return self._failure(context, started, exc)
            except Exception as exc:
                wrapped = AgentExecutionError("AN-15 execution failed unexpectedly.", agent_id=AgentID.OMNI_REPUBLISHER,
                                              mission_id=context.mission_id, retryable=False, cause=exc)
                return self._failure(context, started, wrapped)
        return handler

    def _request_from_dependencies(self, context: AgentExecutionContext) -> RepurposeRequest:
        publish = self._dependency(context, AgentID.PUBLISHER)
        analytics = self._dependency(context, AgentID.ANALYTICS_BRAIN)
        evolution = self._dependency(context, AgentID.EVOLUTION_ENGINE)
        script = self._optional_dependency(context, AgentID.SCRIPT_FORGE)
        video = self._optional_dependency(context, AgentID.VIDEO_FORGE)
        thumbnail = self._optional_dependency(context, AgentID.THUMBNAIL_STUDIO)
        if not isinstance(publish, PublishPackage):
            raise ValidationError("AN-15 requires PublishPackage from AN-12.", agent_id=AgentID.OMNI_REPUBLISHER, mission_id=context.mission_id)
        if not isinstance(analytics, AnalyticsReport):
            raise ValidationError("AN-15 requires AnalyticsReport from AN-13.", agent_id=AgentID.OMNI_REPUBLISHER, mission_id=context.mission_id)
        if not isinstance(evolution, EvolutionReport):
            raise ValidationError("AN-15 requires EvolutionReport from AN-14.", agent_id=AgentID.OMNI_REPUBLISHER, mission_id=context.mission_id)
        return RepurposeRequest(mission_id=context.mission_id, publish=publish, analytics=analytics,
                                evolution=evolution, script=script, video=video, thumbnail=thumbnail)

    @staticmethod
    def _dependency(context: AgentExecutionContext, agent_id: AgentID) -> Any:
        value = OmniRepublisher._find_dependency(context, agent_id)
        if value is None:
            raise ValidationError("Required upstream dependency is missing.", agent_id=AgentID.OMNI_REPUBLISHER,
                                  mission_id=context.mission_id, context={"dependency": agent_id.value})
        return value

    @staticmethod
    def _optional_dependency(context: AgentExecutionContext, agent_id: AgentID) -> Any | None:
        return OmniRepublisher._find_dependency(context, agent_id)

    @staticmethod
    def _find_dependency(context: AgentExecutionContext, agent_id: AgentID) -> Any | None:
        normalized = agent_id.value.lower().replace("-", "")
        for key, result in context.dependency_results.items():
            result_agent = getattr(result, "agent_id", None)
            result_normalized = result_agent.value.lower().replace("-", "") if result_agent else ""
            key_normalized = key.lower().replace("_", "").replace("-", "")
            if (key_normalized in {normalized, agent_id.name.lower().replace("_", "")} or result_normalized == normalized) and result.payload is not None:
                return result.payload
        return None

    @staticmethod
    def _validate_request(request: RepurposeRequest) -> None:
        if any(package.mission_id != request.mission_id for package in (request.publish, request.analytics, request.evolution)):
            raise ValidationError("AN-15 received mismatched mission identifiers.", agent_id=AgentID.OMNI_REPUBLISHER, mission_id=request.mission_id)

    def _effective_config(self, overrides: dict[str, Any]) -> RepurposeConfig:
        values = self.settings.model_dump()
        values.update(overrides)
        return RepurposeConfig(**values)

    @staticmethod
    def _failure(context: AgentExecutionContext, started: datetime, exc: AlphaBaseException) -> AgentResult[BaseModel]:
        return AgentResult(agent_id=AgentID.OMNI_REPUBLISHER, mission_id=context.mission_id,
                           status=ExecutionStatus.FAILED, payload=None, error=exc.to_error_report(),
                           started_at=started, completed_at=datetime.now(timezone.utc))


__all__ = ["OmniRepublisher", "RepublisherService"]
