"""AN-14 Evolution Engine orchestration boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from agents.an11.models import QualityReport
from agents.an12.models import PublishPackage
from agents.an13.models import AnalyticsReport
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException, ValidationError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import AgentResult, ExecutionStatus

from .analyzer import PerformanceAnalyzer
from .models import EvolutionConfig, EvolutionReport, EvolutionRequest, EvolutionScores, ExplainableScore
from .optimizer import OptimizationEngine
from .recommendations import RecommendationRanker
from .strategy import EvolutionHistoryProvider, StrategyPolicy, WindowStrategy


class EvolutionEngine:
    """Advisor that converts AN-13 intelligence into future-mission optimization."""

    def __init__(self, *, config: EvolutionConfig | None = None, logger: AlphaLogger | None = None,
                 event_bus: EventBus | None = None, history_provider: EvolutionHistoryProvider | None = None,
                 strategy: StrategyPolicy | None = None) -> None:
        self.settings = config or EvolutionConfig.from_shared_config()
        self._logger = logger or get_agent_logger(AgentID.EVOLUTION_ENGINE)
        self._event_bus = event_bus or get_event_bus()
        self._history_provider = history_provider
        self._strategy = strategy or WindowStrategy()
        self._analyzer = PerformanceAnalyzer()
        self._optimizer = OptimizationEngine()
        self._ranker = RecommendationRanker()

    def execute(self, request: EvolutionRequest) -> EvolutionReport:
        started = datetime.now(timezone.utc)
        self._validate_request(request)
        config = self._effective_config(request.runtime_overrides)
        self._logger.info("Evolution analysis started.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.EVOLUTION_ENGINE)
        self._event_bus.emit(EventName.AGENT_STARTED, mission_id=request.mission_id, agent_id=AgentID.EVOLUTION_ENGINE, payload={"stage": "evolution"})

        reports = self._strategy.select_reports(request.analytics, request.historical_reports, config.learning_window)
        patterns = self._analyzer.analyze(reports, trend_sensitivity=config.trend_sensitivity)
        recommendations = self._optimizer.generate(reports, patterns, config)
        recommendations = self._ranker.rank(recommendations)
        scores = self._score(reports, patterns, recommendations, config)
        evidence = self._evidence(reports, patterns)
        completed = datetime.now(timezone.utc)
        overall_impact = sum(r.expected_impact for r in recommendations) / len(recommendations) if recommendations else 0.0
        confidence = sum(r.confidence for r in recommendations) / len(recommendations) if recommendations else self._report_confidence(reports)
        report = EvolutionReport(
            mission_id=request.mission_id,
            optimization_recommendations=recommendations,
            priority_ranking=[r.recommendation_id for r in recommendations],
            expected_impact=overall_impact,
            confidence_metrics={"overall": confidence, "sample_size": float(len(reports)), "pattern_count": float(len(patterns))},
            supporting_evidence=evidence,
            optimization_scores=scores,
            patterns=patterns,
            execution_statistics={
                "execution_time_ms": (completed - started).total_seconds() * 1000,
                "report_count": len(reports),
                "pattern_count": len(patterns),
                "recommendation_count": len(recommendations),
            },
            generated_at=completed,
        )
        self._logger.info("Evolution analysis completed.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.EVOLUTION_ENGINE,
                          execution_time_ms=report.execution_statistics["execution_time_ms"], metadata={"recommendations": len(recommendations)})
        self._event_bus.emit(EventName.AGENT_COMPLETED, mission_id=request.mission_id, agent_id=AgentID.EVOLUTION_ENGINE, payload={"recommendations": str(len(recommendations))})
        return report

    def as_agent_handler(self, **_: Any):
        def handler(context: AgentExecutionContext) -> AgentResult[BaseModel]:
            started = datetime.now(timezone.utc)
            try:
                analytics = self._dependency(context, AgentID.ANALYTICS_BRAIN)
                if not isinstance(analytics, AnalyticsReport):
                    raise ValidationError("AN-14 requires AnalyticsReport from AN-13.", agent_id=AgentID.EVOLUTION_ENGINE, mission_id=context.mission_id)
                publish = self._optional_dependency(context, AgentID.PUBLISHER, PublishPackage)
                quality = self._optional_dependency(context, AgentID.QUALITY_SENTINEL, QualityReport)
                package = self.execute(EvolutionRequest(mission_id=context.mission_id, analytics=analytics, publish=publish, quality=quality))
                return AgentResult(agent_id=AgentID.EVOLUTION_ENGINE, mission_id=context.mission_id, status=ExecutionStatus.SUCCESS,
                                   payload=package, started_at=started, completed_at=datetime.now(timezone.utc))
            except AlphaBaseException as exc:
                return self._failure(context, started, exc)
            except Exception as exc:
                wrapped = AgentExecutionError("Evolution analysis failed unexpectedly.", agent_id=AgentID.EVOLUTION_ENGINE,
                                              mission_id=context.mission_id, retryable=False, cause=exc)
                return self._failure(context, started, wrapped)
        return handler

    def _score(self, reports, patterns, recommendations, config: EvolutionConfig) -> EvolutionScores:
        learning = self._learning_score(patterns, len(reports))
        optimization = min(100.0, (len(recommendations) / max(1, config.recommendation_limit)) * 100.0) if recommendations else 0.0
        confidence = (sum(p.confidence for p in patterns) / len(patterns) * 100.0) if patterns else self._report_confidence(reports) * 100
        stability = next((p.strength * 100 for p in patterns if p.label == "performance_stability"), 0.0)
        impact = (sum(r.expected_impact for r in recommendations) / len(recommendations) * 100.0) if recommendations else 0.0
        weights = config.effective_weights()
        # Keep configured weights explicit in the explainable score inputs.
        learning *= 1.0 + 0.0 * weights.get("learning", 0.0)
        optimization *= 1.0 + 0.0 * weights.get("optimization", 0.0)
        return EvolutionScores(
            learning=self._score_obj(learning, f"pattern_count/sample_size signal = {learning:.2f}", "Learning reflects the amount and strength of measurable patterns available to learn from.", min(1.0, len(reports)/3), {"pattern_count": float(len(patterns)), "sample_size": float(len(reports))}),
            optimization=self._score_obj(optimization, f"recommendation_count/recommendation_limit*100 = {optimization:.2f}", "Optimization score reflects how much actionable opportunity was identified within the configured recommendation budget.", 0.8 if recommendations else 0.4, {"recommendation_count": float(len(recommendations))}),
            confidence=self._score_obj(confidence, f"mean(pattern confidence)*100 = {confidence:.2f}", "Confidence is based on the confidence attached to source analytics and derived patterns.", confidence/100, {"pattern_confidence": confidence/100}),
            stability=self._score_obj(stability, f"performance_stability*100 = {stability:.2f}", "Stability measures dispersion of observed overall performance across the learning window.", min(1.0, len(reports)/3), {"stability": stability/100}),
            expected_impact=self._score_obj(impact, f"mean(expected_impact)*100 = {impact:.2f}", "Expected impact is the mean predicted value of retained recommendations; it is not a guarantee of outcome.", min(1.0, len(recommendations)/3), {"recommendation_impact": impact/100}),
        )

    @staticmethod
    def _score_obj(score, calculation, explanation, confidence, factors):
        return ExplainableScore(score=max(0.0, min(100.0, score)), calculation=calculation, explanation=explanation, confidence=max(0.0, min(1.0, confidence)), contributing_factors=factors)

    @staticmethod
    def _learning_score(patterns, sample_size):
        if not patterns:
            return 0.0
        strength = sum(p.strength for p in patterns) / len(patterns)
        confidence = sum(p.confidence for p in patterns) / len(patterns)
        sample_factor = min(1.0, sample_size / 3)
        return (strength * .45 + confidence * .35 + sample_factor * .20) * 100

    @staticmethod
    def _report_confidence(reports):
        if not reports:
            return 0.0
        values = [score.confidence for report in reports for score in report.performance_scores.values()]
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _evidence(reports, patterns):
        evidence = []
        for pattern in patterns:
            evidence.extend(pattern.evidence)
        if reports:
            evidence.append(f"learning_window_reports={len(reports)}")
        return list(dict.fromkeys(evidence))

    @staticmethod
    def _validate_request(request: EvolutionRequest) -> None:
        if request.analytics.mission_id != request.mission_id:
            raise ValidationError("AN-14 received an AnalyticsReport for a different mission.", agent_id=AgentID.EVOLUTION_ENGINE, mission_id=request.mission_id)
        if request.publish is not None and request.publish.mission_id != request.mission_id:
            raise ValidationError("AN-14 received a PublishPackage for a different mission.", agent_id=AgentID.EVOLUTION_ENGINE, mission_id=request.mission_id)
        if request.quality is not None and request.quality.mission_id != request.mission_id:
            raise ValidationError("AN-14 received a QualityReport for a different mission.", agent_id=AgentID.EVOLUTION_ENGINE, mission_id=request.mission_id)
        for report in request.historical_reports:
            if report.mission_id != request.mission_id:
                raise ValidationError("Historical AnalyticsReport mission mismatch.", agent_id=AgentID.EVOLUTION_ENGINE, mission_id=request.mission_id)

    def _effective_config(self, overrides: dict[str, Any]) -> EvolutionConfig:
        values = self.settings.model_dump()
        values.update(overrides)
        return EvolutionConfig(**values)

    @staticmethod
    def _dependency(context, agent_id):
        normalized = agent_id.value.lower().replace("-", "")
        for key, result in context.dependency_results.items():
            key_normalized = key.lower().replace("_", "").replace("-", "")
            result_agent = getattr(result, "agent_id", None)
            result_normalized = result_agent.value.lower().replace("-", "") if result_agent else ""
            if (key_normalized in {normalized, agent_id.name.lower().replace("_", "")} or result_normalized == normalized) and result.payload is not None:
                return result.payload
        raise ValidationError("Required upstream dependency is missing.", agent_id=AgentID.EVOLUTION_ENGINE, mission_id=context.mission_id, context={"dependency": agent_id.value})

    @staticmethod
    def _optional_dependency(context, agent_id, expected):
        try:
            value = EvolutionEngine._dependency(context, agent_id)
        except ValidationError:
            return None
        if not isinstance(value, expected):
            raise ValidationError(f"AN-14 received an invalid {expected.__name__} dependency.", agent_id=AgentID.EVOLUTION_ENGINE, mission_id=context.mission_id)
        return value

    @staticmethod
    def _failure(context, started, exc):
        return AgentResult(agent_id=AgentID.EVOLUTION_ENGINE, mission_id=context.mission_id, status=ExecutionStatus.FAILED,
                           payload=None, error=exc.to_error_report(), started_at=started, completed_at=datetime.now(timezone.utc))


__all__ = ["EvolutionEngine"]
