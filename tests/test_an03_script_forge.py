from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an01.models import ResearchBatch, ResearchCandidate
from agents.an02.models import FactVerificationReport, VerifiedClaim, ClaimType, VerificationStatus
from agents.an03 import (
    CitationMode,
    ScriptForge,
    ScriptForgeCoordinator,
    ScriptGenerationProvider,
    ScriptGenerationProviderRegistry,
    ScriptRequest,
    ScriptSection,
    ScriptStyle,
    SectionType,
)
from agents.an03.models import ScriptGenerationResponse
from agents.an17.dispatcher import AgentExecutionContext
from shared.api_router import APIRouter
from shared.constants import AgentID, WorkflowStage
from shared.retry import RetryPolicy
from shared.schemas import AgentResult, ExecutionStatus, FactVerdict, SourceRef, SourceReliability

NO_WAIT = RetryPolicy(max_attempts=1, delay_seconds=0, backoff_multiplier=1, timeout_seconds=5)


def source(url: str, title: str) -> SourceRef:
    return SourceRef(url=url, title=title, publisher=title, reliability=SourceReliability.PRIMARY, published_at=datetime.now(timezone.utc))


def research(mission_id):
    candidate = ResearchCandidate(
        mission_id=mission_id,
        title="A verified history of the project",
        summary="The project was established to coordinate a modular AI pipeline.",
        sources=[source("https://example.org/a", "Primary Source"), source("https://example.org/a", "Duplicate")],
        confidence_score=0.9,
        freshness_score=0.9,
        authority_score=0.9,
        relevance_score=0.95,
        information_completeness=0.9,
        cross_source_confirmation=0.8,
        source_diversity=0.7,
        overall_priority_score=0.9,
        discovery_timestamp=datetime.now(timezone.utc),
        cluster_id="c1",
    )
    duplicate = candidate.model_copy()
    return ResearchBatch(mission_id=mission_id, query="project history", candidates=[candidate, duplicate])


def fact_check(mission_id):
    src = source("https://example.org/a", "Primary Source")
    claim = VerifiedClaim(
        claim="The project was established to coordinate a modular AI pipeline.",
        verdict=FactVerdict.VERIFIED_TRUE,
        confidence=0.95,
        supporting_sources=[src, src],
        notes="Confirmed by evidence.",
        claim_type=ClaimType.FACT,
        verification_status=VerificationStatus.VERIFIED,
        evidence_summary="Confirmed.",
        reliability_score=0.92,
        independent_confirmations=2,
    )
    return FactVerificationReport(
        mission_id=mission_id,
        claims=[claim],
        overall_reliability_score=0.92,
        verification_confidence=0.95,
        overall_pass=True,
        manual_review_required=False,
    )


class FakeScriptProvider(ScriptGenerationProvider):
    def __init__(self, name="fake", fail=False):
        self._name = name
        self.fail = fail
        self.calls = 0

    @property
    def name(self):
        return self._name

    def call(self, request):
        self.calls += 1
        if self.fail:
            raise ConnectionError("provider down")
        sections = [
            ScriptSection(
                order=index,
                heading=section.value.title(),
                narration=f"Accurate narration for {section.value} based on the supplied evidence.",
                section_type=section,
            )
            for index, section in enumerate(request.outline.sections)
        ]
        return ScriptGenerationResponse(title=request.outline.title, sections=sections, provider=self.name)


def coordinator(*providers):
    registry = ScriptGenerationProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    for index, provider in enumerate(providers):
        registry.register(provider, priority=index + 1)
    return ScriptForgeCoordinator(providers=registry)


def test_successful_generation_and_evidence_preservation():
    mission_id = uuid4()
    forge = ScriptForge(coordinator=coordinator(FakeScriptProvider()))
    result = forge.execute(ScriptRequest(mission_id=mission_id, research=research(mission_id), fact_check=fact_check(mission_id)))
    assert result.status is ExecutionStatus.SUCCESS
    assert result.payload is not None
    assert result.payload.sections
    assert result.payload.evidence_sources
    assert result.payload.metadata.fact_claim_count == 1


def test_invalid_mission_inputs_are_rejected():
    mission_id = uuid4()
    other = uuid4()
    request = ScriptRequest(mission_id=mission_id, research=research(other), fact_check=fact_check(mission_id))
    result = ScriptForge(coordinator=coordinator(FakeScriptProvider())).execute(request)
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None


def test_duplicate_candidates_and_sources_are_removed():
    mission_id = uuid4()
    doc = coordinator(FakeScriptProvider()).run(ScriptRequest(mission_id=mission_id, research=research(mission_id), fact_check=fact_check(mission_id)))
    assert len(doc.evidence_sources) == 1
    assert len(doc.metadata.source_candidate_ids) == 2


def test_runtime_config_overrides_style_duration_sections_and_citations():
    mission_id = uuid4()
    request = ScriptRequest(
        mission_id=mission_id,
        research=research(mission_id),
        fact_check=fact_check(mission_id),
        runtime_overrides={
            "style": "documentary",
            "target_duration_seconds": 600,
            "tone": "measured",
            "section_order": ["hook", "evidence_block", "conclusion"],
            "max_length": 5000,
            "citation_mode": "end_notes",
            "language": "hi",
        },
    )
    doc = coordinator(FakeScriptProvider()).run(request)
    assert doc.metadata.style is ScriptStyle.DOCUMENTARY
    assert doc.metadata.target_duration_seconds == 600
    assert doc.metadata.citation_mode is CitationMode.END_NOTES
    assert [section.section_type for section in doc.sections] == [SectionType.HOOK, SectionType.EVIDENCE_BLOCK, SectionType.CONCLUSION]


def test_provider_failure_falls_back_to_next_provider():
    mission_id = uuid4()
    first = FakeScriptProvider("first", fail=True)
    second = FakeScriptProvider("second")
    doc = coordinator(first, second).run(ScriptRequest(mission_id=mission_id, research=research(mission_id), fact_check=fact_check(mission_id)))
    assert doc.title
    assert first.calls == 1
    assert second.calls == 1


def test_an17_handler_requires_an01_and_an02_dependencies():
    mission_id = uuid4()
    forge = ScriptForge(coordinator=coordinator(FakeScriptProvider()))
    handler = forge.as_agent_handler()
    with pytest.raises(Exception):
        handler(AgentExecutionContext(mission_id=mission_id, agent_id=AgentID.SCRIPT_FORGE, stage=WorkflowStage.SCRIPT, dependency_results={}))


def test_an17_handler_returns_script_result():
    mission_id = uuid4()
    forge = ScriptForge(coordinator=coordinator(FakeScriptProvider()))
    research_result = AgentResult(agent_id=AgentID.RESEARCH_CORE, mission_id=mission_id, status=ExecutionStatus.SUCCESS, payload=research(mission_id), started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc))
    fact_result = AgentResult(agent_id=AgentID.FACT_GUARDIAN, mission_id=mission_id, status=ExecutionStatus.SUCCESS, payload=fact_check(mission_id), started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc))
    result = forge.as_agent_handler()(AgentExecutionContext(mission_id=mission_id, agent_id=AgentID.SCRIPT_FORGE, stage=WorkflowStage.SCRIPT, dependency_results={"research": research_result, "facts": fact_result}))
    assert result.agent_id is AgentID.SCRIPT_FORGE
    assert result.status is ExecutionStatus.SUCCESS
    assert result.payload is not None
