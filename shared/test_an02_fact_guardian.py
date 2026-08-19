from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from shared.api_router import APIRouter
from shared.constants import AgentID, WorkflowStage
from shared.retry import RetryPolicy
from shared.schemas import FactVerdict, SourceRef, SourceReliability

from an01.models import ResearchBatch, ResearchCandidate
from an02 import (
    EvidenceItem,
    FactGuardian,
    FactVerificationCoordinator,
    FactVerificationProvider,
    FactVerificationProviderRegistry,
    VerificationStatus,
)
from an02.models import FactAnalysisConfig
from an17.dispatcher import AgentExecutionContext


NO_WAIT = RetryPolicy(max_attempts=1, delay_seconds=0, backoff_multiplier=1, timeout_seconds=5)


class FakeVerificationProvider(FactVerificationProvider):
    def __init__(self, name: str, evidence: tuple[EvidenceItem, ...], fail: bool = False):
        self._name = name
        self._evidence = evidence
        self.fail = fail
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def call(self, request: str):
        self.calls += 1
        if self.fail:
            raise ConnectionError("provider unavailable")
        return self._evidence


def _source(url: str, publisher: str, *, primary: bool = True, published_at=None):
    return SourceRef(
        url=url,
        title="Evidence source",
        publisher=publisher,
        published_at=published_at or datetime.now(timezone.utc),
        reliability=SourceReliability.PRIMARY if primary else SourceReliability.SECONDARY,
    )


def _candidate(mission_id):
    return ResearchCandidate(
        mission_id=mission_id,
        title="The project was founded in 2020.",
        summary="The project was founded in 2020.",
        sources=[_source("https://example.org/research", "Research Org")],
        confidence_score=0.8,
        freshness_score=0.9,
        authority_score=0.9,
        relevance_score=0.9,
        information_completeness=1.0,
        cross_source_confirmation=0.66,
        source_diversity=0.5,
        overall_priority_score=0.85,
        discovery_timestamp=datetime.now(timezone.utc),
        cluster_id="cluster-001",
        supporting_providers=["research"],
    )


def _research(mission_id):
    return ResearchBatch(
        mission_id=mission_id,
        query="project history",
        candidates=[_candidate(mission_id)],
    )


def test_fact_guardian_cross_source_verifies_claim():
    mission_id = uuid4()
    evidence = (
        EvidenceItem(
            claim="The project was founded in 2020.",
            source=_source("https://gov.example/record", "Government Official"),
            evidence_statement="The project was founded in 2020.",
            excerpt="Founded in 2020.",
            supports_claim=True,
            provider="government",
            evidence_quality=1.0,
        ),
    )
    second = EvidenceItem(
        claim="The project was founded in 2020.",
        source=_source("https://academic.example/paper", "Academic Source", primary=False),
        evidence_statement="The project was founded in 2020.",
        excerpt="Founded in 2020.",
        supports_claim=True,
        provider="academic",
        evidence_quality=0.9,
    )
    registry = FactVerificationProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    registry.register(FakeVerificationProvider("government", evidence), priority=1)
    registry.register(FakeVerificationProvider("academic", (second,)), priority=2)

    report = FactVerificationCoordinator(providers=registry).run(
        __import__("an02.models", fromlist=["FactCheckRequest"]).FactCheckRequest(
            mission_id=mission_id,
            research=_research(mission_id),
        )
    )

    assert report.claims
    claim = report.claims[0]
    assert claim.verdict is FactVerdict.VERIFIED_TRUE
    assert claim.verification_status is VerificationStatus.VERIFIED
    assert claim.independent_confirmations == 2
    assert claim.reliability_score > 0.7


def test_fact_guardian_preserves_conflicting_evidence():
    mission_id = uuid4()
    first = EvidenceItem(
        claim="The project was founded in 2020.",
        source=_source("https://a.example/record", "Source A"),
        evidence_statement="The project was founded in 2020.",
        supports_claim=True,
        provider="a",
    )
    second = EvidenceItem(
        claim="The project was founded in 2020.",
        source=_source("https://b.example/record", "Source B"),
        evidence_statement="The project was founded in 2021.",
        supports_claim=False,
        provider="b",
    )
    registry = FactVerificationProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    registry.register(FakeVerificationProvider("a", (first,)), priority=1)
    registry.register(FakeVerificationProvider("b", (second,)), priority=2)

    from an02.models import FactCheckRequest
    report = FactVerificationCoordinator(providers=registry).run(
        FactCheckRequest(mission_id=mission_id, research=_research(mission_id))
    )

    claim = report.claims[0]
    assert claim.verification_status is VerificationStatus.CONTRADICTED
    assert claim.conflicting_sources
    assert claim.manual_review_required
    assert claim.contradiction_severity > 0


def test_provider_failure_degrades_to_structured_partial_verification():
    mission_id = uuid4()
    working = EvidenceItem(
        claim="The project was founded in 2020.",
        source=_source("https://working.example/record", "Working Source"),
        evidence_statement="The project was founded in 2020.",
        supports_claim=True,
        provider="working",
    )
    failing = FakeVerificationProvider("failing", (), fail=True)
    good = FakeVerificationProvider("working", (working,))
    registry = FactVerificationProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    registry.register(failing, priority=1)
    registry.register(good, priority=2)

    from an02.models import FactCheckRequest
    report = FactVerificationCoordinator(providers=registry).run(
        FactCheckRequest(mission_id=mission_id, research=_research(mission_id))
    )

    assert report.claims
    assert report.provider_failures
    assert report.manual_review_required


def test_an17_handler_requires_an01_dependency_and_returns_an02_result():
    mission_id = uuid4()
    evidence = EvidenceItem(
        claim="The project was founded in 2020.",
        source=_source("https://evidence.example/record", "Evidence Source"),
        evidence_statement="The project was founded in 2020.",
        supports_claim=True,
        provider="provider",
    )
    registry = FactVerificationProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    registry.register(FakeVerificationProvider("provider", (evidence,)))

    guardian = FactGuardian(
        coordinator=FactVerificationCoordinator(providers=registry)
    )
    from shared.schemas import AgentResult, ExecutionStatus

    dependency = AgentResult(
        agent_id=AgentID.RESEARCH_CORE,
        mission_id=mission_id,
        status=ExecutionStatus.SUCCESS,
        payload=_research(mission_id),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    handler = guardian.as_agent_handler()
    result = handler(
        AgentExecutionContext(
            mission_id=mission_id,
            agent_id=AgentID.FACT_GUARDIAN,
            stage=WorkflowStage.FACT_CHECK,
            dependency_results={"research": dependency},
        )
    )
    assert result.agent_id is AgentID.FACT_GUARDIAN
    assert result.payload is not None
    assert result.payload.claims
