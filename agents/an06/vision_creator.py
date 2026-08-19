from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Mapping
from pydantic import BaseModel
from agents.an05.models import VisionPlan
from agents.an17.dispatcher import AgentExecutionContext
from shared.config import get_config
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, InputValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ErrorReport, ExecutionStatus, WorkflowEvent
from .asset_manager import AssetManager
from .continuity import ContinuityValidator
from .models import AssetPackage, GenerationKind, GenerationMetrics, GenerationRequest, ProviderHealth, VisionCreatorConfig, VisionCreatorRequest
from .provider import VisionProviderRouter
from .quality import AssetQualityValidator
from .renderer import SceneRenderer

class VisionCreator:
    def __init__(self, *, provider_router: VisionProviderRouter|None=None, config: VisionCreatorConfig|None=None, event_bus: EventBus|None=None, logger: AlphaLogger|None=None) -> None:
        self._config=config or self._load_config(); self._providers=provider_router or VisionProviderRouter(config=self._config); self._event_bus=event_bus or get_event_bus(); self._logger=logger or get_agent_logger(AgentID.VISION_CREATOR); self._quality=AssetQualityValidator(); self._continuity=ContinuityValidator()
    @staticmethod
    def _load_config() -> VisionCreatorConfig:
        settings=get_config().agents.get(AgentID.VISION_CREATOR.value); return VisionCreatorConfig(**(dict(settings.settings) if settings else {}))
    def execute(self, request: VisionCreatorRequest) -> AssetPackage:
        self._validate(request); config=self._merge_config(request.runtime_overrides); manager=AssetManager(); renderer=SceneRenderer(self._providers,config=config,logger=self._logger)
        metrics=GenerationMetrics(scenes_requested=len(request.vision_plan.scenes)); providers:dict[str,ProviderHealth]={}; failures=[]
        self._publish(EventName.AGENT_STARTED,request.mission_id,{"agent":AgentID.VISION_CREATOR.value})
        for scene in request.vision_plan.scenes:
            scene_outcomes = 0
            requests = [self._scene_request(request.mission_id, scene, config, GenerationKind.IMAGE)]
            if config.generate_video_assets:
                requests.append(self._scene_request(request.mission_id, scene, config, GenerationKind.VIDEO))
            for req in requests:
                try:
                    outcome=renderer.render(req); manager.add(outcome.asset); scene_outcomes += 1; metrics.assets_generated+=1; metrics.total_generation_time_ms+=outcome.elapsed_ms
                    health=providers.setdefault(outcome.provider,ProviderHealth(provider=outcome.provider)); health.requests+=1; health.successes+=1
                except AlphaBaseException as exc:
                    failures.append(f"Scene {scene.scene_number} ({req.kind.value}): {exc}")
                except Exception:
                    failures.append(f"Scene {scene.scene_number} ({req.kind.value}): generation failed.")
            if scene_outcomes == len(requests):
                metrics.scenes_completed += 1
            else:
                metrics.scenes_failed += 1
                self._logger.warning("AN-06 scene generation failed; continuing.",category=LogCategory.AGENT,mission_id=request.mission_id,agent_id=AgentID.VISION_CREATOR,metadata={"scene_id":scene.scene_number})
            if scene_outcomes == len(requests):
                continue
            # Failures are retained in the package; other scenes continue.
            continue
        assets=manager.assets(); quality=self._quality.validate(assets,config); continuity=self._continuity.validate(request.vision_plan,assets); optimization=manager.optimize(config.optimization_level)
        if failures: quality=quality.model_copy(update={"passed":False,"findings":[*quality.findings,*failures]})
        metrics.retries=sum(p.retries for p in providers.values())
        package=AssetPackage(mission_id=request.mission_id,assets=assets,asset_manifest=manager.build_manifest(),generation_metrics=metrics,provider_statistics=list(providers.values()),quality_report=quality,continuity_report=continuity,optimization_report=optimization,production_metadata={"agent":AgentID.VISION_CREATOR.value,"status":"partial" if failures else "complete"})
        self._publish(EventName.AGENT_COMPLETED if not failures else EventName.AGENT_FAILED,request.mission_id,{"agent":AgentID.VISION_CREATOR.value,"assets":str(len(assets))})
        return package
    def as_agent_handler(self):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started=datetime.now(timezone.utc)
            try:
                plan=self._extract(context); package=self.execute(VisionCreatorRequest(mission_id=context.mission_id,vision_plan=plan)); status=ExecutionStatus.PARTIAL_SUCCESS if package.generation_metrics.scenes_failed else ExecutionStatus.SUCCESS
                error=ErrorReport(agent_id=AgentID.VISION_CREATOR,severity="warning",code="partial_generation",message="One or more scenes failed; partial AssetPackage returned.",retryable=True,context={"failed_scenes":str(package.generation_metrics.scenes_failed)}) if status is ExecutionStatus.PARTIAL_SUCCESS else None
                return AgentResult[AssetPackage](agent_id=AgentID.VISION_CREATOR,mission_id=context.mission_id,status=status,payload=package,error=error,started_at=started,completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc: return AgentResult[BaseModel](agent_id=AgentID.VISION_CREATOR,mission_id=context.mission_id,status=ExecutionStatus.FAILED,error=exc.to_error_report(),started_at=started,completed_at=datetime.now(timezone.utc))
            except Exception as exc:
                wrapped=AgentExecutionError("AN-06 execution failed unexpectedly.",agent_id=AgentID.VISION_CREATOR,mission_id=context.mission_id,retryable=False,cause=exc)
                return AgentResult[BaseModel](agent_id=AgentID.VISION_CREATOR,mission_id=context.mission_id,status=ExecutionStatus.FAILED,error=wrapped.to_error_report(),started_at=started,completed_at=datetime.now(timezone.utc))
        return handler
    def _merge_config(self, overrides: Mapping[str,Any]) -> VisionCreatorConfig: values=self._config.model_dump(); values.update(dict(overrides)); return VisionCreatorConfig(**values)
    @staticmethod
    def _validate(request: VisionCreatorRequest) -> None:
        if request.mission_id!=request.vision_plan.mission_id: raise InputValidationError("VisionPlan mission_id does not match execution mission_id.",agent_id=AgentID.VISION_CREATOR,mission_id=request.mission_id)
        if not request.vision_plan.scenes: raise InputValidationError("AN-06 requires at least one scene.",agent_id=AgentID.VISION_CREATOR,mission_id=request.mission_id)
    @staticmethod
    def _scene_request(mission_id,scene,config,kind=GenerationKind.IMAGE):
        prompt = scene.image_prompt if kind is GenerationKind.IMAGE else scene.video_prompt
        return GenerationRequest(mission_id=mission_id,scene_id=scene.scene_number,kind=kind,prompt=prompt,negative_prompt=scene.negative_prompt,provider=config.preferred_provider,resolution=config.image_resolution,aspect_ratio=config.aspect_ratio,quality_level=config.quality_level,realism_level=config.realism_level,output_format=config.output_format,language=config.language,metadata={"video_prompt":scene.video_prompt,"transition":scene.transition_type.value})
    @staticmethod
    def _extract(context: AgentExecutionContext)->VisionPlan:
        for result in context.dependency_results.values():
            if isinstance(result.payload,VisionPlan): return result.payload
        raise InputValidationError("AN-06 could not find a VisionPlan in dependency results.",agent_id=AgentID.VISION_CREATOR,mission_id=context.mission_id)
    def _publish(self,event_type:EventName,mission_id,payload:dict[str,str])->None:
        try: self._event_bus.publish(WorkflowEvent(mission_id=mission_id,agent_id=AgentID.VISION_CREATOR,event_type=event_type.value,payload=payload))
        except Exception: self._logger.warning("AN-06 event publication failed; continuing.",category=LogCategory.EVENT,mission_id=mission_id,agent_id=AgentID.VISION_CREATOR)
