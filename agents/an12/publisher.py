from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel

from agents.an17.dispatcher import AgentExecutionContext
from agents.an04.models import SEOResult
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from agents.an11.models import QualityDecision, QualityReport
from shared.constants import AgentID, EventName, LogCategory, Platform
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.retry import RetryExecutor
from shared.schemas import AgentResult, ExecutionStatus

from .adapters import AdapterRouter, PublishingAdapter, VerificationAdapter
from .audit import PublishAuditor
from .metadata import MetadataBuilder
from .models import PublicationStatus, PublisherConfig, PublishPackage, PublishRequest, PublishMetrics, SchedulingMode
from .publish_engine import PublishEngine
from .scheduler import PublicationScheduler
from .verifier import PublicationVerifier


class Publisher:
    """AN-12 multi-platform publication orchestrator."""

    def __init__(self, *, settings: PublisherConfig | None = None, router: AdapterRouter | None = None,
                 logger: AlphaLogger | None = None, event_bus: EventBus | None = None,
                 retry_executor: RetryExecutor | None = None) -> None:
        self.settings = settings or PublisherConfig.from_shared_config()
        self._router = router or AdapterRouter(timeout=self.settings.timeout, max_attempts=self.settings.max_attempts)
        self._verifier = PublicationVerifier()
        self._engine = PublishEngine(self._router, self._verifier)
        self._metadata = MetadataBuilder()
        self._scheduler = PublicationScheduler()
        self._auditor = PublishAuditor()
        self._logger = logger or get_agent_logger(AgentID.PUBLISHER)
        self._event_bus = event_bus or get_event_bus()
        self._retry_executor = retry_executor or RetryExecutor()

    def register_adapter(self, adapter: PublishingAdapter, *, priority: int = 10) -> None:
        self._router.register(adapter, priority=priority)

    def register_verifier(self, adapter: VerificationAdapter) -> None:
        self._verifier.register(adapter)

    def execute(self, request: PublishRequest) -> PublishPackage:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        config = self._effective_config(request.runtime_overrides)
        self._logger.info("Publishing started.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.PUBLISHER)
        self._event_bus.emit(EventName.AGENT_STARTED, mission_id=request.mission_id, agent_id=AgentID.PUBLISHER, payload={"stage": "publishing"})

        if request.quality.final_decision not in {QualityDecision.PASS, QualityDecision.PASS_WITH_WARNINGS}:
            raise ValidationError("Publishing is not eligible until AN-11 returns PASS or PASS_WITH_WARNINGS.", agent_id=AgentID.PUBLISHER, mission_id=request.mission_id, context={"decision": request.quality.final_decision.value})

        scheduled_at = self._scheduler.resolve(request, config)
        platforms = self._ordered_platforms(request.platforms or config.enabled_platforms, config)
        if not platforms:
            raise ValidationError("No publishing platforms were configured.", agent_id=AgentID.PUBLISHER, mission_id=request.mission_id)

        records = []
        all_attempts = []
        metadata_map = {}
        urls = {}
        notes = []
        if config.dry_run or request.scheduling_mode is SchedulingMode.DRY_RUN:
            notes.append("Dry-run mode: no external publication was attempted.")
            for platform in platforms:
                metadata = self._metadata.build(seo=request.seo, platform=platform, config=config, scheduled_at=scheduled_at)
                metadata_map[platform.value] = metadata
                records.append(self._dry_record(platform, scheduled_at))
        else:
            for platform in platforms:
                metadata = self._metadata.build(seo=request.seo, platform=platform, config=config, scheduled_at=scheduled_at)
                metadata_map[platform.value] = metadata
                record, attempts = self._engine.publish(
                    mission_id=request.mission_id,
                    platform=platform,
                    video_uri=self._video_uri(request.video),
                    thumbnail_uri=self._thumbnail_uri(request.thumbnail),
                    metadata=metadata,
                    config=config,
                )
                records.append(record)
                all_attempts.extend(attempts)
                if record.url and record.status is PublicationStatus.VERIFIED:
                    urls[platform.value] = record.url

        completed = datetime.now(timezone.utc)
        succeeded = sum(r.status is PublicationStatus.VERIFIED for r in records)
        failed = sum(r.status is PublicationStatus.FAILED for r in records)
        if failed and succeeded:
            status = PublicationStatus.PARTIAL
        elif failed:
            status = PublicationStatus.FAILED
        elif scheduled_at and request.scheduling_mode is not SchedulingMode.IMMEDIATE:
            status = PublicationStatus.SCHEDULED
        else:
            status = PublicationStatus.VERIFIED
        audit = self._auditor.build(started_at=started, completed_at=completed, quality_decision=request.quality.final_decision,
                                    requested=platforms, records=records, config=config, notes=notes)
        metrics = PublishMetrics(execution_time_ms=(completed-started).total_seconds()*1000,
                                 platforms_requested=len(platforms), platforms_succeeded=succeeded,
                                 platforms_failed=failed, retries=sum(max(0, a.attempt_number-1) for a in all_attempts),
                                 verified_publications=succeeded)
        package = PublishPackage(mission_id=request.mission_id, status=status, platform_records=records,
                                 published_urls=urls, platform_metadata=metadata_map, verification_report=[r.verification for r in records if r.verification],
                                 retry_history=all_attempts, publishing_history=records, audit_report=audit, execution_metrics=metrics)
        self._logger.info("Publishing completed.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.PUBLISHER,
                          execution_time_ms=metrics.execution_time_ms, metadata={"status": status.value, "platforms": len(platforms)})
        self._event_bus.emit(EventName.AGENT_COMPLETED, mission_id=request.mission_id, agent_id=AgentID.PUBLISHER, payload={"status": status.value})
        return package

    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started = datetime.now(timezone.utc)
            try:
                video = self._dependency(context, AgentID.VIDEO_FORGE)
                thumbnail = self._dependency(context, AgentID.THUMBNAIL_STUDIO)
                quality = self._dependency(context, AgentID.QUALITY_SENTINEL)
                seo = self._dependency(context, AgentID.SEO_BRAIN)
                if not isinstance(video, VideoPackage):
                    raise ValidationError("AN-12 requires VideoPackage from AN-09.", agent_id=AgentID.PUBLISHER, mission_id=context.mission_id)
                if not isinstance(thumbnail, ThumbnailPackage):
                    raise ValidationError("AN-12 requires ThumbnailPackage from AN-10.", agent_id=AgentID.PUBLISHER, mission_id=context.mission_id)
                if not isinstance(quality, QualityReport):
                    raise ValidationError("AN-12 requires QualityReport from AN-11.", agent_id=AgentID.PUBLISHER, mission_id=context.mission_id)
                if not isinstance(seo, SEOResult):
                    raise ValidationError("AN-12 requires SEOResult from AN-04.", agent_id=AgentID.PUBLISHER, mission_id=context.mission_id)
                package = self.execute(PublishRequest(mission_id=context.mission_id, video=video, thumbnail=thumbnail,
                                                      quality=quality, seo=seo, platforms=self.settings.enabled_platforms,
                                                      scheduling_mode=self.settings.scheduling_mode))
                return AgentResult(agent_id=AgentID.PUBLISHER, mission_id=context.mission_id, status=ExecutionStatus.SUCCESS,
                                   payload=package, started_at=started, completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:
                return self._failure(context, started, exc)
            except Exception as exc:
                wrapped = AgentExecutionError("Publishing failed unexpectedly.", agent_id=AgentID.PUBLISHER, mission_id=context.mission_id, cause=exc)
                return self._failure(context, started, wrapped)
        return handler

    @staticmethod
    def _dependency(context: AgentExecutionContext, agent_id: AgentID) -> Any:
        normalized = agent_id.value.lower().replace("-", "")
        for key, result in context.dependency_results.items():
            key_normalized = key.lower().replace("_", "").replace("-", "")
            result_agent = getattr(result, "agent_id", None)
            result_normalized = result_agent.value.lower().replace("-", "") if result_agent is not None else ""
            if (key_normalized in {normalized, agent_id.name.lower().replace("_", "")} or result_normalized == normalized) and result.payload is not None:
                return result.payload
        raise ValidationError("Required upstream dependency is missing.", agent_id=AgentID.PUBLISHER, mission_id=context.mission_id, context={"dependency": agent_id.value})

    @staticmethod
    def _validate_request(request: PublishRequest) -> None:
        if request.video.mission_id != request.mission_id or request.thumbnail.mission_id != request.mission_id or request.quality.mission_id != request.mission_id or request.seo.mission_id != request.mission_id:
            raise ValidationError("AN-12 received mismatched mission identifiers.", agent_id=AgentID.PUBLISHER, mission_id=request.mission_id)

    def _effective_config(self, overrides: dict[str, Any]) -> PublisherConfig:
        values = self.settings.model_dump()
        values.update(overrides)
        return PublisherConfig(**values)

    @staticmethod
    def _ordered_platforms(requested: list[Platform], config: PublisherConfig) -> list[Platform]:
        allowed = list(dict.fromkeys(requested))
        order = config.publishing_order or allowed
        return [p for p in order if p in allowed]

    @staticmethod
    def _video_uri(video: VideoPackage) -> str:
        for attr in ("video_uri", "output_uri", "rendered_uri"):
            value = getattr(video, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        raise ValidationError("VideoPackage does not expose a usable rendered video URI.", agent_id=AgentID.PUBLISHER, mission_id=video.mission_id)

    @staticmethod
    def _thumbnail_uri(thumbnail: ThumbnailPackage) -> str | None:
        for concept in thumbnail.ranked_concepts:
            value = getattr(concept, "image_uri", None)
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _dry_record(platform, scheduled_at):
        from .models import PublicationRecord
        return PublicationRecord(platform=platform, status=PublicationStatus.SCHEDULED if scheduled_at else PublicationStatus.SKIPPED, scheduled_at=scheduled_at)

    @staticmethod
    def _failure(context, started, exc):
        return AgentResult(agent_id=AgentID.PUBLISHER, mission_id=context.mission_id, status=ExecutionStatus.FAILED,
                           payload=None, error=exc.to_error_report(), started_at=started, completed_at=datetime.now(timezone.utc))


__all__ = ["Publisher"]
