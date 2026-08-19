from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an07.models import VoicePackage
from agents.an08.models import SubtitlePackage
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ErrorReport, ExecutionStatus

from .compositor import SceneCompositor
from .exporter import ExportEngine
from .models import (
    RenderJob, RenderMetrics, RenderSettings, RenderStatus, Timeline, VideoPackage,
    VideoProviderRequest, VideoRequest,
)
from .quality import VideoQualityValidator
from .renderer import VideoRenderRouter, VideoRenderProvider
from .timeline import TimelineBuilder
from .transitions import TransitionEngine


class VideoForge:
    """AN-09 execution engine: assembles upstream assets and renders a final video."""

    def __init__(self, *, settings: RenderSettings | None = None,
                 router: VideoRenderRouter | None = None,
                 logger: AlphaLogger | None = None,
                 event_bus: EventBus | None = None) -> None:
        self.settings = settings or RenderSettings.from_shared_config()
        self._router = router or VideoRenderRouter(settings=self.settings)
        self._logger = logger or get_agent_logger(AgentID.VIDEO_FORGE)
        self._event_bus = event_bus or get_event_bus()
        self._timeline = TimelineBuilder()
        self._transitions = TransitionEngine()
        self._compositor = SceneCompositor()
        self._quality = VideoQualityValidator()
        self._exporter = ExportEngine()

    def register_provider(self, provider: VideoRenderProvider, *, priority: int | None = None) -> None:
        self._router.register(provider, priority=priority)

    def execute(self, request: VideoRequest) -> VideoPackage:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        settings = self._effective_settings(request.runtime_overrides)
        self._logger.info("Video timeline creation started.", category=LogCategory.AGENT,
                          mission_id=request.mission_id, agent_id=AgentID.VIDEO_FORGE)
        timeline = self._timeline.build(request.vision_plan, request.asset_package,
                                        request.voice_package, request.subtitle_package, settings)
        transitions = self._transitions.build(timeline, settings)
        composition = self._compositor.compose(timeline, transitions, settings)
        quality, sync = self._quality.validate(timeline, request.asset_package,
                                               request.voice_package, request.subtitle_package)
        scene_uris = self._scene_asset_uris(request.asset_package)
        provider_request = VideoProviderRequest(
            mission_id=request.mission_id,
            timeline=timeline,
            render_settings=settings,
            scene_asset_uris=scene_uris,
            narration_uri=request.voice_package.narration_uri,
            subtitle_exports=request.subtitle_package.exported_formats,
            script_metadata=self._script_metadata(request.script),
        )
        self._event_bus.emit(EventName.AGENT_STARTED, mission_id=request.mission_id, agent_id=AgentID.VIDEO_FORGE,
                             payload={"stage": "render"})
        response = self._router.render(provider_request)
        export = self._exporter.export_metadata(response, settings)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        completed = response.completed_scene_ids or [scene.scene_id for scene in timeline.scenes]
        job = RenderJob(mission_id=request.mission_id, status=RenderStatus.COMPLETED,
                        completed_scene_ids=completed, remaining_scene_ids=[],
                        render_uri=response.video_uri, completed_at=datetime.now(timezone.utc))
        metrics = RenderMetrics(scenes_requested=len(timeline.scenes), scenes_composed=len(completed),
                                scenes_failed=max(0, len(timeline.scenes)-len(completed)),
                                rendered_frames=response.rendered_frames, retries=0,
                                render_time_ms=elapsed, cache_hits=response.cache_hits)
        usage = {
            "scene_count": len(timeline.scenes),
            "asset_count": len(request.asset_package.assets),
            "subtitle_tracks": len(request.subtitle_package.subtitle_tracks),
            "narration_segments": len(request.voice_package.narration_segments),
            "provider": response.provider,
            "composition": composition,
        }
        package = VideoPackage(
            mission_id=request.mission_id,
            video_uri=response.video_uri,
            timeline=timeline,
            render_job=job,
            render_metrics=metrics,
            export_metadata=export,
            quality_report=quality,
            synchronization_report=sync,
            asset_usage_report=usage,
            production_metadata={"provider": response.provider, "export_format": settings.export_format},
        )
        self._logger.info("Video rendering completed.", category=LogCategory.AGENT,
                          mission_id=request.mission_id, agent_id=AgentID.VIDEO_FORGE,
                          execution_time_ms=elapsed)
        self._event_bus.emit(EventName.AGENT_COMPLETED, mission_id=request.mission_id, agent_id=AgentID.VIDEO_FORGE,
                             payload={"video_uri": response.video_uri, "scenes": str(len(completed))})
        return package

    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started = datetime.now(timezone.utc)
            try:
                vision = self._dependency(context, AgentID.VISION_PLANNER)
                assets = self._dependency(context, AgentID.VISION_CREATOR)
                voice = self._dependency(context, AgentID.VOICE_CORE)
                subtitles = self._dependency(context, AgentID.SUBTITLE_ENGINE)
                if not isinstance(vision, VisionPlan):
                    raise ValidationError("AN-09 requires a VisionPlan from AN-05.", agent_id=AgentID.VIDEO_FORGE,
                                          mission_id=context.mission_id)
                if not isinstance(assets, AssetPackage):
                    raise ValidationError("AN-09 requires an AssetPackage from AN-06.", agent_id=AgentID.VIDEO_FORGE,
                                          mission_id=context.mission_id)
                if not isinstance(voice, VoicePackage):
                    raise ValidationError("AN-09 requires a VoicePackage from AN-07.", agent_id=AgentID.VIDEO_FORGE,
                                          mission_id=context.mission_id)
                if not isinstance(subtitles, SubtitlePackage):
                    raise ValidationError("AN-09 requires a SubtitlePackage from AN-08.", agent_id=AgentID.VIDEO_FORGE,
                                          mission_id=context.mission_id)
                request = VideoRequest(mission_id=context.mission_id, vision_plan=vision,
                                       asset_package=assets, voice_package=voice, subtitle_package=subtitles)
                package = self.execute(request)
                return AgentResult(agent_id=AgentID.VIDEO_FORGE, mission_id=context.mission_id,
                                   status=ExecutionStatus.SUCCESS, payload=package,
                                   started_at=started, completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:
                return self._failure(context, started, exc)
            except Exception as exc:
                wrapped = AgentExecutionError("Video rendering failed unexpectedly.", agent_id=AgentID.VIDEO_FORGE,
                                               mission_id=context.mission_id, retryable=False, cause=exc)
                return self._failure(context, started, wrapped)
        return handler

    @staticmethod
    def _dependency(context: AgentExecutionContext, agent_id: AgentID) -> Any:
        aliases = {agent_id.value.lower(), agent_id.value.replace("-", "").lower()}
        for key, result in context.dependency_results.items():
            normalized = key.lower().replace("_", "").replace("-", "")
            if key == agent_id.value or normalized in {a.replace("-", "") for a in aliases}:
                if result.payload is not None:
                    return result.payload
        raise ValidationError("Required upstream dependency is missing.", agent_id=AgentID.VIDEO_FORGE,
                              mission_id=context.mission_id, context={"dependency": agent_id.value})

    @staticmethod
    def _scene_asset_uris(package: AssetPackage) -> dict[int, list[str]]:
        result: dict[int, list[str]] = {}
        for asset in package.assets:
            result.setdefault(asset.scene_id, []).append(asset.storage_path)
        return result

    @staticmethod
    def _script_metadata(script: BaseModel | None) -> dict[str, str]:
        if script is None:
            return {}
        values = script.model_dump()
        return {key: str(value) for key, value in values.items() if isinstance(value, (str, int, float))}

    def _effective_settings(self, overrides: dict[str, Any]) -> RenderSettings:
        values = self.settings.model_dump()
        values.update({key: value for key, value in overrides.items() if key in values})
        return RenderSettings(**values)

    @staticmethod
    def _validate_request(request: VideoRequest) -> None:
        if request.mission_id != request.vision_plan.mission_id:
            raise ValidationError("Mission ID does not match VisionPlan.", agent_id=AgentID.VIDEO_FORGE,
                                  mission_id=request.mission_id)
        for name, value in (("AssetPackage", request.asset_package), ("VoicePackage", request.voice_package),
                            ("SubtitlePackage", request.subtitle_package)):
            if value.mission_id != request.mission_id:
                raise ValidationError(f"Mission ID does not match {name}.", agent_id=AgentID.VIDEO_FORGE,
                                      mission_id=request.mission_id)
        if not request.vision_plan.scenes:
            raise ValidationError("VisionPlan contains no scenes.", agent_id=AgentID.VIDEO_FORGE,
                                  mission_id=request.mission_id)
        if not request.asset_package.assets:
            raise ValidationError("AssetPackage contains no generated assets.", agent_id=AgentID.VIDEO_FORGE,
                                  mission_id=request.mission_id)

    @staticmethod
    def _failure(context: AgentExecutionContext, started: datetime, exc: AlphaBaseException) -> AgentResult[BaseModel]:
        return AgentResult(agent_id=AgentID.VIDEO_FORGE, mission_id=context.mission_id,
                           status=ExecutionStatus.FAILED, error=exc.to_error_report(),
                           started_at=started, completed_at=datetime.now(timezone.utc))


__all__ = ["VideoForge"]
