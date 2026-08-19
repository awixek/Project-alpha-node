from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an13.analytics_brain import AnalyticsBrain
from agents.an13.models import AnalyticsConfig, AnalyticsRequest, MetricSource, NormalizedMetric
from agents.an13.recommendations import RecommendationEngine
from agents.an13.trends import TrendDetector
from agents.an04.models import SEOResult
from agents.an09.models import VideoPackage
from agents.an10.models import ThumbnailPackage
from agents.an11.models import QualityReport
from agents.an12.models import PublishPackage
from shared.constants import Platform


def _request():
    mission_id = uuid4()
    publish = type("Publish", (), {"mission_id": mission_id, "platform_records": []})()
    quality = type("Quality", (), {"mission_id": mission_id, "overall_score": 80.0})()
    seo = type("SEO", (), {"mission_id": mission_id, "seo_score": 55.0})()
    thumbnail = type("Thumb", (), {"mission_id": mission_id, "ctr_report": type("CTR", (), {"top_score": 50.0})()})()
    video = type("Video", (), {"mission_id": mission_id})()
    return AnalyticsRequest.model_construct(mission_id=mission_id, publish=publish, quality=quality, seo=seo, thumbnail=thumbnail, video=video, runtime_overrides={})


def test_config_weights_normalize():
    cfg = AnalyticsConfig(scoring_weights={"performance": 2, "engagement": 1})
    assert sum(cfg.effective_weights().values()) == pytest.approx(1.0)


def test_config_rejects_zero_weights():
    with pytest.raises(ValueError):
        AnalyticsConfig(scoring_weights={"performance": 0}).effective_weights()


def test_trend_detector_requires_two_observations():
    cfg = AnalyticsConfig()
    metrics = [NormalizedMetric(platform=Platform.YOUTUBE)]
    report = TrendDetector().detect(metrics, cfg)
    assert report.reports[0].direction == "insufficient_data"


def test_trend_detector_identifies_growth():
    cfg = AnalyticsConfig()
    now = datetime.now(timezone.utc)
    metrics = [
        NormalizedMetric(platform=Platform.YOUTUBE, captured_at=now, views=10),
        NormalizedMetric(platform=Platform.YOUTUBE, captured_at=now, views=100),
    ]
    report = TrendDetector().detect(metrics, cfg)
    assert report.reports[0].direction == "growing"
    assert report.reports[0].strength > 0


def test_provider_protocol_is_vendor_neutral():
    class FakeProvider:
        name = "fake"
        def collect(self, request, platform):
            return [NormalizedMetric(platform=platform, source=MetricSource.PROVIDER, provider=self.name, views=100, impressions=1000)]
    provider = FakeProvider()
    assert provider.name == "fake"
    assert provider.collect(_request(), Platform.YOUTUBE)[0].views == 100


def test_provider_failure_isolated():
    class BrokenProvider:
        name = "broken"
        def collect(self, request, platform):
            raise RuntimeError("provider unavailable")
    class GoodProvider:
        name = "good"
        def collect(self, request, platform):
            return [NormalizedMetric(platform=platform, source=MetricSource.PROVIDER, provider=self.name, views=250, impressions=1000)]
    brain = AnalyticsBrain(providers=[BrokenProvider(), GoodProvider()], config=AnalyticsConfig())
    report = brain.execute(_request())
    assert len(report.normalized_metrics) >= 1
    assert report.confidence_metrics["provider_failure_rate"] > 0


def test_analytics_report_contains_explainable_scores():
    class Provider:
        name = "test"
        def collect(self, request, platform):
            return [NormalizedMetric(platform=platform, source=MetricSource.PROVIDER, provider=self.name,
                                     views=500, impressions=1000, click_through_rate=.5,
                                     likes=40, comments=10, shares=5, audience_retention=.65,
                                     search_traffic=80, recommendation_traffic=120)]
    report = AnalyticsBrain(providers=[Provider()], config=AnalyticsConfig()).execute(_request())
    assert "overall" in report.performance_scores
    assert report.performance_scores["overall"].calculation
    assert report.performance_scores["overall"].contributing_factors


def test_recommendation_engine_targets_low_performance():
    request = _request()
    recommendations = RecommendationEngine().generate(
        request,
        scores={"performance": 20, "retention": 40, "seo": 50, "thumbnail": 50, "publishing": 100, "audience": 20},
        config=AnalyticsConfig(recommendation_threshold=.5),
    )
    targets = {item.target_agent.value for item in recommendations}
    assert "AN-01" in targets
    assert "AN-03" in targets
    assert "AN-04" in targets


def test_runtime_config_override_is_applied():
    class Provider:
        name = "test"
        def collect(self, request, platform):
            return [NormalizedMetric(platform=platform, source=MetricSource.PROVIDER, provider=self.name, views=10)]
    request = _request().model_copy(update={"runtime_overrides": {"trend_detection_window": 3}})
    report = AnalyticsBrain(providers=[Provider()]).execute(request)
    assert report.trend_analysis.window_size == 3


def test_empty_provider_set_degrades_gracefully():
    report = AnalyticsBrain(config=AnalyticsConfig()).execute(_request())
    assert report.normalized_metrics == []
    assert report.confidence_metrics["overall"] < 1


def test_request_mission_mismatch_is_rejected():
    request = _request()
    request.quality.mission_id = uuid4()
    with pytest.raises(Exception):
        AnalyticsBrain().execute(request)

def test_an17_handler_contract_returns_agent_result():
    from agents.an13.models import AnalyticsReport
    from agents.an17.dispatcher import AgentExecutionContext
    from shared.constants import AgentID, WorkflowStage
    from shared.schemas import AgentResult, ExecutionStatus
    from agents.an12.models import PublicationStatus

    mission_id = uuid4()
    publish = PublishPackage.model_construct(mission_id=mission_id, platform_records=[], status=PublicationStatus.VERIFIED)
    quality = QualityReport.model_construct(mission_id=mission_id, overall_score=85.0)
    seo = SEOResult.model_construct(mission_id=mission_id, seo_score=70.0)
    thumbnail = ThumbnailPackage.model_construct(mission_id=mission_id, ctr_report=type("CTR", (), {"top_score": 75.0})())
    video = VideoPackage.model_construct(mission_id=mission_id)

    def dep(agent, payload):
        return AgentResult(agent_id=agent, mission_id=mission_id, status=ExecutionStatus.SUCCESS,
                           payload=payload, started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc))

    context = AgentExecutionContext(
        mission_id=mission_id,
        agent_id=AgentID.ANALYTICS_BRAIN,
        stage=WorkflowStage.ANALYTICS,
        dependency_results={
            "AN-12": dep(AgentID.PUBLISHER, publish),
            "AN-11": dep(AgentID.QUALITY_SENTINEL, quality),
            "AN-04": dep(AgentID.SEO_BRAIN, seo),
            "AN-10": dep(AgentID.THUMBNAIL_STUDIO, thumbnail),
            "AN-09": dep(AgentID.VIDEO_FORGE, video),
        },
    )
    result = AnalyticsBrain(config=AnalyticsConfig()).as_agent_handler()(context)
    assert result.agent_id is AgentID.ANALYTICS_BRAIN
    assert result.mission_id == mission_id
    assert result.status is ExecutionStatus.SUCCESS
    assert isinstance(result.payload, AnalyticsReport)
