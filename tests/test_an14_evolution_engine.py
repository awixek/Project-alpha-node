from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an14.analyzer import PerformanceAnalyzer
from agents.an14.evolution_engine import EvolutionEngine
from agents.an14.models import EvolutionConfig, EvolutionReport, EvolutionRequest
from agents.an14.optimizer import OptimizationEngine
from agents.an14.strategy import EvolutionHistoryProvider, WindowStrategy
from agents.an13.models import AnalyticsReport, PerformanceScore, TrendAnalysis, TrendReport
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import AgentResult, ExecutionStatus


def report(mission_id, *, performance=70, retention=60, seo=70, thumbnail=70, publishing=100, audience=65, confidence=.8, generated_at=None):
    scores = {}
    for name, value in {"performance": performance, "engagement": 65, "seo": seo, "retention": retention, "thumbnail": thumbnail, "publishing": publishing, "audience": audience}.items():
        scores[name] = PerformanceScore(score=value, calculation=f"value={value}", explanation="test", confidence=confidence, contributing_factors={name: float(value)})
    scores["overall"] = PerformanceScore(score=sum(v.score for v in scores.values()) / len(scores), calculation="mean", explanation="test", confidence=confidence)
    return AnalyticsReport(mission_id=mission_id, trend_analysis=TrendAnalysis(reports=[TrendReport(name="search_growth", direction="growing", strength=.8, confidence=.8, evidence=["growth"], explanation="growth")], window_size=7, sample_size=2), performance_scores=scores, generated_at=generated_at or datetime.now(timezone.utc))


def test_config_weights_normalize():
    cfg = EvolutionConfig(scoring_weights={"learning": 2, "optimization": 1})
    assert sum(cfg.effective_weights().values()) == pytest.approx(1.0)


def test_config_rejects_zero_weights():
    with pytest.raises(ValueError):
        EvolutionConfig(scoring_weights={"learning": 0}).effective_weights()


def test_analyzer_detects_success_and_failure_patterns():
    mission = uuid4()
    findings = PerformanceAnalyzer().analyze([report(mission, retention=40)], trend_sensitivity=.15)
    labels = {f.label for f in findings}
    assert "weak_retention" in labels
    assert "strong_performance" in labels


def test_optimizer_generates_targeted_recommendations():
    mission = uuid4()
    current = report(mission, retention=30, seo=40, thumbnail=45)
    patterns = PerformanceAnalyzer().analyze([current], trend_sensitivity=.1)
    recs = OptimizationEngine().generate([current], patterns, EvolutionConfig(optimization_threshold=.4, confidence_threshold=.2))
    targets = {r.target_agent.value for r in recs}
    assert "AN-03" in targets
    assert "AN-04" in targets
    assert "AN-10" in targets


def test_execute_returns_explainable_report():
    mission = uuid4()
    current = report(mission, retention=35, seo=45, thumbnail=45)
    result = EvolutionEngine(config=EvolutionConfig(optimization_threshold=.4, confidence_threshold=.2)).execute(EvolutionRequest(mission_id=mission, analytics=current))
    assert isinstance(result, EvolutionReport)
    assert result.optimization_scores.learning.calculation
    assert result.optimization_scores.expected_impact.explanation
    assert result.priority_ranking == [r.recommendation_id for r in result.optimization_recommendations]


def test_historical_window_compares_reports():
    mission = uuid4()
    old = report(mission, performance=40, generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    current = report(mission, performance=90, generated_at=datetime(2026, 8, 19, tzinfo=timezone.utc))
    selected = WindowStrategy().select_reports(current, [old], 7)
    assert selected[0].generated_at < selected[-1].generated_at
    assert len(selected) == 2


def test_runtime_config_override_changes_recommendation_limit():
    mission = uuid4()
    current = report(mission, retention=20, seo=20, thumbnail=20, performance=20)
    result = EvolutionEngine(config=EvolutionConfig(optimization_threshold=.2, confidence_threshold=.1)).execute(EvolutionRequest(mission_id=mission, analytics=current, runtime_overrides={"recommendation_limit": 2}))
    assert len(result.optimization_recommendations) <= 2


def test_optional_memory_interface_is_provider_neutral():
    class Memory(EvolutionHistoryProvider):
        def load_analytics(self, mission_id, limit):
            return []
        def store_evolution_report(self, report):
            self.report = report
    memory = Memory()
    assert memory.load_analytics(uuid4(), 3) == []


def test_an17_handler_contract():
    mission = uuid4()
    analytics = report(mission, retention=40)
    context = AgentExecutionContext(
        mission_id=mission,
        agent_id=AgentID.EVOLUTION_ENGINE,
        stage=WorkflowStage.ANALYTICS,
        dependency_results={
            "AN-13": AgentResult(agent_id=AgentID.ANALYTICS_BRAIN, mission_id=mission, status=ExecutionStatus.SUCCESS, payload=analytics,
                                  started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc))
        },
    )
    result = EvolutionEngine(config=EvolutionConfig()).as_agent_handler()(context)
    assert result.agent_id is AgentID.EVOLUTION_ENGINE
    assert result.status is ExecutionStatus.SUCCESS
    assert isinstance(result.payload, EvolutionReport)


def test_handler_returns_structured_failure_for_missing_analytics():
    mission = uuid4()
    context = AgentExecutionContext(mission_id=mission, agent_id=AgentID.EVOLUTION_ENGINE, stage=WorkflowStage.ANALYTICS, dependency_results={})
    result = EvolutionEngine().as_agent_handler()(context)
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None


def test_mission_mismatch_rejected():
    mission = uuid4()
    request = EvolutionRequest(mission_id=mission, analytics=report(uuid4()))
    with pytest.raises(Exception):
        EvolutionEngine().execute(request)
