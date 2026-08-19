from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel

from agents.an03.models import ScriptDocument
from agents.an04.models import SEOResult
from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an09.models import VideoPackage
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ErrorReport, ExecutionStatus

from .analyzer import ThumbnailAnalyzer
from .models import ThumbnailConfig, ThumbnailConcept, ThumbnailPackage, ThumbnailRequest
from .planner import ThumbnailPlanner
from .provider import ThumbnailProvider, ThumbnailProviderRouter
from .scorer import CTRScorer


class ThumbnailStudio:
    """AN-10 intelligence engine for fact-safe, CTR-oriented thumbnail concepts."""

    def __init__(self, *, settings: ThumbnailConfig | None = None,
                 router: ThumbnailProviderRouter | None = None,
                 logger: AlphaLogger | None = None,
                 event_bus: EventBus | None = None) -> None:
        self.settings = settings or ThumbnailConfig.from_shared_config()
        self._router = router or ThumbnailProviderRouter()
        self._logger = logger or get_agent_logger(AgentID.THUMBNAIL_STUDIO)
        self._event_bus = event_bus or get_event_bus()
        self._analyzer = ThumbnailAnalyzer()
        self._planner = ThumbnailPlanner()
        self._scorer = CTRScorer()

    def register_provider(self, provider: ThumbnailProvider, *, priority: int = 10) -> None:
        self._router.register(provider, priority=priority)

    def execute(self, request: ThumbnailRequest) -> ThumbnailPackage:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        config = self._effective_config(request.runtime_overrides)
        self._logger.info("Thumbnail analysis started.", category=LogCategory.AGENT,
                          mission_id=request.mission_id, agent_id=AgentID.THUMBNAIL_STUDIO)
        self._event_bus.emit(EventName.AGENT_STARTED, mission_id=request.mission_id,
                             agent_id=AgentID.THUMBNAIL_STUDIO, payload={"stage": "thumbnail_analysis"})
        analysis = self._analyzer.analyze(request.video, request.vision_plan, request.assets,
                                          request.script, request.seo)
        strategies = self._planner.choose_strategies(request.script, request.vision_plan,
                                                     request.seo, config.number_of_candidates)
        concepts: list[ThumbnailConcept] = []
        fingerprints: set[tuple[str, str, str]] = set()
        for strategy in strategies:
            raw = self._planner.build(strategy, analysis, request.vision_plan, request.script, config)
            fingerprint = (raw["focal_subject"].strip().lower(), (raw["text_overlay"] or "").strip().lower(), raw["layout"].composition.strip().lower())
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            prompt = self._prompt(raw, analysis, config)
            negative = "misleading text, fabricated historical details, excessive text, clutter, distorted anatomy, logos covering subject"
            score = self._scorer.score(analysis, raw["layout"], strategy.value, raw["text_overlay"], config)
            concepts.append(ThumbnailConcept(
                strategy=strategy, title=raw["title"], focal_subject=raw["focal_subject"],
                emotional_hook=raw["emotional_hook"], text_overlay=raw["text_overlay"], layout=raw["layout"],
                branding={"placement": raw["layout"].branding_region, "mode": config.branding},
                prompt=prompt, negative_prompt=negative, visual_analysis=analysis, ctr_score=score,
                supporting_scene_ids=[raw["scene_id"]], supporting_asset_ids=[a.asset_id for a in request.assets.assets if a.scene_id == raw["scene_id"]], factual_guardrails=[
                    "Use only subjects and claims supported by upstream production data.",
                    "Do not imply an event, identity, result, or comparison not established by the source material.",
                ],
            ))
        concepts.sort(key=lambda c: c.ctr_score.overall, reverse=True)
        for rank, concept in enumerate(concepts, 1):
            concept.rank = rank
        avg = sum(c.ctr_score.overall for c in concepts) / len(concepts) if concepts else 0.0
        report = {
            "candidates_considered": len(strategies), "candidates_ranked": len(concepts),
            "average_score": round(avg, 2), "top_score": round(concepts[0].ctr_score.overall, 2) if concepts else 0.0,
            "weights": config.ctr_weights,
            "methodology": ["Deterministic weighted scoring", "Upstream evidence only", "Duplicate concepts removed before ranking"],
        }
        package = ThumbnailPackage(mission_id=request.mission_id, ranked_concepts=concepts,
                                   recommendation=concepts[0].concept_id.hex if concepts else "No viable thumbnail concept",
                                   visual_analysis=analysis, ctr_report=report,
                                   generation_statistics={"strategies_generated": len(strategies),
                                                          "duplicates_removed": len(strategies) - len(concepts),
                                                          "execution_time_ms": (datetime.now(timezone.utc)-started).total_seconds()*1000})
        self._logger.info("Thumbnail analysis completed.", category=LogCategory.AGENT,
                          mission_id=request.mission_id, agent_id=AgentID.THUMBNAIL_STUDIO,
                          execution_time_ms=package.generation_statistics["execution_time_ms"])
        self._event_bus.emit(EventName.AGENT_COMPLETED, mission_id=request.mission_id,
                             agent_id=AgentID.THUMBNAIL_STUDIO, payload={"candidates": str(len(concepts))})
        return package

    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started = datetime.now(timezone.utc)
            try:
                video = self._dependency(context, AgentID.VIDEO_FORGE)
                vision = self._dependency(context, AgentID.VISION_PLANNER)
                assets = self._dependency(context, AgentID.VISION_CREATOR)
                script = self._dependency(context, AgentID.SCRIPT_FORGE)
                seo = self._dependency(context, AgentID.SEO_BRAIN, required=False)
                if not isinstance(video, VideoPackage):
                    raise ValidationError("AN-10 requires VideoPackage from AN-09.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=context.mission_id)
                if not isinstance(vision, VisionPlan):
                    raise ValidationError("AN-10 requires VisionPlan from AN-05.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=context.mission_id)
                if not isinstance(assets, AssetPackage):
                    raise ValidationError("AN-10 requires AssetPackage from AN-06.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=context.mission_id)
                if not isinstance(script, ScriptDocument):
                    raise ValidationError("AN-10 requires ScriptDocument from AN-03.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=context.mission_id)
                if seo is not None and not isinstance(seo, SEOResult):
                    raise ValidationError("AN-10 received an invalid SEO payload.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=context.mission_id)
                package = self.execute(ThumbnailRequest(mission_id=context.mission_id, video=video,
                                                        vision_plan=vision, assets=assets, script=script, seo=seo))
                return AgentResult(agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=context.mission_id,
                                   status=ExecutionStatus.SUCCESS, payload=package, started_at=started,
                                   completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:
                return self._failure(context, started, exc)
            except Exception as exc:
                wrapped = AgentExecutionError("Thumbnail analysis failed unexpectedly.", agent_id=AgentID.THUMBNAIL_STUDIO,
                                               mission_id=context.mission_id, retryable=False, cause=exc)
                return self._failure(context, started, wrapped)
        return handler

    @staticmethod
    def _dependency(context: AgentExecutionContext, agent_id: AgentID, *, required: bool = True) -> Any:
        aliases = {agent_id.value.lower(), agent_id.value.replace("-", "").lower()}
        for key, result in context.dependency_results.items():
            normalized = key.lower().replace("_", "").replace("-", "")
            if key == agent_id.value or normalized in {a.replace("-", "") for a in aliases}:
                return result.payload
        if required:
            raise ValidationError("Required upstream dependency is missing.", agent_id=AgentID.THUMBNAIL_STUDIO,
                                  mission_id=context.mission_id, context={"dependency": agent_id.value})
        return None

    @staticmethod
    def _validate_request(request: ThumbnailRequest) -> None:
        if request.mission_id != request.video.mission_id:
            raise ValidationError("VideoPackage mission_id does not match request.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=request.mission_id)
        if request.vision_plan.mission_id != request.mission_id or request.assets.mission_id != request.mission_id:
            raise ValidationError("Upstream mission identifiers are inconsistent.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=request.mission_id)
        if request.script.mission_id != request.mission_id:
            raise ValidationError("Script mission_id does not match request.", agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=request.mission_id)

    def _effective_config(self, overrides: dict[str, Any]) -> ThumbnailConfig:
        values = self.settings.model_dump()
        values.update(overrides)
        if "CTR_weights" in overrides and "ctr_weights" not in overrides:
            values["ctr_weights"] = overrides["CTR_weights"]
        return ThumbnailConfig(**values)

    @staticmethod
    def _prompt(raw: dict, analysis, config: ThumbnailConfig) -> str:
        return (
            f"Create a {config.thumbnail_style} editorial thumbnail at {config.aspect_ratio}. "
            f"Focal subject: {raw['focal_subject']}. Emotional hook: {raw['emotional_hook']}. "
            f"Composition: {raw['layout'].composition}. Negative space: {analysis.negative_space}. "
            f"Text overlay: {raw['text_overlay'] or 'none'}. Branding: {config.branding}, "
            f"palette: {config.color_palette}. Keep the visual factual, uncluttered, mobile-readable, "
            "and faithful to the supplied production evidence."
        )

    @staticmethod
    def _failure(context: AgentExecutionContext, started: datetime, exc: AlphaBaseException) -> AgentResult[BaseModel]:
        return AgentResult(agent_id=AgentID.THUMBNAIL_STUDIO, mission_id=context.mission_id,
                           status=ExecutionStatus.FAILED, payload=None, error=exc.to_error_report(),
                           started_at=started, completed_at=datetime.now(timezone.utc))
