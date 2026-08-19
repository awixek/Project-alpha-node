from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field
from agents.an01.models import ResearchBatch
from agents.an02.models import FactVerificationReport
from agents.an03.models import ScriptDocument
from agents.an04.models import SEOResult
from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an07.models import VoicePackage
from agents.an08.models import SubtitlePackage
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from shared.constants import AgentID
from shared.schemas import BaseAlphaModel

class QualityDecision(str, Enum):
    PASS='PASS'; PASS_WITH_WARNINGS='PASS_WITH_WARNINGS'; NEEDS_CHANGES='NEEDS_CHANGES'; FAIL='FAIL'
class IssueSeverity(str, Enum):
    INFO='info'; WARNING='warning'; ERROR='error'; CRITICAL='critical'
class ValidationStage(str, Enum):
    RESEARCH='research'; FACT='fact'; SCRIPT='script'; SEO='seo'; VISUAL='visual'; VOICE='voice'; SUBTITLE='subtitle'; VIDEO='video'; THUMBNAIL='thumbnail'; CONSISTENCY='consistency'; ACCESSIBILITY='accessibility'

class QualityIssue(BaseModel):
    model_config=ConfigDict(extra='forbid')
    issue_id: UUID=Field(default_factory=uuid4); stage: ValidationStage; severity: IssueSeverity; code:str=Field(min_length=1); message:str=Field(min_length=1); confidence:float=Field(ge=0,le=1); affected_agent:AgentID|None=None; recommended_fix:str=Field(min_length=1); estimated_impact:float=Field(default=0,ge=0,le=1); repair_priority:int=Field(default=5,ge=1,le=10); evidence:list[str]=Field(default_factory=list)
class ScoreDetail(BaseModel):
    model_config=ConfigDict(extra='forbid')
    score:float=Field(ge=0,le=100); explanation:str=Field(min_length=1); factors:dict[str,float]=Field(default_factory=dict)
class ValidationReport(BaseModel):
    model_config=ConfigDict(extra='forbid')
    stage_results:dict[str,ScoreDetail]=Field(default_factory=dict); passed:bool; issues:list[QualityIssue]=Field(default_factory=list)
class ConsistencyFinding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    source_agents:list[AgentID]=Field(default_factory=list); code:str; message:str; severity:IssueSeverity; confidence:float=Field(ge=0,le=1); evidence:list[str]=Field(default_factory=list); recommended_fix:str
class ConsistencyReport(BaseModel):
    model_config=ConfigDict(extra='forbid')
    score:float=Field(ge=0,le=100); aligned_pairs:int=Field(default=0,ge=0); checked_pairs:int=Field(default=0,ge=0); findings:list[ConsistencyFinding]=Field(default_factory=list); explanation:str
class AuditMetadata(BaseModel):
    model_config=ConfigDict(extra='forbid')
    audit_id:UUID=Field(default_factory=uuid4); started_at:datetime; completed_at:datetime|None=None; stages_completed:list[ValidationStage]=Field(default_factory=list); agents_seen:list[AgentID]=Field(default_factory=list); configuration_snapshot:dict[str,Any]=Field(default_factory=dict)
class ExecutionMetrics(BaseModel):
    model_config=ConfigDict(extra='forbid')
    execution_time_ms:float=Field(ge=0); issues_count:int=Field(default=0,ge=0); critical_issues:int=Field(default=0,ge=0); warnings:int=Field(default=0,ge=0); stages_completed:int=Field(default=0,ge=0)
class QualityReport(BaseAlphaModel):
    mission_id:UUID; agent_id:AgentID=AgentID.QUALITY_SENTINEL; final_decision:QualityDecision; overall_score:float=Field(ge=0,le=100); score_breakdown:dict[str,ScoreDetail]=Field(default_factory=dict); issue_list:list[QualityIssue]=Field(default_factory=list); recommendations:list[QualityIssue]=Field(default_factory=list); validation_report:ValidationReport; consistency_report:ConsistencyReport; accessibility_report:ScoreDetail; audit_metadata:AuditMetadata; execution_metrics:ExecutionMetrics; reasoning:str; generated_at:datetime=Field(default_factory=lambda:datetime.now(timezone.utc))
class QualityConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    quality_threshold:float=Field(default=70,ge=0,le=100); seo_threshold:float=Field(default=70,ge=0,le=100); consistency_threshold:float=Field(default=75,ge=0,le=100); subtitle_threshold:float=Field(default=75,ge=0,le=100); video_threshold:float=Field(default=75,ge=0,le=100); thumbnail_threshold:float=Field(default=70,ge=0,le=100); accessibility_threshold:float=Field(default=70,ge=0,le=100)
    scoring_weights:dict[str,float]=Field(default_factory=lambda:{'production':.20,'educational':.10,'technical':.15,'seo':.10,'accessibility':.10,'visual':.10,'audio':.10,'consistency':.10,'confidence':.05})
    @classmethod
    def from_shared_config(cls):
        from shared.config import get_config
        s=get_config().agents.get(AgentID.QUALITY_SENTINEL.value); values=dict(s.settings) if s else {}; weights=dict(cls().scoring_weights); weights.update(values.get('scoring_weights',{})); values['scoring_weights']=weights; return cls(**values)
class QualityRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',arbitrary_types_allowed=True)
    mission_id:UUID; research:ResearchBatch; facts:FactVerificationReport; script:ScriptDocument; seo:SEOResult; vision:VisionPlan; assets:AssetPackage; voice:VoicePackage; subtitles:SubtitlePackage; video:VideoPackage; thumbnail:ThumbnailPackage; runtime_overrides:dict[str,Any]=Field(default_factory=dict)
