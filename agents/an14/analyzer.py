"""Deterministic performance-pattern analysis for AN-14."""
from __future__ import annotations

from statistics import mean

from agents.an13.models import AnalyticsReport, PerformanceScore
from .models import PatternFinding, PatternType


class PerformanceAnalyzer:
    """Extracts explainable success/failure patterns from analytics reports."""

    def analyze(self, reports: list[AnalyticsReport], *, trend_sensitivity: float) -> list[PatternFinding]:
        if not reports:
            return []

        findings: list[PatternFinding] = []
        current = reports[-1]
        score_map = self._mean_scores(reports)
        thresholds = {
            "performance": 60.0,
            "engagement": 55.0,
            "seo": 60.0,
            "retention": 55.0,
            "thumbnail": 60.0,
            "publishing": 90.0,
            "audience": 55.0,
        }
        for key, threshold in thresholds.items():
            value = score_map.get(key)
            if value is None:
                continue
            if value >= threshold:
                strength = min(1.0, max(0.0, (value - threshold) / max(1.0, 100.0 - threshold)))
                findings.append(PatternFinding(
                    pattern_type=PatternType.SUCCESS,
                    label=f"strong_{key}", direction="positive", strength=strength,
                    confidence=self._score_confidence(reports, key),
                    evidence=[f"mean_{key}={value:.2f}", f"sample_size={len(reports)}"],
                    explanation=f"The observed {key} score is at or above the {threshold:.0f} baseline across the supplied analytics window.",
                ))
            else:
                strength = min(1.0, max(0.0, (threshold - value) / max(1.0, threshold)))
                findings.append(PatternFinding(
                    pattern_type=PatternType.FAILURE,
                    label=f"weak_{key}", direction="negative", strength=strength,
                    confidence=self._score_confidence(reports, key),
                    evidence=[f"mean_{key}={value:.2f}", f"sample_size={len(reports)}"],
                    explanation=f"The observed {key} score is below the {threshold:.0f} baseline and represents an optimization opportunity.",
                ))

        trend_reports = current.trend_analysis.reports
        for trend in trend_reports:
            if trend.direction in {"growing", "declining"} and trend.strength >= trend_sensitivity:
                findings.append(PatternFinding(
                    pattern_type=PatternType.TREND,
                    label=trend.name,
                    direction=trend.direction,
                    strength=trend.strength,
                    confidence=trend.confidence,
                    evidence=list(trend.evidence),
                    explanation=trend.explanation,
                ))

        stability = self._stability(reports)
        findings.append(PatternFinding(
            pattern_type=PatternType.STABILITY,
            label="performance_stability",
            direction="stable" if stability >= 0.7 else "variable",
            strength=stability,
            confidence=min(1.0, len(reports) / 3),
            evidence=[f"stability={stability:.3f}"],
            explanation="Stability is derived from dispersion of the available overall performance scores; fewer samples reduce confidence.",
        ))
        return findings

    @staticmethod
    def _mean_scores(reports: list[AnalyticsReport]) -> dict[str, float]:
        values: dict[str, list[float]] = {}
        for report in reports:
            for name, score in report.performance_scores.items():
                if name == "overall":
                    continue
                values.setdefault(name, []).append(score.score)
        return {name: mean(scores) for name, scores in values.items() if scores}

    @staticmethod
    def _score_confidence(reports: list[AnalyticsReport], key: str) -> float:
        available = sum(1 for report in reports if key in report.performance_scores)
        provider_conf = [report.performance_scores[key].confidence for report in reports if key in report.performance_scores]
        return min(1.0, (available / max(1, len(reports))) * (mean(provider_conf) if provider_conf else 0.0))

    @staticmethod
    def _stability(reports: list[AnalyticsReport]) -> float:
        values = [r.performance_scores["overall"].score for r in reports if "overall" in r.performance_scores]
        if len(values) < 2:
            return 0.5 if values else 0.0
        average = mean(values)
        deviation = mean(abs(value - average) for value in values)
        return max(0.0, min(1.0, 1.0 - deviation / 50.0))
