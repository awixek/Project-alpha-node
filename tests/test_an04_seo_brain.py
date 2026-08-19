from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an03.models import ScriptDocument, ScriptMetadata, ScriptOutline, ScriptSection, ScriptStyle, SectionType, CitationMode
from agents.an04 import (
    SEOBrain,
    SEOBrainCoordinator,
    SEOConfig,
    SEOGenerationProvider,
    SEOGenerationProviderRegistry,
    SEOGenerationResponse,
    SEORequest,
)
from agents.an17.dispatcher import AgentExecutionContext
from shared.api_router import APIRouter
from shared.constants import AgentID, WorkflowStage
from shared.retry import RetryPolicy
from shared.schemas import AgentResult, ExecutionStatus, SourceRef, SourceReliability

NO_WAIT = RetryPolicy(max_attempts=1, delay_seconds=0, backoff_multiplier=1, timeout_seconds=5)


def source(url: str = "https://example.org/evidence") -> SourceRef:
    return SourceRef(
        url=url,
        title="Evidence Source",
        publisher="Example Publisher",
        reliability=SourceReliability.PRIMARY,
        published_at=datetime.now(timezone.utc),
    )


def script(mission_id):
    sections = [
        ScriptSection(
            order=0,
            heading="Hook",
            narration=(
                "Ancient astronomy developed precise observational methods and mathematical models. "
                "This script explains how astronomical observations, planetary models, and historical evidence "
                "fit together without overstating what the evidence proves."
            ),
            section_type=SectionType.HOOK,
        ),
        ScriptSection(
            order=1,
            heading="Main Explanation",
            narration=(
                "Researchers compare primary sources, historical records, observations, and mathematical methods. "
                "The evidence shows a long tradition of astronomy and careful measurement."
            ),
            section_type=SectionType.MAIN_EXPLANATION,
        ),
        ScriptSection(
            order=2,
            heading="Conclusion",
            narration="The evidence supports a careful, evidence-based understanding of the topic.",
            section_type=SectionType.CONCLUSION,
        ),
    ]
    metadata = ScriptMetadata(
        style=ScriptStyle.EDUCATIONAL,
        language="en",
        tone="clear",
        target_duration_seconds=300,
        estimated_duration_seconds=120,
        word_count=70,
        citation_mode=CitationMode.INLINE,
        evidence_sources=[source()],
        fact_claim_count=2,
    )
    return ScriptDocument(
        mission_id=mission_id,
        title="A History of Astronomical Observation",
        sections=sections,
        tone="clear",
        outline=ScriptOutline(
            title="A History of Astronomical Observation",
            thesis="Astronomical observation developed through careful measurement and mathematical reasoning.",
            sections=[SectionType.HOOK, SectionType.MAIN_EXPLANATION, SectionType.CONCLUSION],
            style=ScriptStyle.EDUCATIONAL,
        ),
        metadata=metadata,
        evidence_sources=[source()],
    )


class FakeSEOProvider(SEOGenerationProvider):
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
            raise ConnectionError("provider unavailable")
        return SEOGenerationResponse(
            provider=self.name,
            description="Evidence-based SEO description for the topic.",
            alternative_titles=["Astronomical Observation: What the Evidence Shows"],
        )


def coordinator(*providers, config=None):
    registry = None
    if providers:
        registry = SEOGenerationProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
        for index, provider in enumerate(providers):
            registry.register(provider, priority=index + 1)
    return SEOBrainCoordinator(config=config or SEOConfig(), provider_registry=registry)


def test_keyword_extraction_and_semantic_clusters():
    mission_id = uuid4()
    result = coordinator().run(SEORequest(mission_id=mission_id, script=script(mission_id)))
    assert result.primary_keywords
    assert result.semantic_clusters
    assert len(result.primary_keywords) == len({item.casefold() for item in result.primary_keywords})


def test_successful_seo_generation():
    mission_id = uuid4()
    result = SEOBrain(coordinator=coordinator(FakeSEOProvider())).execute(
        SEORequest(mission_id=mission_id, script=script(mission_id))
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert result.payload is not None
    assert result.payload.optimized_title
    assert result.payload.description
    assert result.payload.seo_score >= 0
    assert result.payload.metadata.primary_title == result.payload.optimized_title


def test_title_optimization_and_variations():
    mission_id = uuid4()
    result = coordinator().run(SEORequest(mission_id=mission_id, script=script(mission_id)))
    assert result.optimized_title
    assert result.alternative_titles
    assert len(result.optimized_title) <= 65


def test_metadata_generation():
    mission_id = uuid4()
    config = SEOConfig(site_url="https://example.org", locale="en_US")
    result = coordinator(config=config).run(SEORequest(mission_id=mission_id, script=script(mission_id)))
    assert result.open_graph.type == "article"
    assert result.open_graph.url == f"https://example.org/{result.slug}"
    assert result.twitter_card.card == "summary_large_image"
    assert result.metadata.mission_id == mission_id


def test_slug_generation_is_stable_and_clean():
    mission_id = uuid4()
    result = coordinator().run(SEORequest(mission_id=mission_id, script=script(mission_id)))
    assert result.slug == result.slug.casefold()
    assert " " not in result.slug
    assert not result.slug.startswith("-")
    assert not result.slug.endswith("-")


def test_seo_scoring_and_readability_are_explainable():
    mission_id = uuid4()
    result = coordinator().run(SEORequest(mission_id=mission_id, script=script(mission_id)))
    assert 0 <= result.seo_score <= 100
    assert 0 <= result.readability_score <= 100
    assert 0 <= result.clickbait_score <= 100
    assert 0 <= result.keyword_density <= 100
    assert result.score_breakdown.title_quality >= 0
    assert result.recommendations


def test_duplicate_keyword_removal():
    mission_id = uuid4()
    request = SEORequest(
        mission_id=mission_id,
        script=script(mission_id),
        runtime_overrides={"max_primary_keywords": 10, "max_secondary_keywords": 20},
    )
    result = coordinator().run(request)
    all_keywords = result.primary_keywords + result.secondary_keywords + result.long_tail_keywords
    normalized = [item.casefold() for item in all_keywords]
    assert len(normalized) == len(set(normalized))


def test_runtime_config_overrides():
    mission_id = uuid4()
    request = SEORequest(
        mission_id=mission_id,
        script=script(mission_id),
        runtime_overrides={
            "title_max_length": 50,
            "max_primary_keywords": 2,
            "max_hashtags": 3,
            "description_max_length": 100,
            "locale": "hi_IN",
        },
    )
    result = coordinator().run(request)
    assert len(result.primary_keywords) <= 2
    assert len(result.hashtags) <= 3
    assert len(result.description) <= 100
    assert result.open_graph.locale == "hi_IN"


def test_invalid_input_is_returned_as_structured_failure():
    mission_id = uuid4()
    other = uuid4()
    result = SEOBrain(coordinator=coordinator()).execute(
        SEORequest(mission_id=mission_id, script=script(other))
    )
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.agent_id is AgentID.SEO_BRAIN


def test_provider_failure_falls_back_to_deterministic_generation():
    mission_id = uuid4()
    provider = FakeSEOProvider(fail=True)
    result = coordinator(provider).run(SEORequest(mission_id=mission_id, script=script(mission_id)))
    assert result.optimized_title
    assert result.description
    assert provider.calls == 1


def test_an17_handler_requires_an03_dependency():
    mission_id = uuid4()
    brain = SEOBrain(coordinator=coordinator())
    handler = brain.as_agent_handler()
    with pytest.raises(Exception):
        handler(
            AgentExecutionContext(
                mission_id=mission_id,
                agent_id=AgentID.SEO_BRAIN,
                stage=WorkflowStage.SEO,
                dependency_results={},
            )
        )


def test_an17_handler_returns_seo_result():
    mission_id = uuid4()
    brain = SEOBrain(coordinator=coordinator())
    script_result = AgentResult(
        agent_id=AgentID.SCRIPT_FORGE,
        mission_id=mission_id,
        status=ExecutionStatus.SUCCESS,
        payload=script(mission_id),
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    result = brain.as_agent_handler()(
        AgentExecutionContext(
            mission_id=mission_id,
            agent_id=AgentID.SEO_BRAIN,
            stage=WorkflowStage.SEO,
            dependency_results={"script": script_result},
        )
    )
    assert result.agent_id is AgentID.SEO_BRAIN
    assert result.status is ExecutionStatus.SUCCESS
    assert result.payload is not None
    assert result.payload.slug
