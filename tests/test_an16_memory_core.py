from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from pydantic import BaseModel
from agents.an16.memory_core import MemoryCore
from agents.an16.models import *
from agents.an16.storage import InMemoryStore
from agents.an16.retrieval import MemoryRetriever
from shared.constants import AgentID
from shared.exceptions import AlphaBaseException


def record(mission, domain=MemoryDomain.RESEARCH, ctype=ContentType.RESEARCH, **kw):
    return MemoryRecord(mission_id=mission,domain=domain,content_type=ctype,source_agent=AgentID.RESEARCH_CORE,topic=kw.pop('topic','Ancient astronomy'),keywords=kw.pop('keywords',['astronomy']),payload=kw.pop('payload',{'x':1}),**kw)


def test_storage_and_indexing_and_report():
    m=uuid4(); core=MemoryCore(store=InMemoryStore()); r=core.execute(MemoryRequest(mission_id=m,records=[record(m)]))
    assert r.stored_records_summary['stored']==1
    assert core.retrieve(MemoryQuery(mission_id=m))[0].mission_id==m
    assert core.health().healthy


def test_retrieval_methods_and_filters():
    m=uuid4(); core=MemoryCore(); core.execute(MemoryRequest(mission_id=m,records=[record(m,topic='Vedic astronomy',keywords=['planet'])]))
    assert len(core.retriever.by_topic('vedic astronomy'))==1
    assert len(core.retriever.by_keyword('planet'))==1
    assert len(core.retriever.by_agent(AgentID.RESEARCH_CORE))==1
    assert len(core.retriever.by_content_type(ContentType.RESEARCH))==1


def test_duplicate_detection_and_idempotent_write():
    m=uuid4(); r=record(m); core=MemoryCore(); first=core.execute(MemoryRequest(mission_id=m,records=[r])); second=core.execute(MemoryRequest(mission_id=m,records=[r]))
    assert first.duplicate_summary['exact_duplicates']==0
    assert second.duplicate_summary['exact_duplicates']==1
    assert len(core.retrieve(MemoryQuery(mission_id=m)))==1


def test_relationship_management():
    m=uuid4(); a=record(m); b=record(m,domain=MemoryDomain.SCRIPT,ctype=ContentType.SCRIPT); rel=MemoryRelationship(mission_id=m,from_record_id=a.record_id,to_record_id=b.record_id,relation='research_to_script')
    core=MemoryCore(); core.execute(MemoryRequest(mission_id=m,records=[a,b],relationships=[rel])); assert len(core.store.relationships())>=1


def test_lifecycle_archive_policy():
    m=uuid4(); old=record(m,updated_at=datetime.now(timezone.utc)-timedelta(days=10)); cfg=MemoryConfig(lifecycle=LifecyclePolicy(archive_after_days=5)); core=MemoryCore(config=cfg); core.execute(MemoryRequest(mission_id=m,records=[old])); assert core.retrieve(MemoryQuery(mission_id=m,status=MemoryStatus.ARCHIVED))


def test_configuration_override():
    m=uuid4(); core=MemoryCore(); rep=core.execute(MemoryRequest(mission_id=m,records=[record(m)],runtime_overrides={'retrieval_limit':1,'duplicate_threshold':0.5})); assert rep.retrieval_statistics['retrieval_limit']==1


def test_future_semantic_interface_is_optional():
    class FutureSemantic:
        def search(self,query,limit=50): return []
    core=MemoryCore(semantic=FutureSemantic()); assert core.health().healthy


def test_invalid_empty_request_uses_shared_exception():
    with pytest.raises(AlphaBaseException): MemoryCore().execute(MemoryRequest(mission_id=uuid4()))


def test_an17_handler():
    from agents.an17.dispatcher import AgentExecutionContext
    from shared.schemas import AgentResult, ExecutionStatus
    m=uuid4(); r=record(m)
    from agents.an17.dispatcher import AgentExecutionContext
    from shared.constants import WorkflowStage
    upstream=AgentResult(agent_id=AgentID.RESEARCH_CORE, mission_id=m, status=ExecutionStatus.SUCCESS, payload=r, started_at=datetime.now(timezone.utc))
    ctx=AgentExecutionContext(mission_id=m, agent_id=AgentID.MEMORY_CORE, stage=WorkflowStage.MISSION_COMPLETE, dependency_results={'AN-01':upstream})
    result=MemoryCore().as_agent_handler()(ctx)
    assert isinstance(result,AgentResult); assert result.status==ExecutionStatus.SUCCESS; assert result.payload.mission_id==m


def test_artifact_normalization():
    class FakeArtifact(BaseModel):
        mission_id: object = None
        title: str='Topic'
    m=uuid4(); artifact=FakeArtifact(mission_id=m); core=MemoryCore(); report=core.execute(MemoryRequest(mission_id=m,artifacts=[artifact])); assert report.stored_records_summary['stored']==1
