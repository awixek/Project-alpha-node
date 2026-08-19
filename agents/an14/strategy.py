"""Strategy and optional-memory extension points for AN-14."""
from __future__ import annotations

from typing import Protocol, Sequence

from agents.an13.models import AnalyticsReport
from .models import EvolutionReport


class EvolutionHistoryProvider(Protocol):
    """Optional future-memory boundary; AN-14 does not require an implementation."""

    def load_analytics(self, mission_id: object, limit: int) -> Sequence[AnalyticsReport]:
        """Return historical analytics summaries for a mission or campaign."""
        ...

    def store_evolution_report(self, report: EvolutionReport) -> None:
        """Persist an evolution report for future retrieval."""
        ...


class StrategyPolicy(Protocol):
    """Replaceable policy boundary for future learning strategies."""

    def select_reports(self, current: AnalyticsReport, historical: Sequence[AnalyticsReport], limit: int) -> list[AnalyticsReport]:
        """Select the reports used for the current optimization window."""
        ...


class WindowStrategy:
    """Default deterministic strategy: newest reports within the configured window."""

    def select_reports(self, current: AnalyticsReport, historical: Sequence[AnalyticsReport], limit: int) -> list[AnalyticsReport]:
        reports = list(historical)
        if current not in reports:
            reports.append(current)
        reports.sort(key=lambda report: report.generated_at)
        return reports[-limit:]
