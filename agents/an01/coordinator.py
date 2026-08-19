"""Research coordination pipeline for AN-01."""
from __future__ import annotations

from datetime import timezone
from typing import Callable

from shared.constants import AgentID, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AlphaBaseException
from shared.logger import AlphaLogger, get_agent_logger

from .analysis import build_candidates, cluster_candidates, merge_duplicates
from .models import ProviderSearchRequest, ResearchAnalysisConfig, ResearchBatch, ResearchRequest
from .providers import ResearchProviderRegistry


class ResearchCoordinator:
    """Coordinates discovery, aggregation, deduplication, clustering and ranking."""

    def __init__(
        self,
        *,
        providers: ResearchProviderRegistry,
        config: ResearchAnalysisConfig | None = None,
        logger: AlphaLogger | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._providers = providers
        self._config = config or ResearchAnalysisConfig.from_shared_config()
        self._logger = logger or get_agent_logger(AgentID.RESEARCH_CORE)
        self._event_bus = event_bus or get_event_bus()

    def run(self, request: ResearchRequest) -> ResearchBatch:
        """Execute a complete research discovery pass with graceful degradation."""
        self._logger.info(
            "Research started.",
            category=LogCategory.AGENT,
            agent_id=AgentID.RESEARCH_CORE,
            mission_id=request.mission_id,
            metadata={"keyword_count": len(request.keywords)},
        )
        query = self._build_query(request)
        provider_request = ProviderSearchRequest(
            mission_id=request.mission_id,
            query=query,
            language=request.language,
            platform=request.platform,
            time_window_start=request.time_window_start,
            time_window_end=request.time_window_end,
            search_config=request.search_config,
            constraints=request.constraints,
        )

        if not self._providers.provider_names:
            raise AgentExecutionError(
                "Research Core has no registered providers.",
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                context={"operation": "research"},
            )

        responses = {}
        failures = {}
        for provider_name in self._providers.provider_names:
            self._logger.info(
                "Research provider request started.",
                category=LogCategory.API,
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                metadata={"provider": provider_name},
            )
            try:
                response = self._providers.search_provider(provider_name, provider_request)
                responses[provider_name] = response
            except Exception as exc:  # provider failure is isolated by design
                failures[provider_name] = str(exc)
                self._logger.warning(
                    "Research provider failed; continuing with remaining providers.",
                    category=LogCategory.API,
                    agent_id=AgentID.RESEARCH_CORE,
                    mission_id=request.mission_id,
                    metadata={"provider": provider_name, "error": str(exc)},
                )
        self._logger.info(
            "Research provider collection completed.",
            category=LogCategory.API,
            agent_id=AgentID.RESEARCH_CORE,
            mission_id=request.mission_id,
            metadata={
                "providers_attempted": len(self._providers.provider_names),
                "providers_succeeded": len(responses),
                "provider_failures": len(failures),
            },
        )

        items = [item for response in responses.values() for item in response.items]
        if request.time_window_start or request.time_window_end:
            filtered = []
            for item in items:
                if item.published_at is None:
                    filtered.append(item)
                    continue
                published_at = item.published_at
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                if request.time_window_start and published_at < request.time_window_start:
                    continue
                if request.time_window_end and published_at > request.time_window_end:
                    continue
                filtered.append(item)
            items = filtered
            self._logger.info(
                "Research time-window filtering completed.",
                category=LogCategory.QUALITY,
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                metadata={"remaining_results": len(items)},
            )
        self._logger.info(
            "Research results received.",
            category=LogCategory.AGENT,
            agent_id=AgentID.RESEARCH_CORE,
            mission_id=request.mission_id,
            metadata={"raw_result_count": len(items)},
        )
        groups, removed = merge_duplicates(items, self._config)
        self._logger.info(
            "Research duplicate removal completed.",
            category=LogCategory.QUALITY,
            agent_id=AgentID.RESEARCH_CORE,
            mission_id=request.mission_id,
            metadata={"duplicates_removed": removed, "unique_groups": len(groups)},
        )
        clusters = cluster_candidates(groups, self._config)
        self._logger.info(
            "Research clustering completed.",
            category=LogCategory.AGENT,
            agent_id=AgentID.RESEARCH_CORE,
            mission_id=request.mission_id,
            metadata={"cluster_count": len(clusters)},
        )
        candidates = build_candidates(
            groups,
            clusters=clusters,
            mission_id=request.mission_id,
            query=query,
            config=self._config,
        )
        self._logger.info(
            "Research ranking completed.",
            category=LogCategory.QUALITY,
            agent_id=AgentID.RESEARCH_CORE,
            mission_id=request.mission_id,
            metadata={"candidate_count": len(candidates)},
        )
        result = ResearchBatch(
            mission_id=request.mission_id,
            query=query,
            candidates=candidates,
            providers_attempted=list(self._providers.provider_names),
            providers_succeeded=sorted(responses),
            provider_failures=failures,
        )
        self._logger.info(
            "Research completed.",
            category=LogCategory.AGENT,
            agent_id=AgentID.RESEARCH_CORE,
            mission_id=request.mission_id,
            metadata={"candidate_count": len(candidates)},
        )
        return result

    @staticmethod
    def _build_query(request: ResearchRequest) -> str:
        parts = list(request.keywords)
        if not parts:
            raise AgentExecutionError(
                "Research request must contain at least one keyword.",
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=request.mission_id,
                context={"operation": "build_query"},
            )
        return " ".join(dict.fromkeys(part.strip() for part in parts if part.strip()))
