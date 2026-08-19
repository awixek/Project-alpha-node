"""AN-03 orchestration pipeline."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, InputValidationError
from shared.logger import AlphaLogger, get_agent_logger

from .analysis import EvidenceLinker, ScriptPlanner, ScriptQualityValidator, ScriptSectionPlanner
from .models import (
    ScriptDocument,
    ScriptOutline,
    ScriptGenerationConfig,
    ScriptGenerationRequest,
    ScriptMetadata,
    ScriptRequest,
    ScriptSection,
)
from .providers import ScriptGenerationProviderRegistry


class ScriptForgeCoordinator:
    """Coordinates planning, provider generation, evidence linking, and validation."""

    def __init__(
        self,
        *,
        providers: ScriptGenerationProviderRegistry,
        planner: ScriptPlanner | None = None,
        section_planner: ScriptSectionPlanner | None = None,
        evidence_linker: EvidenceLinker | None = None,
        validator: ScriptQualityValidator | None = None,
        config: ScriptGenerationConfig | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._providers = providers
        self._planner = planner or ScriptPlanner()
        self._section_planner = section_planner or ScriptSectionPlanner()
        self._evidence_linker = evidence_linker or EvidenceLinker()
        self._validator = validator or ScriptQualityValidator()
        self._config = config or ScriptGenerationConfig.from_shared_config()
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.SCRIPT_FORGE)

    def run(self, request: ScriptRequest) -> ScriptDocument:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        self._emit(EventName.AGENT_STARTED, request.mission_id, {"operation": "script_generation"})
        self._logger.info(
            "Script Forge verification started.",
            category=LogCategory.AGENT,
            agent_id=AgentID.SCRIPT_FORGE,
            mission_id=request.mission_id,
        )
        try:
            config = self._apply_overrides(request)
            outline = self._planner.build_outline(request, config)
            self._logger.info("Script narrative outline generated.", category=LogCategory.AGENT, agent_id=AgentID.SCRIPT_FORGE, mission_id=request.mission_id)

            evidence_sources = self._evidence_linker.collect_sources(request)
            evidence_context = self._evidence_linker.evidence_context(request, citation_mode=config.citation_mode)
            generation_request = ScriptGenerationRequest(
                mission_id=request.mission_id,
                outline=outline,
                evidence_context=evidence_context,
                style=config.style,
                tone=config.tone,
                language=config.language,
                target_duration_seconds=config.target_duration_seconds,
                max_length=config.max_length,
                citation_mode=config.citation_mode,
            )
            self._logger.info("Requesting script generation provider.", category=LogCategory.API, agent_id=AgentID.SCRIPT_FORGE, mission_id=request.mission_id)
            provider_response = self._providers.generate(generation_request)
            sections = self._merge_provider_sections(provider_response.sections, request, config)
            metadata = ScriptMetadata(
                style=config.style,
                language=config.language,
                tone=config.tone,
                target_duration_seconds=config.target_duration_seconds,
                estimated_duration_seconds=0.0,
                word_count=0,
                citation_mode=config.citation_mode,
                evidence_sources=evidence_sources,
                source_candidate_ids=[candidate.candidate_id for candidate in request.research.candidates],
                fact_claim_count=len(request.fact_check.claims),
                manual_review_required=request.fact_check.manual_review_required,
            )
            document = ScriptDocument(
                script_id=uuid4(),
                mission_id=request.mission_id,
                title=provider_response.title,
                sections=sections,
                tone=config.tone,
                version=1,
                outline=outline,
                metadata=metadata,
                evidence_sources=evidence_sources,
            )
            document = self._validator.validate(document, request, config)
            self._logger.info(
                "Script Forge generation completed.",
                category=LogCategory.AGENT,
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                metadata={"duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000), "quality_score": document.quality_score},
            )
            self._emit(EventName.AGENT_COMPLETED, request.mission_id, {"quality_score": str(document.quality_score)})
            return document
        except AlphaBaseException:
            self._emit(EventName.AGENT_FAILED, request.mission_id, {"operation": "script_generation"})
            raise
        except Exception as exc:  # noqa: BLE001 - agent boundary
            self._logger.exception("Unexpected Script Forge failure.", category=LogCategory.ERROR, agent_id=AgentID.SCRIPT_FORGE, mission_id=request.mission_id)
            self._emit(EventName.AGENT_FAILED, request.mission_id, {"operation": "script_generation"})
            raise AgentExecutionError(
                "Script Forge generation failed unexpectedly.",
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                retryable=True,
                context={"operation": "run"},
                cause=exc,
            ) from exc

    @staticmethod
    def _validate_request(request: ScriptRequest) -> None:
        if request.mission_id != request.research.mission_id or request.mission_id != request.fact_check.mission_id:
            raise InputValidationError(
                "Mission IDs in script, research, and fact-check inputs must match.",
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                context={"operation": "validate_request"},
            )
        if not request.research.candidates:
            raise InputValidationError("Research input contains no candidates.", agent_id=AgentID.SCRIPT_FORGE, mission_id=request.mission_id)

    def _apply_overrides(self, request: ScriptRequest) -> ScriptGenerationConfig:
        base = self._config
        values = {
            "style": request.style or base.style,
            "target_duration_seconds": request.target_duration_seconds or base.target_duration_seconds,
            "tone": request.tone or base.tone,
            "section_order": tuple(request.section_order or base.section_order),
            "max_length": request.max_length or base.max_length,
            "citation_mode": request.citation_mode or base.citation_mode,
            "language": request.language or base.language,
        }
        values.update(request.runtime_overrides)
        try:
            return ScriptGenerationConfig(
                style=values["style"] if hasattr(values["style"], "value") else type(base.style)(values["style"]),
                target_duration_seconds=int(values["target_duration_seconds"]),
                tone=str(values["tone"]),
                section_order=tuple(item if hasattr(item, "value") else type(base.section_order[0])(item) for item in values["section_order"]),
                max_length=int(values["max_length"]),
                citation_mode=values["citation_mode"] if hasattr(values["citation_mode"], "value") else type(base.citation_mode)(values["citation_mode"]),
                language=str(values["language"]),
            )
        except (TypeError, ValueError) as exc:
            raise InputValidationError(
                "Invalid Script Forge runtime configuration override.",
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                context={"operation": "apply_overrides"},
                cause=exc,
            ) from exc

    @staticmethod
    def _merge_provider_sections(provider_sections: list[ScriptSection], request: ScriptRequest, config: ScriptGenerationConfig) -> list[ScriptSection]:
        by_type = {section.section_type: section for section in provider_sections}
        planned = ScriptSectionPlanner().expand_instructions(
            ScriptOutline(
                title=request.research.candidates[0].title,
                thesis=request.research.candidates[0].summary or request.research.candidates[0].title,
                sections=list(config.section_order),
                style=config.style,
            ),
            request,
        )
        merged: list[ScriptSection] = []
        for planned_section in planned:
            section = by_type.get(planned_section.section_type, planned_section)
            merged.append(section.model_copy(update={"order": len(merged)}))
        return merged

    def _emit(self, event: EventName, mission_id: UUID, payload: dict[str, str]) -> None:
        try:
            self._event_bus.emit(event, mission_id=mission_id, agent_id=AgentID.SCRIPT_FORGE, payload=payload)
        except Exception:  # noqa: BLE001 - event observability must not break content generation
            self._logger.warning("Failed to emit Script Forge lifecycle event.", category=LogCategory.ERROR, agent_id=AgentID.SCRIPT_FORGE, mission_id=mission_id)
