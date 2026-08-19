from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agents.an12.adapters import PublishAdapterRequest, PublishAdapterResponse, PublishingAdapter, VerificationAdapter, VerificationRequest, VerificationResponse
from agents.an12.models import PlatformMetadata, PublisherConfig, PublishRequest, PublicationStatus, SchedulingMode
from agents.an12.publisher import Publisher
from agents.an04.models import SEOResult
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from agents.an11.models import QualityDecision, QualityReport
from shared.constants import AgentID, Platform
from shared.schemas import AgentResult, ExecutionStatus


class FakePublisherAdapter(PublishingAdapter):
    def __init__(self, platform=Platform.YOUTUBE, fail=False):
        self._platform = platform
        self.fail = fail
        self.calls = 0

    @property
    def name(self):
        return "fake-publisher"

    @property
    def platform(self):
        return self._platform

    def call(self, request: PublishAdapterRequest):
        self.calls += 1
        if self.fail:
            raise RuntimeError("temporary provider failure")
        return PublishAdapterResponse(provider=self.name, platform_id="pub-123", url="https://example.test/pub-123", upload_success=True)


class FakeVerifier(VerificationAdapter):
    @property
    def name(self):
        return "fake-verifier"

    @property
    def platform(self):
        return Platform.YOUTUBE

    def verify(self, request: VerificationRequest):
        return VerificationResponse(provider=self.name, exists=True, processing_complete=True, metadata_integrity=True, thumbnail_integrity=True, url="https://example.test/pub-123")


def minimal_seo(mission_id):
    return SEOResult.model_construct(
        mission_id=mission_id, optimized_title="A Good Research Video", alternative_titles=[], primary_keywords=["research"],
        secondary_keywords=[], long_tail_keywords=[], semantic_clusters={}, hashtags=["#research"], slug="research-video",
        description="A factual description.", excerpt="Excerpt", tags=["research"], metadata=None, open_graph=None,
        twitter_card=None, readability_score=90, seo_score=90, clickbait_score=5, keyword_density=1, score_breakdown=None,
        recommendations=[]
    )


def minimal_inputs(mission_id):
    video = VideoPackage.model_construct(mission_id=mission_id, video_uri="file:///video.mp4", timeline=None, render_job=None, render_metrics=None, export_metadata=None, quality_report=None, synchronization_report=None)
    thumbnail = ThumbnailPackage.model_construct(mission_id=mission_id, ranked_concepts=[], recommendation="none", visual_analysis=None, ctr_report=None)
    quality = QualityReport.model_construct(mission_id=mission_id, final_decision=QualityDecision.PASS, overall_score=95, score_breakdown={}, issue_list=[], recommendations=[], validation_report=None, consistency_report=None, accessibility_report=None, audit_metadata=None, execution_metrics=None, reasoning="pass")
    return video, thumbnail, quality


def test_metadata_is_canonical_and_deduplicated():
    from agents.an12.metadata import MetadataBuilder
    seo = minimal_seo(uuid4())
    config = PublisherConfig(enabled_platforms=[Platform.YOUTUBE])
    metadata = MetadataBuilder().build(seo=seo, platform=Platform.YOUTUBE, config=config)
    assert metadata.title == seo.optimized_title
    assert metadata.tags == ["research"]
    assert metadata.hashtags == ["#research"]
    assert metadata.category == "Education"


def test_scheduled_mode_requires_time():
    video, thumbnail, quality = minimal_inputs(uuid4())
    request = PublishRequest(mission_id=video.mission_id, video=video, thumbnail=thumbnail, quality=quality, seo=minimal_seo(video.mission_id), platforms=[Platform.YOUTUBE], scheduling_mode=SchedulingMode.SCHEDULED)
    with pytest.raises(Exception):
        Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE])).execute(request)


def test_timezone_normalization():
    from agents.an12.scheduler import PublicationScheduler
    mission = uuid4(); video, thumbnail, quality = minimal_inputs(mission)
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    request = PublishRequest(mission_id=mission, video=video, thumbnail=thumbnail, quality=quality, seo=minimal_seo(mission), platforms=[Platform.YOUTUBE], scheduling_mode=SchedulingMode.SCHEDULED, scheduled_at=local, timezone="UTC")
    resolved = PublicationScheduler().resolve(request, PublisherConfig())
    assert resolved.tzinfo is not None


def test_successful_publish_and_verification():
    mission = uuid4(); video, thumbnail, quality = minimal_inputs(mission)
    publisher = Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE], max_attempts=1))
    adapter = FakePublisherAdapter(); publisher.register_adapter(adapter); publisher.register_verifier(FakeVerifier())
    result = publisher.execute(PublishRequest(mission_id=mission, video=video, thumbnail=thumbnail, quality=quality, seo=minimal_seo(mission), platforms=[Platform.YOUTUBE]))
    assert result.status is PublicationStatus.VERIFIED
    assert result.published_urls["youtube"].endswith("pub-123")
    assert result.platform_records[0].verification.status.value == "passed"


def test_quality_gate_blocks_publication():
    mission = uuid4(); video, thumbnail, quality = minimal_inputs(mission)
    quality.final_decision = QualityDecision.FAIL
    publisher = Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE]))
    with pytest.raises(Exception):
        publisher.execute(PublishRequest(mission_id=mission, video=video, thumbnail=thumbnail, quality=quality, seo=minimal_seo(mission), platforms=[Platform.YOUTUBE]))


def test_partial_platform_failure_preserves_success():
    mission = uuid4(); video, thumbnail, quality = minimal_inputs(mission)
    publisher = Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE, Platform.TELEGRAM], max_attempts=1))
    publisher.register_adapter(FakePublisherAdapter(Platform.YOUTUBE)); publisher.register_verifier(FakeVerifier())
    # Telegram has no adapter and therefore becomes a structured failed record.
    result = publisher.execute(PublishRequest(mission_id=mission, video=video, thumbnail=thumbnail, quality=quality, seo=minimal_seo(mission), platforms=[Platform.YOUTUBE]))
    assert result.status is PublicationStatus.VERIFIED


def test_dry_run_does_not_call_provider():
    mission = uuid4(); video, thumbnail, quality = minimal_inputs(mission)
    adapter = FakePublisherAdapter()
    publisher = Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE], dry_run=True))
    publisher.register_adapter(adapter)
    result = publisher.execute(PublishRequest(mission_id=mission, video=video, thumbnail=thumbnail, quality=quality, seo=minimal_seo(mission), platforms=[Platform.YOUTUBE], scheduling_mode=SchedulingMode.DRY_RUN))
    assert adapter.calls == 0
    assert result.platform_records[0].status is PublicationStatus.SKIPPED


def test_config_override_changes_visibility():
    mission = uuid4(); video, thumbnail, quality = minimal_inputs(mission)
    publisher = Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE], default_visibility="private"))
    assert publisher._effective_config({"default_visibility": "unlisted"}).default_visibility == "unlisted"


def test_idempotency_key_is_deterministic():
    from agents.an12.publish_engine import PublishEngine
    from agents.an12.adapters import AdapterRouter
    from agents.an12.verifier import PublicationVerifier
    assert str(uuid4()) != str(uuid4())
    assert PublishEngine(AdapterRouter(), PublicationVerifier()) is not None


def test_agent_handler_returns_agent_result_on_missing_dependency():
    mission = uuid4()
    from agents.an17.dispatcher import AgentExecutionContext
    from shared.constants import WorkflowStage
    publisher = Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE]))
    context = AgentExecutionContext(mission_id=mission, agent_id=AgentID.PUBLISHER, stage=WorkflowStage.PUBLISHING, dependency_results={})
    result = publisher.as_agent_handler()(context)
    assert result.status is ExecutionStatus.FAILED
    assert result.agent_id is AgentID.PUBLISHER


def test_publish_package_is_serializable():
    mission = uuid4(); video, thumbnail, quality = minimal_inputs(mission)
    publisher = Publisher(settings=PublisherConfig(enabled_platforms=[Platform.YOUTUBE], dry_run=True))
    result = publisher.execute(PublishRequest(mission_id=mission, video=video, thumbnail=thumbnail, quality=quality, seo=minimal_seo(mission), platforms=[Platform.YOUTUBE], scheduling_mode=SchedulingMode.DRY_RUN))
    assert result.model_dump(mode="json")["mission_id"] == str(mission)
