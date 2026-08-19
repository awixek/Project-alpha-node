from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field
from shared.constants import AgentID, LogCategory, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ErrorReport, ExecutionStatus
from agents.an17.dispatcher import AgentExecutionContext
from agents.an07.models import VoicePackage
from .models import SubtitlePackage, SubtitleRequest, SubtitleTrack, SubtitleMetadata, SubtitleStyle
from .subtitle_builder import SubtitleBuilder
from .synchronizer import SubtitleSynchronizer
from .formatter import SubtitleFormatter
from .quality import SubtitleQualityValidator

class SubtitleEngineConfig(BaseModel):
    preferred_format: str = "srt"
    max_characters_per_line: int = 42
    max_lines: int = 2
    reading_speed: float = 17.0
    language: str = "en"
    bilingual_mode: bool = False
    timing_offset: float = 0.0
    timing_tolerance: float = 0.15
    timeout: float = 30.0
    style_profile: SubtitleStyle = Field(default_factory=SubtitleStyle)

class SubtitleEngine:
    def __init__(self, *, config: SubtitleEngineConfig | None=None, logger: AlphaLogger|None=None, event_bus: EventBus|None=None):
        self.config=config or SubtitleEngineConfig()
        self._logger=logger or get_agent_logger(AgentID.SUBTITLE_ENGINE)
        self._event_bus=event_bus or get_event_bus()
        self._formatter=SubtitleFormatter()
        self._validator=SubtitleQualityValidator()

    def execute(self, request: SubtitleRequest) -> SubtitlePackage:
        started=datetime.now(timezone.utc)
        self._validate_request(request)
        cfg=self._effective(request)
        self._logger.info("Subtitle generation started.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.SUBTITLE_ENGINE)
        voice=request.voice_package
        builder=SubtitleBuilder(cfg.max_characters_per_line,cfg.max_lines,cfg.reading_speed)
        sync=SubtitleSynchronizer(builder,cfg.timing_offset)
        segments=sync.synchronize(voice.narration_segments,cfg.language,request.speaker_labels)
        tracks=[SubtitleTrack(language=cfg.language,label=cfg.language,segments=segments,format=cfg.preferred_format)]
        formats=set(request.formats or [request.subtitle_format,cfg.preferred_format])
        if cfg.bilingual_mode or request.bilingual_mode:
            for lang in request.translated_text:
                if lang==cfg.language: continue
                translated=self._translated_segments(segments,request.translated_text[lang],lang)
                tracks.append(SubtitleTrack(language=lang,label=lang,segments=translated,format=cfg.preferred_format))
        report=self._validator.validate(segments,max_chars=cfg.max_characters_per_line,max_lines=cfg.max_lines,reading_speed=cfg.reading_speed,tolerance=cfg.timing_tolerance)
        exported={fmt:self._formatter.export(segments,fmt) for fmt in sorted(formats)}
        total=max((s.end_time for s in segments),default=0.0)
        metadata=SubtitleMetadata(mission_id=request.mission_id,language=cfg.language,track_count=len(tracks),segment_count=len(segments),total_duration=total,formats=sorted(formats))
        package=SubtitlePackage(
            mission_id=request.mission_id,
            subtitle_tracks=tracks,
            synchronization_metadata=report.metrics,
            exported_formats=exported,
            quality_report=report,
            validation_report=report.findings,
            formatting_metadata=cfg.style_profile,
            generation_statistics={"segments": len(segments), "formats": len(formats), "duration": total},
            metadata=metadata,
        )
        self._logger.info("Subtitle generation completed.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.SUBTITLE_ENGINE, metadata={"segments":len(segments)})
        return package

    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started=datetime.now(timezone.utc)
            try:
                payload=self._dependency(context,"AN-07",AgentID.VOICE_CORE)
                if not isinstance(payload,VoicePackage):
                    raise ValidationError("AN-08 requires an AN-07 VoicePackage.",agent_id=AgentID.SUBTITLE_ENGINE,mission_id=context.mission_id)
                req=SubtitleRequest(mission_id=context.mission_id,voice_package=payload)
                package=self.execute(req)
                return AgentResult(agent_id=AgentID.SUBTITLE_ENGINE,mission_id=context.mission_id,status=ExecutionStatus.SUCCESS,payload=package,started_at=started,completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:
                return self._failure(context,started,exc)
            except Exception as exc:
                wrapped=AgentExecutionError("Subtitle generation failed unexpectedly.",agent_id=AgentID.SUBTITLE_ENGINE,mission_id=context.mission_id,retryable=False,cause=exc)
                return self._failure(context,started,wrapped)
        return handler

    @staticmethod
    def _dependency(context,key,agent_id):
        for k,v in context.dependency_results.items():
            if k==key or k.lower() in {agent_id.value.lower(),"an07","voice_core"}:
                if v.payload is not None: return v.payload
        raise ValidationError("Required voice dependency is missing.",agent_id=AgentID.SUBTITLE_ENGINE,mission_id=context.mission_id,context={"dependency":agent_id.value})

    def _failure(self,context,started,exc):
        return AgentResult(agent_id=AgentID.SUBTITLE_ENGINE,mission_id=context.mission_id,status=ExecutionStatus.FAILED,error=exc.to_error_report(),started_at=started,completed_at=datetime.now(timezone.utc))

    def _validate_request(self,request):
        if request.mission_id != request.voice_package.mission_id:
            raise ValidationError("Mission ID does not match VoicePackage.",agent_id=AgentID.SUBTITLE_ENGINE,mission_id=request.mission_id)
        if not request.voice_package.narration_segments:
            raise ValidationError("VoicePackage contains no narration segments.",agent_id=AgentID.SUBTITLE_ENGINE,mission_id=request.mission_id)

    def _effective(self,request):
        data=self.config.model_dump()
        data.update({k:v for k,v in request.runtime_overrides.items() if k in data})
        data["style_profile"]=request.style_profile
        data["language"]=request.language
        data["bilingual_mode"]=request.bilingual_mode
        data["timing_offset"]=request.timing_offset
        return SubtitleEngineConfig(**data)

    def _translated_segments(self,base,translation,language):
        pieces=[p.strip() for p in translation.split("\n") if p.strip()]
        out=[]
        for i,s in enumerate(base):
            text=pieces[i] if i<len(pieces) else s.text
            out.append(s.model_copy(update={"subtitle_id":f"{s.subtitle_id}-{language}","language":language,"text":text}))
        return out
