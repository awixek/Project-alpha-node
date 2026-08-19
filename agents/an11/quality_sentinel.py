from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ExecutionStatus
from .analyzer import audit_all
from .models import QualityConfig, QualityDecision, QualityReport, QualityRequest, AuditMetadata, ExecutionMetrics, ValidationReport, ValidationStage
from .recommendations import prioritize
from .scoring import weighted_score, explain

class QualitySentinel:
    def __init__(self, *, settings:QualityConfig|None=None, logger:AlphaLogger|None=None, event_bus:EventBus|None=None):
        self.settings=settings or QualityConfig.from_shared_config(); self._logger=logger or get_agent_logger(AgentID.QUALITY_SENTINEL); self._event_bus=event_bus or get_event_bus()
    def execute(self, request:QualityRequest)->QualityReport:
        started=datetime.now(timezone.utc); self._validate_request(request); config=self._effective_config(request.runtime_overrides)
        self._logger.info('Quality validation started.',category=LogCategory.AGENT,mission_id=request.mission_id,agent_id=AgentID.QUALITY_SENTINEL)
        self._event_bus.emit(EventName.AGENT_STARTED,mission_id=request.mission_id,agent_id=AgentID.QUALITY_SENTINEL,payload={'stage':'quality_audit'})
        details,issues,consistency,accessibility=audit_all(request,config); overall=weighted_score(details,config)
        critical=sum(i.severity.value=='critical' for i in issues); errors=sum(i.severity.value=='error' for i in issues); warnings=sum(i.severity.value=='warning' for i in issues)
        if critical: decision=QualityDecision.FAIL
        elif errors or overall<config.quality_threshold or consistency.score<config.consistency_threshold: decision=QualityDecision.NEEDS_CHANGES
        elif warnings: decision=QualityDecision.PASS_WITH_WARNINGS
        else: decision=QualityDecision.PASS
        completed=datetime.now(timezone.utc); elapsed=(completed-started).total_seconds()*1000
        validation=ValidationReport(stage_results=details,passed=decision in {QualityDecision.PASS,QualityDecision.PASS_WITH_WARNINGS},issues=issues)
        audit=AuditMetadata(started_at=started,completed_at=completed,stages_completed=list(ValidationStage),agents_seen=[AgentID.RESEARCH_CORE,AgentID.FACT_GUARDIAN,AgentID.SCRIPT_FORGE,AgentID.SEO_BRAIN,AgentID.VISION_PLANNER,AgentID.VISION_CREATOR,AgentID.VOICE_CORE,AgentID.SUBTITLE_ENGINE,AgentID.VIDEO_FORGE,AgentID.THUMBNAIL_STUDIO,AgentID.QUALITY_SENTINEL],configuration_snapshot=config.model_dump())
        metrics=ExecutionMetrics(execution_time_ms=elapsed,issues_count=len(issues),critical_issues=critical,warnings=warnings,stages_completed=len(details))
        report=QualityReport(mission_id=request.mission_id,final_decision=decision,overall_score=overall,score_breakdown=details,issue_list=issues,recommendations=prioritize(issues),validation_report=validation,consistency_report=consistency,accessibility_report=accessibility,audit_metadata=audit,execution_metrics=metrics,reasoning=explain(details,overall))
        self._logger.info('Quality validation completed.',category=LogCategory.AGENT,mission_id=request.mission_id,agent_id=AgentID.QUALITY_SENTINEL,execution_time_ms=elapsed)
        self._event_bus.emit(EventName.AGENT_COMPLETED,mission_id=request.mission_id,agent_id=AgentID.QUALITY_SENTINEL,payload={'decision':decision.value,'score':str(round(overall,2))})
        return report
    def as_agent_handler(self,**_:Any):
        def handler(context:AgentExecutionContext)->AgentResult[BaseModel]:
            started=datetime.now(timezone.utc)
            try:
                ids=[AgentID.RESEARCH_CORE,AgentID.FACT_GUARDIAN,AgentID.SCRIPT_FORGE,AgentID.SEO_BRAIN,AgentID.VISION_PLANNER,AgentID.VISION_CREATOR,AgentID.VOICE_CORE,AgentID.SUBTITLE_ENGINE,AgentID.VIDEO_FORGE,AgentID.THUMBNAIL_STUDIO]
                vals=[self._dependency(context,a) for a in ids]
                req=QualityRequest(mission_id=context.mission_id,research=vals[0],facts=vals[1],script=vals[2],seo=vals[3],vision=vals[4],assets=vals[5],voice=vals[6],subtitles=vals[7],video=vals[8],thumbnail=vals[9])
                return AgentResult(agent_id=AgentID.QUALITY_SENTINEL,mission_id=context.mission_id,status=ExecutionStatus.SUCCESS,payload=self.execute(req),started_at=started,completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:return self._failure(context,started,exc)
            except Exception as exc:return self._failure(context,started,AgentExecutionError('Quality audit failed unexpectedly.',agent_id=AgentID.QUALITY_SENTINEL,mission_id=context.mission_id,cause=exc))
        return handler
    @staticmethod
    def _dependency(context,agent_id):
        aliases={agent_id.value.lower(),agent_id.value.replace('-','').lower()}
        for key,result in context.dependency_results.items():
            normalized=key.lower().replace('_','').replace('-','')
            if key==agent_id.value or normalized in {a.replace('-','') for a in aliases}:
                if result.payload is not None:return result.payload
        raise ValidationError('Required upstream dependency is missing.',agent_id=AgentID.QUALITY_SENTINEL,mission_id=context.mission_id,context={'dependency':agent_id.value})
    @staticmethod
    def _validate_request(request):
        for name,obj in request.__dict__.items():
            if name in {'mission_id','runtime_overrides'}:continue
            if getattr(obj,'mission_id',request.mission_id)!=request.mission_id:raise ValidationError(f'{name} mission_id does not match request.',agent_id=AgentID.QUALITY_SENTINEL,mission_id=request.mission_id)
    def _effective_config(self,overrides):
        values=self.settings.model_dump(); values.update(overrides); return QualityConfig(**values)
    @staticmethod
    def _failure(context,started,exc):
        return AgentResult(agent_id=AgentID.QUALITY_SENTINEL,mission_id=context.mission_id,status=ExecutionStatus.FAILED,payload=None,error=exc.to_error_report(),started_at=started,completed_at=datetime.now(timezone.utc))

__all__=['QualitySentinel']
