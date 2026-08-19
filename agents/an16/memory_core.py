"""AN-16 Memory Core public orchestration boundary."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from pydantic import BaseModel
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ExecutionStatus
from .models import *
from .storage import MemoryStore, InMemoryStore
from .indexing import MemoryIndexer
from .retrieval import MemoryRetriever, SemanticRetrievalProvider
from .knowledge import KnowledgeLinker
from .lifecycle import LifecycleManager

class MemoryCore:
    def __init__(self, *, config: MemoryConfig | None=None, store: MemoryStore | None=None, semantic: SemanticRetrievalProvider | None=None, logger: AlphaLogger | None=None, event_bus: EventBus | None=None):
        self.settings=config or MemoryConfig.from_shared_config(); self.store=store or InMemoryStore(); self.indexer=MemoryIndexer(); self.retriever=MemoryRetriever(self.store,self.indexer,semantic); self.linker=KnowledgeLinker(self.store); self.lifecycle=LifecycleManager(self.store); self.logger=logger or get_agent_logger(AgentID.MEMORY_CORE); self.event_bus=event_bus or get_event_bus()
        for r in self.store.all_records(): self.indexer.add(r)
    def execute(self, request: MemoryRequest) -> MemoryReport:
        started=datetime.now(timezone.utc); self._validate(request); cfg=self._effective_config(request.runtime_overrides)
        self.logger.info("Memory write started.",category=LogCategory.AGENT,mission_id=request.mission_id,agent_id=AgentID.MEMORY_CORE)
        self.event_bus.emit(EventName.AGENT_STARTED,mission_id=request.mission_id,agent_id=AgentID.MEMORY_CORE,payload={"stage":"memory"})
        stored=created=updated=duplicates=0; domains={}; lifecycle_actions=[]; relationship_count=0
        records=list(request.records)+[self._artifact_to_record(a,request.mission_id) for a in request.artifacts]
        prepared=[]
        for r in records:
            if r.mission_id != request.mission_id: raise ValidationError("Memory record mission mismatch.",agent_id=AgentID.MEMORY_CORE,mission_id=request.mission_id)
            nr=self.lifecycle.prepare(r); prepared.append(nr)
        for r in prepared:
            existing=self.store.get(r.record_id)
            if existing and existing.checksum == r.checksum: duplicates += 1; continue
            is_new=self.store.upsert(r); self.indexer.remove(r); self.indexer.add(r); stored += 1; created += int(is_new); updated += int(not is_new); domains[r.domain.value]=domains.get(r.domain.value,0)+1
        inferred=self.linker.infer(prepared)
        for rel in list(request.relationships)+inferred:
            try: self.linker.link(rel); relationship_count += 1
            except ValueError: continue
        lifecycle_actions=self.lifecycle.apply(cfg.lifecycle)
        completed=datetime.now(timezone.utc)
        report=MemoryReport(mission_id=request.mission_id,stored_records_summary={"processed":len(records),"stored":stored,"created":created,"updated":updated,**domains},index_summary=self.indexer.stats(),retrieval_statistics={"records_total":len(self.store.all_records()),"relationships_total":len(self.store.relationships()),"retrieval_limit":cfg.retrieval_limit},relationship_summary={"created_or_confirmed":relationship_count,"total":len(self.store.relationships())},duplicate_summary={"exact_duplicates":duplicates,"duplicate_pairs":len(self.lifecycle.duplicate_ids(cfg.duplicate_threshold))},lifecycle_actions=lifecycle_actions,execution_metrics={"execution_time_ms":(completed-started).total_seconds()*1000,"storage_backend":cfg.storage_backend},timestamps={"started_at":started,"completed_at":completed})
        self.logger.info("Memory write completed.",category=LogCategory.AGENT,mission_id=request.mission_id,agent_id=AgentID.MEMORY_CORE,metadata={"stored":stored})
        self.event_bus.emit(EventName.AGENT_COMPLETED,mission_id=request.mission_id,agent_id=AgentID.MEMORY_CORE,payload={"stored":str(stored)})
        return report
    def retrieve(self, query: MemoryQuery): return self.retriever.retrieve(query)
    def health(self)->MemoryHealth: return MemoryHealth(backend=self.settings.storage_backend,records=len(self.store.all_records()),relationships=len(self.store.relationships()),indexed_fields=len(self.indexer.stats()),healthy=True)
    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started=datetime.now(timezone.utc)
            try:
                request=self._request_from_context(context); report=self.execute(request)
                return AgentResult(agent_id=AgentID.MEMORY_CORE,mission_id=context.mission_id,status=ExecutionStatus.SUCCESS,payload=report,started_at=started,completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc: return self._failure(context,started,exc)
            except Exception as exc:
                wrapped=AgentExecutionError("AN-16 memory operation failed unexpectedly.",agent_id=AgentID.MEMORY_CORE,mission_id=context.mission_id,cause=exc)
                return self._failure(context,started,wrapped)
        return handler
    @staticmethod
    def _artifact_to_record(artifact: BaseModel, mission_id: UUID) -> MemoryRecord:
        agent=getattr(artifact,"agent_id",None); name=artifact.__class__.__name__.lower(); mapping={"researchbatch":(MemoryDomain.RESEARCH,ContentType.RESEARCH),"verifiedfactpackage":(MemoryDomain.FACT,ContentType.VERIFIED_FACT),"scriptdocument":(MemoryDomain.SCRIPT,ContentType.SCRIPT),"seoresult":(MemoryDomain.SEO,ContentType.SEO),"visionplan":(MemoryDomain.VISUAL,ContentType.VISION_PLAN),"assetpackage":(MemoryDomain.MEDIA,ContentType.ASSET_PACKAGE),"voicepackage":(MemoryDomain.MEDIA,ContentType.VOICE),"subtitlepackage":(MemoryDomain.MEDIA,ContentType.SUBTITLE),"videopackage":(MemoryDomain.MEDIA,ContentType.VIDEO),"thumbnailpackage":(MemoryDomain.MEDIA,ContentType.THUMBNAIL),"publishpackage":(MemoryDomain.PUBLISHING,ContentType.PUBLISH),"analyticsreport":(MemoryDomain.ANALYTICS,ContentType.ANALYTICS),"evolutionreport":(MemoryDomain.EVOLUTION,ContentType.EVOLUTION),"republisherpackage":(MemoryDomain.REPUBLISHER,ContentType.REPUBLISHER)}
        domain,ctype=mapping.get(name,(MemoryDomain.MISSION,ContentType.GENERIC)); payload=artifact.model_dump(mode="json"); topic=payload.get("title") or payload.get("topic")
        rid=payload.get("mission_id") or mission_id
        return MemoryRecord(mission_id=mission_id,domain=domain,content_type=ctype,source_agent=agent,topic=str(topic) if topic else None,payload=payload)
    @staticmethod
    def _validate(request: MemoryRequest):
        if not request.records and not request.artifacts: raise ValidationError("AN-16 requires at least one memory record or artifact.",agent_id=AgentID.MEMORY_CORE,mission_id=request.mission_id)
    def _effective_config(self, overrides): return MemoryConfig(**{**self.settings.model_dump(),**overrides})
    @staticmethod
    def _request_from_context(context):
        artifacts=[]
        for result in context.dependency_results.values():
            payload=getattr(result,"payload",None)
            if isinstance(payload,BaseModel): artifacts.append(payload)
        return MemoryRequest(mission_id=context.mission_id,artifacts=artifacts)
    @staticmethod
    def _failure(context,started,exc): return AgentResult(agent_id=AgentID.MEMORY_CORE,mission_id=context.mission_id,status=ExecutionStatus.FAILED,payload=None,error=exc.to_error_report(),started_at=started,completed_at=datetime.now(timezone.utc))

__all__=["MemoryCore"]
