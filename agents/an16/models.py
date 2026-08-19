"""Typed contracts for AN-16 Memory Core."""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field
from shared.constants import AgentID
from shared.schemas import BaseAlphaModel

class MemoryDomain(str, Enum):
    MISSION="mission"; RESEARCH="research"; FACT="fact"; SCRIPT="script"; SEO="seo"; VISUAL="visual"; MEDIA="media"; PUBLISHING="publishing"; ANALYTICS="analytics"; EVOLUTION="evolution"; REPUBLISHER="republisher"

class ContentType(str, Enum):
    MISSION="mission"; RESEARCH="research"; VERIFIED_FACT="verified_fact"; SCRIPT="script"; SEO="seo"; VISION_PLAN="vision_plan"; ASSET_PACKAGE="asset_package"; VOICE="voice"; SUBTITLE="subtitle"; VIDEO="video"; THUMBNAIL="thumbnail"; PUBLISH="publish"; ANALYTICS="analytics"; EVOLUTION="evolution"; REPUBLISHER="republisher"; GENERIC="generic"

class MemoryStatus(str, Enum): ACTIVE="active"; ARCHIVED="archived"; DELETED="deleted"

class MemoryRecord(BaseAlphaModel):
    record_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    domain: MemoryDomain
    content_type: ContentType
    source_agent: AgentID | None = None
    topic: str | None = None
    keywords: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str | None = None
    language: str | None = None
    platform: str | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    checksum: str = ""

class MemoryRelationship(BaseAlphaModel):
    relationship_id: UUID = Field(default_factory=uuid4)
    mission_id: UUID
    from_record_id: UUID
    to_record_id: UUID
    relation: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class MemoryQuery(BaseModel):
    model_config=ConfigDict(extra="forbid", str_strip_whitespace=True)
    mission_id: UUID | None = None
    topic: str | None = None
    keyword: str | None = None
    agent_id: AgentID | None = None
    platform: str | None = None
    content_type: ContentType | None = None
    domain: MemoryDomain | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    limit: int = Field(default=50, ge=1, le=1000)

class LifecyclePolicy(BaseModel):
    model_config=ConfigDict(extra="forbid")
    retention_days: int | None = Field(default=None, ge=1)
    archive_after_days: int | None = Field(default=None, ge=1)
    allow_delete: bool = False
    prune_duplicates: bool = True
    verify_integrity: bool = True
    keep_versions: int = Field(default=10, ge=1, le=1000)

class MemoryConfig(BaseModel):
    model_config=ConfigDict(extra="forbid", str_strip_whitespace=True)
    storage_backend: str = "memory"
    retention_policy: dict[str, Any] = Field(default_factory=dict)
    archive_policy: dict[str, Any] = Field(default_factory=dict)
    indexing_strategy: str = "metadata"
    duplicate_threshold: float = Field(default=1.0, ge=0, le=1)
    retrieval_limit: int = Field(default=50, ge=1, le=1000)
    cache_size: int = Field(default=256, ge=0, le=100000)
    lifecycle: LifecyclePolicy = Field(default_factory=LifecyclePolicy)
    @classmethod
    def from_shared_config(cls) -> "MemoryConfig":
        from shared.config import get_config
        agent = get_config().agents.get(AgentID.MEMORY_CORE.value)
        return cls(**(dict(agent.settings) if agent else {}))

class MemoryRequest(BaseModel):
    model_config=ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    mission_id: UUID
    records: list[MemoryRecord] = Field(default_factory=list)
    artifacts: list[BaseModel] = Field(default_factory=list)
    relationships: list[MemoryRelationship] = Field(default_factory=list)
    runtime_overrides: dict[str, Any] = Field(default_factory=dict)

class MemoryReport(BaseAlphaModel):
    mission_id: UUID
    agent_id: AgentID = AgentID.MEMORY_CORE
    stored_records_summary: dict[str, int] = Field(default_factory=dict)
    index_summary: dict[str, int] = Field(default_factory=dict)
    retrieval_statistics: dict[str, int | float] = Field(default_factory=dict)
    relationship_summary: dict[str, int] = Field(default_factory=dict)
    duplicate_summary: dict[str, int] = Field(default_factory=dict)
    lifecycle_actions: list[str] = Field(default_factory=list)
    execution_metrics: dict[str, float | int | str] = Field(default_factory=dict)
    timestamps: dict[str, datetime] = Field(default_factory=dict)

class MemoryHealth(BaseModel):
    model_config=ConfigDict(extra="forbid")
    backend: str
    records: int
    relationships: int
    indexed_fields: int
    healthy: bool

__all__=["MemoryDomain","ContentType","MemoryStatus","MemoryRecord","MemoryRelationship","MemoryQuery","LifecyclePolicy","MemoryConfig","MemoryRequest","MemoryReport","MemoryHealth"]
