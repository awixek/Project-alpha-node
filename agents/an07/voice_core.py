from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from pydantic import BaseModel

from agents.an03.models import ScriptDocument
from agents.an17.dispatcher import AgentExecutionContext
from shared.config import get_config
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, InputValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ErrorReport, ExecutionStatus, WorkflowEvent

from .models import (
    GenerationMetrics,
    PronunciationEntry,
    SynchronizationMetadata,
    VoiceCoreConfig,
    VoiceMetadata,
    VoicePackage,
    ProviderHealth,
    VoiceProfile,
    VoiceRequest,
    VoiceSegment,
)
from .pronunciation import PronunciationProcessor
from .provider import VoiceProviderRouter
from .quality import VoiceQualityValidator
from .synthesizer import VoiceSynthesizer


class VoiceCore:
    def __init__(
        self,
        *,
        provider_router: VoiceProviderRouter | None = None,
        config: VoiceCoreConfig | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._config = config or self._load_config()
        self._providers = provider_router or VoiceProviderRouter(config=self._config)
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.VOICE_CORE)
        self._quality = VoiceQualityValidator()
        self._pronunciation = PronunciationProcessor()

    @staticmethod
    def _load_config() -> VoiceCoreConfig:
        settings = get_config().agents.get(AgentID.VOICE_CORE.value)
        values = dict(settings.settings) if settings else {}
        return VoiceCoreConfig(**values)

    def execute(self, request: VoiceRequest) -> VoicePackage:
        self._validate(request)
        config = self._merge_config({**request.runtime_overrides, **({"preferred_provider": request.preferred_provider} if request.preferred_provider else {}), **({"fallback_provider": request.fallback_provider} if request.fallback_provider else {})})
        profile = request.profile.model_copy(update={
            "language": config.language,
            "voice": config.voice,
            "gender": config.gender,
            "style": config.style,
            "speaking_rate": config.speaking_rate,
            "pitch": config.pitch,
            "volume": config.volume,
            "emotion": config.emotion,
        })
        dictionary = self._pronunciation.merge_dictionaries(config.pronunciation_dictionary, request.pronunciation_dictionary)
        sections = self._extract_sections(request.script)
        metrics = GenerationMetrics(segments_requested=len(sections))
        provider_stats: dict[str, Any] = {}
        generated: list[VoiceSegment] = []
        failures: list[str] = []
        elapsed_cursor = 0.0
        self._publish(EventName.AGENT_STARTED, request.mission_id, {"agent": AgentID.VOICE_CORE.value})

        synthesizer = VoiceSynthesizer(self._providers, config=config, pronunciation=self._pronunciation, logger=self._logger)
        for sequence, section in enumerate(sections):
            segment_id = self._segment_id(section, sequence)
            try:
                processed, pronunciation_entries = self._pronunciation.process(section.narration, dictionary)
                outcome = synthesizer.synthesize(
                    mission_id=request.mission_id,
                    segment_id=segment_id,
                    text=section.narration,
                    profile=profile,
                    pronunciation_dictionary=dictionary,
                )
                response = outcome.response
                if not response.audio_uri.strip() or response.duration_seconds <= 0:
                    raise InputValidationError(
                        "Voice provider returned an invalid audio response.",
                        agent_id=AgentID.VOICE_CORE,
                        mission_id=request.mission_id,
                    )
                start = elapsed_cursor
                end = start + response.duration_seconds
                segment = VoiceSegment(
                    segment_id=segment_id,
                    section_id=str(section.order),
                    sequence=sequence,
                    text=section.narration,
                    processed_text=processed,
                    start_time=start,
                    estimated_end_time=end,
                    duration=response.duration_seconds,
                    narrator=profile.voice,
                    language=profile.language,
                    emotion=profile.emotion,
                    emphasis=[],
                    speech_rate=profile.speaking_rate,
                    provider=response.provider,
                    audio_uri=response.audio_uri,
                    mime_type=response.mime_type,
                    pronunciation=pronunciation_entries,
                    generation_metadata=response.metadata,
                )
                generated.append(segment)
                elapsed_cursor = end + config.pause_duration
                metrics.segments_generated += 1
                metrics.generation_time_ms += outcome.elapsed_ms
                health = provider_stats.setdefault(response.provider, {"requests": 0, "successes": 0, "failures": 0, "retries": 0})
                health["requests"] += 1
                health["successes"] += 1
            except AlphaBaseException as exc:
                failures.append(f"Segment {segment_id}: {exc}")
                metrics.segments_failed += 1
                self._logger.warning(
                    "AN-07 segment generation failed; continuing.",
                    category=LogCategory.AGENT,
                    mission_id=request.mission_id,
                    agent_id=AgentID.VOICE_CORE,
                    metadata={"segment_id": segment_id},
                )
            except Exception as exc:
                failures.append(f"Segment {segment_id}: generation failed.")
                metrics.segments_failed += 1
                self._logger.error(
                    "AN-07 unexpected segment failure.",
                    category=LogCategory.ERROR,
                    mission_id=request.mission_id,
                    agent_id=AgentID.VOICE_CORE,
                    metadata={"segment_id": segment_id, "error_type": type(exc).__name__},
                )

        quality = self._quality.validate(generated, config)
        if failures:
            quality = quality.model_copy(update={"passed": False, "findings": [*quality.findings, *failures]})
        pronunciations = self._collect_pronunciations(generated)
        total_duration = generated[-1].estimated_end_time if generated else 0.0
        metadata = VoiceMetadata(
            language=profile.language,
            voice=profile.voice,
            style=profile.style,
            total_duration=total_duration,
            segment_count=len(generated),
            word_count=sum(len(segment.text.split()) for segment in generated),
        )
        package = VoicePackage(
            mission_id=request.mission_id,
            narration_segments=generated,
            narration_uri=None,
            metadata=metadata,
            pronunciation_metadata=pronunciations,
            synchronization=SynchronizationMetadata(segments=generated, total_duration=total_duration),
            provider_statistics=[
                ProviderHealth(provider=name, **values)
                for name, values in provider_stats.items()
            ],
            quality_report=quality,
            generation_metrics=metrics,
            production_metadata={
                "agent": AgentID.VOICE_CORE.value,
                "status": "partial" if failures else "complete",
                "failed_segments": str(metrics.segments_failed),
            },
        )
        self._publish(
            EventName.AGENT_COMPLETED if not failures else EventName.AGENT_FAILED,
            request.mission_id,
            {"agent": AgentID.VOICE_CORE.value, "segments": str(len(generated))},
        )
        return package

    def as_agent_handler(self):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started = datetime.now(timezone.utc)
            try:
                script = self._extract_script(context)
                package = self.execute(VoiceRequest(mission_id=context.mission_id, script=script))
                status = ExecutionStatus.PARTIAL_SUCCESS if package.generation_metrics.segments_failed else ExecutionStatus.SUCCESS
                error = None
                if status is ExecutionStatus.PARTIAL_SUCCESS:
                    error = ErrorReport(
                        agent_id=AgentID.VOICE_CORE,
                        severity="warning",
                        code="partial_voice_generation",
                        message="One or more narration segments failed; partial VoicePackage returned.",
                        retryable=True,
                        context={"failed_segments": str(package.generation_metrics.segments_failed)},
                    )
                return AgentResult[VoicePackage](
                    agent_id=AgentID.VOICE_CORE,
                    mission_id=context.mission_id,
                    status=status,
                    payload=package,
                    error=error,
                    started_at=started,
                    completed_at=datetime.now(timezone.utc),
                )
            except AlphaBaseException as exc:
                return AgentResult[BaseModel](
                    agent_id=AgentID.VOICE_CORE,
                    mission_id=context.mission_id,
                    status=ExecutionStatus.FAILED,
                    error=exc.to_error_report(),
                    started_at=started,
                    completed_at=datetime.now(timezone.utc),
                )
            except Exception as exc:
                wrapped = AgentExecutionError(
                    "AN-07 execution failed unexpectedly.",
                    agent_id=AgentID.VOICE_CORE,
                    mission_id=context.mission_id,
                    retryable=False,
                    cause=exc,
                )
                return AgentResult[BaseModel](
                    agent_id=AgentID.VOICE_CORE,
                    mission_id=context.mission_id,
                    status=ExecutionStatus.FAILED,
                    error=wrapped.to_error_report(),
                    started_at=started,
                    completed_at=datetime.now(timezone.utc),
                )

        return handler

    @staticmethod
    def _extract_sections(script: ScriptDocument) -> list[Any]:
        sections = [section for section in script.sections if section.narration.strip()]
        if not sections:
            raise InputValidationError("AN-07 requires at least one non-empty script section.", agent_id=AgentID.VOICE_CORE, mission_id=script.mission_id)
        return sections

    @staticmethod
    def _segment_id(section: Any, sequence: int) -> str:
        return f"section-{sequence + 1}-{section.order}"

    @staticmethod
    def _collect_pronunciations(segments: list[VoiceSegment]) -> list[PronunciationEntry]:
        merged: dict[tuple[str, str], int] = {}
        for segment in segments:
            for entry in segment.pronunciation:
                key = (entry.original, entry.pronunciation)
                merged[key] = merged.get(key, 0) + entry.occurrences
        return [PronunciationEntry(original=k[0], pronunciation=k[1], occurrences=v) for k, v in merged.items()]

    @staticmethod
    def _validate(request: VoiceRequest) -> None:
        if request.mission_id != request.script.mission_id:
            raise InputValidationError("Script mission_id does not match execution mission_id.", agent_id=AgentID.VOICE_CORE, mission_id=request.mission_id)

    def _merge_config(self, overrides: Mapping[str, Any]) -> VoiceCoreConfig:
        values = self._config.model_dump()
        values.update(dict(overrides))
        return VoiceCoreConfig(**values)

    @staticmethod
    def _extract_script(context: AgentExecutionContext) -> ScriptDocument:
        for result in context.dependency_results.values():
            if isinstance(result.payload, ScriptDocument):
                return result.payload
        raise InputValidationError("AN-07 could not find a ScriptDocument in dependency results.", agent_id=AgentID.VOICE_CORE, mission_id=context.mission_id)

    def _publish(self, event_type: EventName, mission_id, payload: dict[str, str]) -> None:
        try:
            self._event_bus.publish(WorkflowEvent(mission_id=mission_id, agent_id=AgentID.VOICE_CORE, event_type=event_type.value, payload=payload))
        except Exception:
            self._logger.warning("AN-07 event publication failed; continuing.", category=LogCategory.AGENT, mission_id=mission_id, agent_id=AgentID.VOICE_CORE)
