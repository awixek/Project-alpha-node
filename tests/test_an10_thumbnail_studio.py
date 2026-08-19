from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an03.models import ScriptDocument, ScriptMetadata, ScriptOutline, ScriptSection, ScriptStyle, CitationMode, SectionType
from agents.an04.models import SEOResult, SEOScoreBreakdown, OpenGraphMetadata, TwitterCardMetadata
from agents.an05.models import VisionPlan, VisionScene, Storyboard, PromptPackage, ContinuityPackage, VisualStyle, ShotType, CameraAngle, CameraMovement, TransitionType
from agents.an06.models import AssetPackage, AssetManifest, GenerationMetrics, QualityReport, ContinuityReport, OptimizationReport, GeneratedAsset, GenerationKind, GenerationStatus, QualityStatus, AssetManifestItem, ProviderHealth
from agents.an09.models import VideoPackage, Timeline, RenderJob, RenderMetrics, VideoQualityReport, SynchronizationReport, RenderStatus
from agents.an10.models import ThumbnailConfig, ThumbnailRequest
from agents.an10.thumbnail_studio import ThumbnailStudio
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import SEOMetadata


def build_inputs():
    mid = uuid4()
    now = datetime.now(timezone.utc)
    section = ScriptSection(order=0, heading="History", narration="A documented story about an important historical development.", estimated_duration_seconds=10, section_type=SectionType.BACKGROUND)
    script = ScriptDocument(mission_id=mid, title="How Ancient Astronomy Changed Observation", sections=[section],
                            outline=ScriptOutline(title="How Ancient Astronomy Changed Observation", thesis="Observation changed computation.", sections=[SectionType.INTRO], style=ScriptStyle.DOCUMENTARY),
                            metadata=ScriptMetadata(style=ScriptStyle.DOCUMENTARY, language="en", tone="clear", target_duration_seconds=30, estimated_duration_seconds=10, word_count=8, citation_mode=CitationMode.INLINE))
    scene = VisionScene(order=0, script_section_order=0, prompt="Historical observatory", duration_seconds=10, scene_number=1,
        narrative_goal="Explain observation", visual_goal="Show observatory", camera_type=ShotType.ESTABLISHING,
        camera_angle=CameraAngle.EYE_LEVEL, camera_movement=CameraMovement.SLOW_PUSH, subject="astronomical observatory",
        characters=[], environment="historical observatory", lighting="natural", time_of_day="day", weather="clear",
        mood="focused", color_palette="earth and sky", composition="strong focal subject with negative space",
        depth="layered", lens_suggestion="35mm", animation_suggestion="slow push", transition_type=TransitionType.CUT,
        sound_suggestion="ambient", music_mood="documentary", image_prompt="historical observatory", negative_prompt="modern objects",
        video_prompt="slow cinematic push", confidence_score=0.95)
    vision = VisionPlan(mission_id=mid, shots=[scene], storyboard=Storyboard(sequence=[1], timing=[10.0], transitions=[TransitionType.CUT], pacing="steady", emotional_rhythm=["focused"]),
        scenes=[scene], prompt_package=PromptPackage(image_prompts={1: scene.image_prompt}, video_prompts={1: scene.video_prompt}, negative_prompts={1: scene.negative_prompt}, style=VisualStyle.HISTORICAL, language="en"),
        continuity_package=ContinuityPackage(characters=[], environments=[]), asset_manifest=[], estimated_runtime_seconds=10,
        overall_confidence=0.95)
    asset = GeneratedAsset(mission_id=mid, storage_path="/assets/scene1.png", provider="test", asset_type="image",
        scene_id=1, generation_kind=GenerationKind.IMAGE, generation_status=GenerationStatus.GENERATED, quality_status=QualityStatus.PASSED)
    assets = AssetPackage(mission_id=mid, assets=[asset], asset_manifest=AssetManifest(items=[AssetManifestItem(asset_id=asset.asset_id, scene_id=1, asset_type=asset.asset_type, storage_path=asset.storage_path, provider="test")]),
        generation_metrics=GenerationMetrics(scenes_requested=1, scenes_completed=1, assets_generated=1), provider_statistics=[ProviderHealth(provider="test")],
        quality_report=QualityReport(passed=True, score=100, checked_assets=1), continuity_report=ContinuityReport(passed=True, checked_scenes=1), optimization_report=OptimizationReport(applied=True))
    timeline = Timeline(mission_id=mid, scenes=[], total_runtime=10)
    video = VideoPackage(mission_id=mid, timeline=timeline, render_job=RenderJob(mission_id=mid, status=RenderStatus.COMPLETED),
        render_metrics=RenderMetrics(scenes_requested=1), quality_report=VideoQualityReport(passed=True, score=100), synchronization_report=SynchronizationReport(passed=True, score=100))
    seo = SEOResult(mission_id=mid, optimized_title=script.title, slug="ancient-astronomy", description="Documentary", excerpt="Documentary", metadata=SEOMetadata(mission_id=mid, primary_title=script.title, description="Documentary"),
        open_graph=OpenGraphMetadata(title=script.title, description="Documentary"), twitter_card=TwitterCardMetadata(title=script.title, description="Documentary"), score_breakdown=SEOScoreBreakdown(title_quality=90, keyword_coverage=80, readability=90, keyword_density=90, content_completeness=90, clickbait_penalty=0), seo_score=88, readability_score=90, clickbait_score=10, keyword_density=1)
    return mid, script, vision, assets, video, seo


def test_generation_and_ranking():
    mid, script, vision, assets, video, seo = build_inputs()
    package = ThumbnailStudio(settings=ThumbnailConfig(number_of_candidates=5)).execute(ThumbnailRequest(mission_id=mid, video=video, vision_plan=vision, assets=assets, script=script, seo=seo))
    assert package.ranked_concepts
    assert package.ranked_concepts[0].ctr_score.overall >= package.ranked_concepts[-1].ctr_score.overall
    assert package.ranked_concepts[0].prompt


def test_configuration_override():
    mid, script, vision, assets, video, seo = build_inputs()
    package = ThumbnailStudio(settings=ThumbnailConfig(number_of_candidates=2)).execute(ThumbnailRequest(mission_id=mid, video=video, vision_plan=vision, assets=assets, script=script, runtime_overrides={"number_of_candidates": 2, "aspect_ratio": "1:1"}))
    assert len(package.ranked_concepts) <= 2
    assert all(c.layout.aspect_ratio == "1:1" for c in package.ranked_concepts)


def test_invalid_input_rejected():
    mid, script, vision, assets, video, seo = build_inputs()
    with pytest.raises(Exception):
        ThumbnailStudio().execute(ThumbnailRequest(mission_id=uuid4(), video=video, vision_plan=vision, assets=assets, script=script))


def test_agent_handler_success():
    mid, script, vision, assets, video, seo = build_inputs()
    from shared.schemas import AgentResult
    deps = {
        AgentID.VIDEO_FORGE.value: AgentResult(agent_id=AgentID.VIDEO_FORGE, mission_id=mid, status="success", payload=video, started_at=datetime.now(timezone.utc)),
        AgentID.VISION_PLANNER.value: AgentResult(agent_id=AgentID.VISION_PLANNER, mission_id=mid, status="success", payload=vision, started_at=datetime.now(timezone.utc)),
        AgentID.VISION_CREATOR.value: AgentResult(agent_id=AgentID.VISION_CREATOR, mission_id=mid, status="success", payload=assets, started_at=datetime.now(timezone.utc)),
        AgentID.SCRIPT_FORGE.value: AgentResult(agent_id=AgentID.SCRIPT_FORGE, mission_id=mid, status="success", payload=script, started_at=datetime.now(timezone.utc)),
        AgentID.SEO_BRAIN.value: AgentResult(agent_id=AgentID.SEO_BRAIN, mission_id=mid, status="success", payload=seo, started_at=datetime.now(timezone.utc)),
    }
    ctx = AgentExecutionContext(mission_id=mid, agent_id=AgentID.THUMBNAIL_STUDIO, stage=WorkflowStage.THUMBNAIL, dependency_results=deps)
    result = ThumbnailStudio(settings=ThumbnailConfig(number_of_candidates=2)).as_agent_handler()(ctx)
    assert result.status.value == "success"
    assert result.payload is not None


def test_ctr_scoring_is_explainable():
    from agents.an10.models import ThumbnailLayout, VisualAnalysis
    from agents.an10.scorer import CTRScorer
    analysis = VisualAnalysis(dominant_subject="observatory", emotional_peak="focus", educational_highlight="observation", curiosity_moment="method", negative_space="clear", focal_path="subject to text", color_harmony=90, contrast=90, visual_clutter=10, mobile_visibility=95)
    layout = ThumbnailLayout(focal_region="center", text_region="left", branding_region="right", composition="clear", aspect_ratio="16:9", text_density="low")
    score = CTRScorer().score(analysis, layout, "question_style", "Why?", ThumbnailConfig())
    assert 0 <= score.overall <= 100
    assert set(score.score_breakdown) == {"curiosity", "readability", "contrast", "composition", "branding", "mobile_visibility", "confidence"}
    assert score.recommendation_reason


def test_provider_abstraction_accepts_injected_provider():
    from agents.an10.provider import ThumbnailProvider, ThumbnailProviderRequest, ThumbnailProviderResponse, ThumbnailProviderRouter
    class FakeProvider(ThumbnailProvider):
        @property
        def name(self):
            return "fake"
        def call(self, request):
            return ThumbnailProviderResponse(provider=self.name, preview_uri="memory://preview")
    router = ThumbnailProviderRouter()
    router.register(FakeProvider(), priority=0)
    response = router.preview(ThumbnailProviderRequest(mission_id=uuid4(), prompt="test", aspect_ratio="16:9"))
    assert response.provider == "fake"
    assert response.preview_uri == "memory://preview"


def test_missing_required_dependency_returns_structured_failure():
    from shared.schemas import AgentResult
    mid, script, vision, assets, video, seo = build_inputs()
    ctx = AgentExecutionContext(mission_id=mid, agent_id=AgentID.THUMBNAIL_STUDIO, stage=WorkflowStage.THUMBNAIL, dependency_results={})
    result = ThumbnailStudio().as_agent_handler()(ctx)
    assert result.status.value == "failed"
    assert result.error is not None
    assert result.error.code


def test_asset_reference_is_preserved():
    mid, script, vision, assets, video, seo = build_inputs()
    package = ThumbnailStudio(settings=ThumbnailConfig(number_of_candidates=1)).execute(ThumbnailRequest(mission_id=mid, video=video, vision_plan=vision, assets=assets, script=script))
    assert package.ranked_concepts[0].supporting_asset_ids == [assets.assets[0].asset_id]


def test_factual_guardrails_are_present():
    mid, script, vision, assets, video, seo = build_inputs()
    package = ThumbnailStudio(settings=ThumbnailConfig(number_of_candidates=3)).execute(ThumbnailRequest(mission_id=mid, video=video, vision_plan=vision, assets=assets, script=script, seo=seo))
    assert all(concept.factual_guardrails for concept in package.ranked_concepts)


def test_duplicate_strategy_concepts_are_removed():
    mid, script, vision, assets, video, seo = build_inputs()
    studio = ThumbnailStudio(settings=ThumbnailConfig(number_of_candidates=10))
    package = studio.execute(ThumbnailRequest(mission_id=mid, video=video, vision_plan=vision, assets=assets, script=script))
    fingerprints = {(c.focal_subject.lower(), (c.text_overlay or "").lower(), c.layout.composition.lower()) for c in package.ranked_concepts}
    assert len(fingerprints) == len(package.ranked_concepts)
    assert package.ctr_report.candidates_ranked == len(package.ranked_concepts)
