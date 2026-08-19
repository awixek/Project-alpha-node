from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an12.models import PlatformMetadata, PublicationRecord, PublicationStatus, PublishPackage
from agents.an13.models import AnalyticsReport, PerformanceScore, TrendAnalysis, TrendReport
from agents.an14.models import EvolutionReport, EvolutionScores, ExplainableScore, OptimizationRecommendation, EvolutionTarget
from agents.an15.adapters import AdapterRegistry
from agents.an15.models import DistributionStatus, PlatformProfile, RepurposeConfig, RepurposeRequest, TransformationType
from agents.an15.planner import RepurposePlanner
from agents.an15.republisher import OmniRepublisher
from agents.an15.transformer import ContentTransformer
from agents.an15.validator import DistributionValidator
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, Platform, WorkflowStage
from shared.schemas import AgentResult, ExecutionStatus


def packages(mission_id):
    publish = PublishPackage.model_construct(
        mission_id=mission_id,
        status=PublicationStatus.VERIFIED,
        platform_records=[PublicationRecord(platform=Platform.YOUTUBE, status=PublicationStatus.VERIFIED, url="https://example.test/video")],
        published_urls={"youtube": "https://example.test/video"},
        platform_metadata={"youtube": PlatformMetadata(title="Ancient Science Explained", description="Evidence-based research story.", hashtags=["#science", "#Science"], tags=["history", "history"])},
        verification_report=[], retry_history=[], publishing_history=[], audit_report=None, execution_metrics=None,
    )
    scores = {name: PerformanceScore(score=70, calculation="value=70", explanation="test", confidence=.8) for name in ("performance", "engagement", "seo", "retention", "thumbnail", "publishing", "audience")}
    analytics = AnalyticsReport.model_construct(
        mission_id=mission_id,
        normalized_metrics=[],
        trend_analysis=TrendAnalysis(reports=[TrendReport(name="search_growth", direction="growing", strength=.8, confidence=.8, evidence=["growth"], explanation="growth")], window_size=7, sample_size=1),
        performance_scores=scores,
        audience_insights=[], seo_insights=[], thumbnail_insights=[], publishing_insights=[], recommendation_report=[], confidence_metrics={"overall": .8}, execution_statistics={},
    )
    explain = ExplainableScore(score=75, calculation="test", explanation="test", confidence=.8)
    evolution = EvolutionReport.model_construct(
        mission_id=mission_id,
        optimization_recommendations=[OptimizationRecommendation(target_agent=EvolutionTarget.AN04, action="Use clearer search intent", rationale="SEO signal", expected_impact=.7, confidence=.8, optimization_priority=2, implementation_difficulty=2)],
        priority_ranking=[], expected_impact=.7, confidence_metrics={"overall": .8}, supporting_evidence=["search_growth"],
        optimization_scores=EvolutionScores(learning=explain, optimization=explain, confidence=explain, stability=explain, expected_impact=explain), patterns=[], execution_statistics={}
    )
    return publish, analytics, evolution


def request(mission_id=None, **kwargs):
    mission_id = mission_id or uuid4()
    publish, analytics, evolution = packages(mission_id)
    return RepurposeRequest(mission_id=mission_id, publish=publish, analytics=analytics, evolution=evolution, **kwargs)


def test_platform_profiles_cover_supported_destinations():
    planner = RepurposePlanner()
    config = RepurposeConfig(enabled_platforms=[Platform.INSTAGRAM, Platform.TIKTOK, Platform.LINKEDIN, Platform.WEBSITE])
    profiles = [planner.profile(p, config) for p in config.enabled_platforms]
    assert [p.aspect_ratio for p in profiles] == ["9:16", "9:16", "16:9", "16:9"]


def test_transformation_engine_is_provider_neutral_and_deterministic():
    planner = RepurposePlanner()
    profile = planner.profile(Platform.TIKTOK, RepurposeConfig())
    asset = ContentTransformer().transform(platform=Platform.TIKTOK, transformation=TransformationType.SHORT_VIDEO,
                                           source_title="Research", source_text="A " * 200, profile=profile, source_reference="script:1")
    assert asset.platform is Platform.TIKTOK
    assert asset.transformation is TransformationType.SHORT_VIDEO
    assert asset.body
    assert asset.aspect_ratio == "9:16"


def test_full_repurposing_produces_platform_packages_and_deduplicated_metadata():
    mission = uuid4()
    package = OmniRepublisher(config=RepurposeConfig(enabled_platforms=[Platform.YOUTUBE, Platform.INSTAGRAM])).execute(request(mission))
    assert package.mission_id == mission
    assert len(package.distributions) == 2
    assert package.platform_metadata["youtube"].hashtags == ["#science"]
    assert package.execution_metrics.assets_generated == 2
    assert all(d.status in {DistributionStatus.READY, DistributionStatus.WARNING} for d in package.distributions)


def test_configuration_override_changes_enabled_platforms():
    mission = uuid4()
    engine = OmniRepublisher(config=RepurposeConfig(enabled_platforms=[Platform.YOUTUBE]))
    package = engine.execute(request(mission, runtime_overrides={"enabled_platforms": [Platform.TELEGRAM, Platform.WEBSITE]}))
    assert [d.platform for d in package.distributions] == [Platform.TELEGRAM, Platform.WEBSITE]


def test_custom_adapter_can_change_transformation_without_business_logic_change():
    class Adapter:
        platform = Platform.LINKEDIN
        def transform(self, *, source_title, source_text, profile):
            return "Professional Title", "Professional adaptation"
        def optimize_metadata(self, **kwargs):
            raise AssertionError("metadata adapter is optional")

    registry = AdapterRegistry([Adapter()])
    planner = RepurposePlanner()
    profile = planner.profile(Platform.LINKEDIN, RepurposeConfig())
    asset = ContentTransformer(registry).transform(platform=Platform.LINKEDIN, transformation=TransformationType.LINKEDIN_ARTICLE,
                                                   source_title="Original", source_text="Original body", profile=profile, source_reference="script:1")
    assert asset.title == "Professional Title"
    assert asset.body == "Professional adaptation"


def test_validator_detects_platform_constraint_violations():
    from agents.an15.models import PlatformDistribution, PlatformMetadata, TransformedAsset
    profile = PlatformProfile(platform=Platform.X, max_title_chars=5, max_text_chars=5, max_hashtags=1)
    asset = TransformedAsset(platform=Platform.X, transformation=TransformationType.SOCIAL_THREAD, source_reference="x", title="Long", body="Long body")
    distribution = PlatformDistribution(platform=Platform.X, status=DistributionStatus.READY, profile=profile, assets=[asset],
                                        metadata=PlatformMetadata(title="Too long", description="Too long", hashtags=["#a", "#b"]))
    issues = DistributionValidator().validate(distribution)
    codes = {i.code for i in issues}
    assert {"title_limit", "text_limit", "hashtag_limit"}.issubset(codes)


def test_duplicate_transformed_content_is_flagged():
    from agents.an15.models import PlatformDistribution, PlatformMetadata, TransformedAsset
    profile = PlatformProfile(platform=Platform.TELEGRAM)
    a = TransformedAsset(platform=Platform.TELEGRAM, transformation=TransformationType.TELEGRAM_POST, source_reference="1", title="T", body="same")
    b = TransformedAsset(platform=Platform.TELEGRAM, transformation=TransformationType.TELEGRAM_POST, source_reference="2", title="T", body="same")
    distribution = PlatformDistribution(platform=Platform.TELEGRAM, status=DistributionStatus.READY, profile=profile, assets=[a, b], metadata=PlatformMetadata(title="T", description="same"))
    assert any(i.code == "duplicate_asset" for i in DistributionValidator().validate(distribution))


def test_structured_failure_for_missing_required_dependency():
    mission = uuid4()
    context = AgentExecutionContext(mission_id=mission, agent_id=AgentID.OMNI_REPUBLISHER, stage=WorkflowStage.ANALYTICS, dependency_results={})
    result = OmniRepublisher().as_agent_handler()(context)
    assert result.status is ExecutionStatus.FAILED
    assert result.agent_id is AgentID.OMNI_REPUBLISHER
    assert result.error is not None


def test_an17_handler_accepts_canonical_upstream_packages():
    mission = uuid4()
    publish, analytics, evolution = packages(mission)
    context = AgentExecutionContext(
        mission_id=mission,
        agent_id=AgentID.OMNI_REPUBLISHER,
        stage=WorkflowStage.ANALYTICS,
        dependency_results={
            "AN-12": AgentResult(agent_id=AgentID.PUBLISHER, mission_id=mission, status=ExecutionStatus.SUCCESS, payload=publish, started_at=datetime.now(timezone.utc)),
            "AN-13": AgentResult(agent_id=AgentID.ANALYTICS_BRAIN, mission_id=mission, status=ExecutionStatus.SUCCESS, payload=analytics, started_at=datetime.now(timezone.utc)),
            "AN-14": AgentResult(agent_id=AgentID.EVOLUTION_ENGINE, mission_id=mission, status=ExecutionStatus.SUCCESS, payload=evolution, started_at=datetime.now(timezone.utc)),
        },
    )
    result = OmniRepublisher(config=RepurposeConfig(enabled_platforms=[Platform.YOUTUBE])).as_agent_handler()(context)
    assert result.status is ExecutionStatus.SUCCESS
    assert result.payload is not None
    assert result.payload.agent_id is AgentID.OMNI_REPUBLISHER


def test_mission_mismatch_is_rejected():
    mission = uuid4()
    _, analytics, evolution = packages(mission)
    other_publish, _, _ = packages(uuid4())
    with pytest.raises(Exception):
        OmniRepublisher().execute(RepurposeRequest(mission_id=mission, publish=other_publish, analytics=analytics, evolution=evolution))
