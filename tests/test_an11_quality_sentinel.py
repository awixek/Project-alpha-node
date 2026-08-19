from types import SimpleNamespace
from uuid import uuid4
from agents.an11.models import QualityConfig, QualityDecision, ValidationStage, IssueSeverity
from agents.an11.quality_sentinel import QualitySentinel
from agents.an11.validators import validate_research, validate_facts, validate_seo, validate_video
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import ExecutionStatus

def test_config_override():
    c=QualityConfig(quality_threshold=90); assert c.quality_threshold==90

def test_decisions_are_exact():
    assert [x.value for x in QualityDecision]==['PASS','PASS_WITH_WARNINGS','NEEDS_CHANGES','FAIL']

def test_handler_missing_dependencies_is_structured():
    mid=uuid4(); ctx=AgentExecutionContext(mission_id=mid,agent_id=AgentID.QUALITY_SENTINEL,stage=WorkflowStage.QUALITY_REVIEW,dependency_results={}); result=QualitySentinel().as_agent_handler()(ctx); assert result.status==ExecutionStatus.FAILED and result.error is not None

def test_research_empty_is_critical():
    d,issues=validate_research(SimpleNamespace(candidates=[])); assert d.score<100 and any(i.severity==IssueSeverity.CRITICAL for i in issues)

def test_fact_risk_is_reported():
    claim=SimpleNamespace(verification_status='unsupported',manual_review_required=True); d,issues=validate_facts(SimpleNamespace(claims=[claim])); assert d.score<100 and issues

def test_seo_threshold_is_explainable():
    d,issues=validate_seo(SimpleNamespace(seo_score=55,optimized_title='Title'),70); assert d.score==55 and issues[0].code=='seo_below_threshold'

def test_video_missing_output_is_critical():
    d,issues=validate_video(SimpleNamespace(video_uri='',timeline=SimpleNamespace(scenes=[])),70); assert any(i.severity==IssueSeverity.CRITICAL for i in issues)

def test_quality_config_is_independent_instance():
    a=QualityConfig(); b=QualityConfig(); a.scoring_weights['seo']=.9; assert b.scoring_weights['seo']!=.9

def test_validation_stage_contains_full_pipeline():
    assert set(x.value for x in ValidationStage)=={'research','fact','script','seo','visual','voice','subtitle','video','thumbnail','consistency','accessibility'}

def test_recommendation_fields_are_present():
    d,issues=validate_research(SimpleNamespace(candidates=[])); issue=issues[0]; assert issue.recommended_fix and issue.code and issue.confidence>=0
