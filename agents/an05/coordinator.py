"""AN-05 orchestration pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, InputValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import SourceRef

from .continuity import ContinuityManager
from .models import (
    AssetManifestItem,
    PromptPackage,
    VisionPlan,
    VisionPlanningConfig,
    VisionPlanningRequest,
)
from .planner import ScenePlanner
from .storyboard import StoryboardEngine


class VisionPlannerCoordinator:
    """Coordinates parsing, scene planning, continuity, validation and output."""

    def __init__(
        self,
        *,
        planner: ScenePlanner | None = None,
        continuity: ContinuityManager | None = None,
        storyboard: StoryboardEngine | None = None,
        config: VisionPlanningConfig | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._planner = planner or ScenePlanner()
        self._continuity = continuity or ContinuityManager()
        self._storyboard = storyboard or StoryboardEngine()
        self._config = config or VisionPlanningConfig.from_shared_config()
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.VISION_PLANNER)

    def run(self, request: VisionPlanningRequest) -> VisionPlan:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        self._emit(EventName.AGENT_STARTED, request.mission_id, {"operation": "visual_planning"})
        self._logger.info(
            "Vision Planner execution started.",
            category=LogCategory.AGENT,
            agent_id=AgentID.VISION_PLANNER,
            mission_id=request.mission_id,
        )
        try:
            config = self._apply_overrides(request)
            scenes = self._planner.build_scenes(request.script, config)
            uncertainty = [note for scene in scenes for note in scene.uncertainty_notes]
            continuity = self._continuity.build(scenes, uncertainty)
            issues = self._continuity.validate(scenes, continuity)
            duplicate_signatures = self._duplicate_scene_signatures(scenes)
            issues.extend(duplicate_signatures)
            storyboard = self._storyboard.build(scenes)
            runtime = sum(scene.duration_seconds or 0.0 for scene in scenes)
            if config.target_total_duration_seconds:
                runtime = min(runtime, config.target_total_duration_seconds)
            prompt_package = PromptPackage(
                image_prompts={scene.scene_number: scene.image_prompt for scene in scenes},
                video_prompts={scene.scene_number: scene.video_prompt for scene in scenes},
                negative_prompts={scene.scene_number: scene.negative_prompt for scene in scenes},
                style=config.preferred_style,
                language=config.language,
            )
            assets = self._asset_manifest(scenes)
            confidence = self._confidence(scenes, issues)
            plan = VisionPlan(
                mission_id=request.mission_id,
                shots=[scene.model_copy(update={"prompt": scene.image_prompt}) for scene in scenes],
                storyboard=storyboard,
                scenes=scenes,
                prompt_package=prompt_package,
                continuity_package=continuity,
                asset_manifest=assets,
                estimated_runtime_seconds=runtime,
                production_metadata={
                    "agent": AgentID.VISION_PLANNER.value,
                    "style": config.preferred_style.value,
                    "language": config.language,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "source_script_id": str(request.script.script_id),
                    "seo_input_present": str(request.seo_metadata is not None).lower(),
                },
                validation_issues=issues,
                uncertainty_notes=sorted(set(uncertainty)),
                overall_confidence=confidence,
            )
            self._emit(EventName.AGENT_COMPLETED, request.mission_id, {"scene_count": str(len(scenes))})
            self._logger.info(
                "Vision Planner execution completed.",
                category=LogCategory.AGENT,
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                metadata={"scene_count": len(scenes), "runtime_seconds": runtime, "confidence": confidence},
            )
            return plan
        except AlphaBaseException:
            raise
        except Exception as exc:  # noqa: BLE001 - coordinator boundary
            self._logger.exception(
                "Unexpected Vision Planner coordinator failure.",
                category=LogCategory.ERROR,
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
            )
            raise AgentExecutionError(
                "Vision Planner coordination failed unexpectedly.",
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "run"},
                cause=exc,
            ) from exc

    @staticmethod
    def _validate_request(request: VisionPlanningRequest) -> None:
        if request.mission_id != request.script.mission_id:
            raise InputValidationError(
                "Vision Planning mission_id must match ScriptDocument.mission_id.",
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                context={"operation": "validate_request"},
            )
        if not request.script.sections:
            raise InputValidationError(
                "Vision Planner requires at least one script section.",
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
            )

    def _apply_overrides(self, request: VisionPlanningRequest) -> VisionPlanningConfig:
        base = request.config or self._config
        values = base.model_dump()
        values.update(request.runtime_overrides)
        try:
            return VisionPlanningConfig.model_validate(values)
        except Exception as exc:
            raise InputValidationError(
                "Invalid AN-05 runtime configuration override.",
                agent_id=AgentID.VISION_PLANNER,
                mission_id=request.mission_id,
                context={"operation": "apply_overrides"},
                cause=exc,
            ) from exc

    @staticmethod
    def _duplicate_scene_signatures(scenes):
        seen: dict[str, int] = {}
        issues: list[str] = []
        for scene in scenes:
            signature = "|".join((scene.visual_goal.casefold(), scene.subject.casefold(), scene.environment.casefold()))
            if signature in seen:
                issues.append(f"Potential duplicate scene: {scene.scene_number} duplicates scene {seen[signature]}.")
            else:
                seen[signature] = scene.scene_number
        return issues

    @staticmethod
    def _asset_manifest(scenes):
        records: dict[str, AssetManifestItem] = {}
        for scene in scenes:
            environment_key = f"environment:{scene.environment.casefold()}"
            records.setdefault(environment_key, AssetManifestItem(
                asset_key=environment_key,
                asset_type="environment",
                purpose="Recurring environment continuity reference.",
                scenes=[],
                reusable=True,
            )).scenes.append(scene.scene_number)
            for character in scene.characters:
                key = f"character:{character.casefold()}"
                records.setdefault(key, AssetManifestItem(
                    asset_key=key,
                    asset_type="character_reference",
                    purpose="Recurring character continuity reference.",
                    scenes=[],
                    reusable=True,
                )).scenes.append(scene.scene_number)
        return list(records.values())

    @staticmethod
    def _confidence(scenes, issues):
        if not scenes:
            return 0.0
        base = sum(scene.confidence_score for scene in scenes) / len(scenes)
        return max(0.0, min(1.0, base - min(0.4, 0.05 * len(issues))))

    def _emit(self, event: EventName, mission_id: UUID, payload: dict[str, str]) -> None:
        try:
            self._event_bus.emit(event, mission_id=mission_id, agent_id=AgentID.VISION_PLANNER, payload=payload)
        except Exception:  # noqa: BLE001 - observability must not break planning
            self._logger.warning(
                "Failed to emit Vision Planner lifecycle event.",
                category=LogCategory.ERROR,
                agent_id=AgentID.VISION_PLANNER,
                mission_id=mission_id,
            )
