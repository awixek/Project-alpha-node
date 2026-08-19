"""Deterministic trend detection over normalized analytics."""
from __future__ import annotations
from agents.an13.models import AnalyticsConfig, NormalizedMetric, TrendAnalysis, TrendReport


class TrendDetector:
    def detect(self, metrics: list[NormalizedMetric], config: AnalyticsConfig) -> TrendAnalysis:
        ordered = sorted(metrics, key=lambda m: m.captured_at)
        reports: list[TrendReport] = []
        if len(ordered) >= 2:
            midpoint = len(ordered) // 2
            first, second = ordered[:midpoint], ordered[midpoint:]
            first_views = sum(m.views for m in first) / max(1, len(first))
            second_views = sum(m.views for m in second) / max(1, len(second))
            delta = (second_views - first_views) / max(1.0, first_views)
            direction = "growing" if delta > 0.05 else "declining" if delta < -0.05 else "stable"
            reports.append(TrendReport(
                name="views", direction=direction, strength=min(1.0, abs(delta)),
                confidence=min(1.0, len(ordered) / max(1, config.minimum_sample_size)),
                evidence=[f"first_window_avg_views={first_views:.2f}", f"second_window_avg_views={second_views:.2f}"],
                explanation=f"Average views changed by {delta*100:.2f}% between the two available windows.",
            ))
        else:
            reports.append(TrendReport(
                name="sample", direction="insufficient_data", strength=0.0,
                confidence=0.2, evidence=[f"sample_size={len(ordered)}"],
                explanation="At least two observations are required for directional trend detection.",
            ))
        return TrendAnalysis(reports=reports, window_size=config.trend_detection_window, sample_size=len(metrics))
