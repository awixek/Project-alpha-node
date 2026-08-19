"""AN-13 Analytics Brain orchestration boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from agents.an01.models import ResearchBatch
from agents.an03.models import ScriptDocument
from agents.an17.dispatcher import AgentExecutionContext
from agents.an13.analyzer import AnalyticsAnalyzer
from agents.an13.collectors import AnalyticsProvider, PublishingBaselineCollector
from agents.an13.insights import InsightEngine
from agents.an13.models import AnalyticsConfig, AnalyticsReport, AnalyticsRequest, NormalizedMetric
from agents.an13.recommendations import RecommendationEngine
from agents.an13.scoring import AnalyticsScorer
from agents.an13.trends import TrendDetector
from agents.an04.models import SEOResult
from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an07.models import VoicePackage
from agents.an08.models import SubtitlePackage
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from agents.an11.models import QualityReport
from agents.an12.models import PublishPackage
from shared.constants import AgentID, LogCategory, Platform
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ExecutionStatus


class AnalyticsBrain:
    def __init__(self, *, providers: list[AnalyticsProvider] | None = None,
                 config: AnalyticsConfig | None = None, logger: AlphaLogger | None = None,
                 event_bus: EventBus | None = None) -> None:
        self.settings = config or AnalyticsConfig.from_shared_config()
        self._providers = list(providers or [])
        self._baseline = PublishingBaselineCollector()
        self._analyzer = AnalyticsAnalyzer()
        self._scorer = AnalyticsScorer()
        self._trends = TrendDetector()
        self._insights = InsightEngine()
        self._recommendations = RecommendationEngine()
        self._logger = logger or get_agent_logger(AgentID.ANALYTICS_BRAIN)
        self._event_bus = event_bus or get_event_bus()

    def register_provider(self, provider: AnalyticsProvider) -> None:
        self._providers.append(provider)

    def execute(self, request: AnalyticsRequest) -> AnalyticsReport:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        config = self._effective_config(request.runtime_overrides)
        self._logger.info("Analytics collection started.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.ANALYTICS_BRAIN)
        metrics: list[NormalizedMetric] = []
        platforms = list(dict.fromkeys(r.platform for r in request.publish.platform_records))
        if not platforms:
            platforms = [Platform.YOUTUBE]
        provider_failures = 0
        for platform in platforms:
            metrics.extend(self._baseline.collect(request, platform))
            for provider in self._providers:
                try:
                    metrics.extend(provider.collect(request, platform))
                except AlphaBaseException:
                    provider_failures += 1
                    self._logger.warning("Analytics provider failed; continuing with remaining providers.", category=LogCategory.API,
                                         mission_id=request.mission_id, agent_id=AgentID.ANALYTICS_BRAIN, metadata={"provider": provider.name, "platform": platform.value})
                except Exception as exc:
                    provider_failures += 1
                    self._logger.warning("Analytics provider failed unexpectedly; continuing.", category=LogCategory.API,
                                         mission_id=request.mission_id, agent_id=AgentID.ANALYTICS_BRAIN, metadata={"provider": provider.name, "platform": platform.value, "error": type(exc).__name__})
        trend = self._trends.detect(metrics, config)
        seo_score = request.seo.seo_score
        thumb_score = request.thumbnail.ctr_report.top_score
        publishing_score = self._publishing_score(request.publish)
        audience_score = self._audience_score(metrics)
        quality_score = request.quality.overall_score
        scores = self._scorer.score(metrics, seo=seo_score, thumbnail=thumb_score, publishing=publishing_score,
                                     audience=audience_score, quality=quality_score, weights=config.effective_weights())
        score_values = {k: v.score for k, v in scores.items() if k != "overall"}
        recommendations = self._recommendations.generate(request, scores=score_values, config=config)
        insights = self._analyzer.audience_insights(metrics)
        completed = datetime.now(timezone.utc)
        confidence = min(1.0, mean_confidence(metrics)) if metrics else 0.25
        return AnalyticsReport(
            mission_id=request.mission_id,
            normalized_metrics=metrics,
            trend_analysis=trend,
            performance_scores=scores,
            audience_insights=insights,
            seo_insights=self._insights.seo(request, metrics),
            thumbnail_insights=self._insights.thumbnail(request),
            publishing_insights=self._insights.publishing(request),
            recommendation_report=recommendations,
            confidence_metrics={"overall": confidence, "provider_failure_rate": provider_failures / max(1, len(self._providers) * len(platforms))},
            execution_statistics={"execution_time_ms": (completed-started).total_seconds()*1000, "metric_count": len(metrics), "provider_failures": provider_failures},
            generated_at=completed,
        )

    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started = datetime.now(timezone.utc)
            try:
                request = self._request_from_dependencies(context)
                package = self.execute(request)
                return AgentResult(agent_id=AgentID.ANALYTICS_BRAIN, mission_id=context.mission_id, status=ExecutionStatus.SUCCESS,
                                   payload=package, started_at=started, completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:
                return self._failure(context, started, exc)
            except Exception as exc:
                wrapped = AgentExecutionError("Analytics execution failed unexpectedly.", agent_id=AgentID.ANALYTICS_BRAIN,
                                              mission_id=context.mission_id, retryable=False, cause=exc)
                return self._failure(context, started, wrapped)
        return handler

    def _request_from_dependencies(self, context: AgentExecutionContext) -> AnalyticsRequest:
        publish = self._dependency(context, AgentID.PUBLISHER)
        quality = self._dependency(context, AgentID.QUALITY_SENTINEL)
        seo = self._dependency(context, AgentID.SEO_BRAIN)
        thumbnail = self._dependency(context, AgentID.THUMBNAIL_STUDIO)
        video = self._dependency(context, AgentID.VIDEO_FORGE)
        values = (publish, quality, seo, thumbnail, video)
        types = (PublishPackage, QualityReport, SEOResult, ThumbnailPackage, VideoPackage)
        for value, expected in zip(values, types):
            if not isinstance(value, expected):
                raise ValidationError(f"AN-13 requires {expected.__name__}.", agent_id=AgentID.ANALYTICS_BRAIN, mission_id=context.mission_id)
        return AnalyticsRequest(mission_id=context.mission_id, publish=publish, quality=quality, seo=seo, thumbnail=thumbnail, video=video)

    @staticmethod
    def _dependency(context: AgentExecutionContext, agent_id: AgentID) -> Any:
        normalized = agent_id.value.lower().replace("-", "")
        for key, result in context.dependency_results.items():
            key_normalized = key.lower().replace("_", "").replace("-", "")
            result_agent = getattr(result, "agent_id", None)
            result_normalized = result_agent.value.lower().replace("-", "") if result_agent else ""
            if (key_normalized in {normalized, agent_id.name.lower().replace("_", "")} or result_normalized == normalized) and result.payload is not None:
                return result.payload
        raise ValidationError("Required upstream dependency is missing.", agent_id=AgentID.ANALYTICS_BRAIN,
                              mission_id=context.mission_id, context={"dependency": agent_id.value})

    @staticmethod
    def _validate_request(request: AnalyticsRequest) -> None:
        packages = (request.publish, request.quality, request.seo, request.thumbnail, request.video)
        if any(getattr(package, "mission_id", request.mission_id) != request.mission_id for package in packages):
            raise ValidationError("AN-13 received mismatched mission identifiers.", agent_id=AgentID.ANALYTICS_BRAIN, mission_id=request.mission_id)

    def _effective_config(self, overrides: dict[str, Any]) -> AnalyticsConfig:
        values = self.settings.model_dump()
        values.update(overrides)
        return AnalyticsConfig(**values)

    @staticmethod
    def _publishing_score(package: PublishPackage) -> float:
        requested = len(package.platform_records)
        verified = sum(1 for r in package.platform_records if r.status.value == "verified")
        return 100.0 if requested == 0 else verified / requested * 100

    @staticmethod
    def _audience_score(metrics: list[NormalizedMetric]) -> float:
        if not metrics:
            return 0.0
        retention = [m.audience_retention for m in metrics if m.audience_retention is not None]
        traffic = sum(m.search_traffic + m.recommendation_traffic for m in metrics)
        views = sum(m.views for m in metrics)
        retention_score = (sum(retention) / len(retention) * 100) if retention else 0
        traffic_score = min(100.0, traffic / max(1, views) * 100)
        return (retention_score * .7 + traffic_score * .3) if retention else traffic_score

    @staticmethod
    def _failure(context: AgentExecutionContext, started: datetime, exc: AlphaBaseException) -> AgentResult[BaseModel]:
        return AgentResult(agent_id=AgentID.ANALYTICS_BRAIN, mission_id=context.mission_id, status=ExecutionStatus.FAILED,
                           payload=None, error=exc.to_error_report(), started_at=started, completed_at=datetime.now(timezone.utc))


def mean_confidence(metrics: list[NormalizedMetric]) -> float:
    if not metrics:
        return 0.0
    provider = [m for m in metrics if m.source.value == "provider"]
    return 0.9 if provider else 0.45


__all__ = ["AnalyticsBrain"]
