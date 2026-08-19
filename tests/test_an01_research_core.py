from __future__ import annotations

from datetime import datetime, timedelta, timezone

from shared.api_router import APIRouter
from shared.constants import AgentID
from shared.retry import RetryPolicy

from an01 import (
    ProviderSearchItem,
    ProviderSearchResponse,
    ResearchAnalysisConfig,
    ResearchCoordinator,
    ResearchCore,
    ResearchProvider,
    ResearchProviderRegistry,
    ResearchRequest,
)


NO_WAIT = RetryPolicy(max_attempts=1, delay_seconds=0, backoff_multiplier=1, timeout_seconds=5)


class FakeProvider(ResearchProvider):
    def __init__(self, name: str, items: tuple[ProviderSearchItem, ...], fail: bool = False):
        self._name = name
        self._items = items
        self.fail = fail
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def call(self, request):
        self.calls += 1
        if self.fail:
            raise ConnectionError("provider unavailable")
        return ProviderSearchResponse(provider=self.name, items=self._items)


def _item(title: str, url: str, provider: str, *, published_at=None):
    return ProviderSearchItem(
        title=title,
        summary="A useful research summary about the topic.",
        url=url,
        publisher=f"Publisher-{provider}",
        published_at=published_at,
        reliability="primary",
        keywords=("research", "topic"),
        provider=provider,
    )


def test_research_core_deduplicates_clusters_and_ranks():
    now = datetime.now(timezone.utc)
    first = FakeProvider("provider-a", (_item("Ancient astronomy discovery", "https://a/1", "provider-a", published_at=now),))
    second = FakeProvider("provider-b", (_item("Ancient astronomy discovery", "https://b/1", "provider-b", published_at=now),))
    registry = ResearchProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    registry.register(first, priority=1)
    registry.register(second, priority=2)
    coordinator = ResearchCoordinator(providers=registry)

    result = coordinator.run(ResearchRequest(mission_id=__import__("uuid").uuid4(), keywords=["ancient", "astronomy"]))

    assert len(result.candidates) == 1
    assert result.candidates[0].cross_source_confirmation > 0
    assert len(result.candidates[0].sources) == 2
    assert result.candidates[0].score_breakdown


def test_provider_failure_degrades_gracefully():
    working = FakeProvider("working", (_item("Research topic", "https://working/1", "working"),))
    failing = FakeProvider("failing", (), fail=True)
    registry = ResearchProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    registry.register(failing, priority=1)
    registry.register(working, priority=2)

    result = ResearchCoordinator(providers=registry).run(
        ResearchRequest(mission_id=__import__("uuid").uuid4(), keywords=["research", "topic"])
    )
    assert "working" in result.providers_succeeded
    assert "failing" in result.provider_failures
    assert result.candidates


def test_an17_handler_contract_uses_research_core_agent_id():
    provider = FakeProvider("provider", (_item("Topic", "https://example/1", "provider"),))
    registry = ResearchProviderRegistry(router=APIRouter(retry_policy=NO_WAIT))
    registry.register(provider)
    core = ResearchCore(coordinator=ResearchCoordinator(providers=registry))

    from shared.schemas import Mission, Topic
    from an17.dispatcher import AgentExecutionContext
    from shared.constants import WorkflowStage

    mission = Mission(topic=Topic(title="Topic"), requested_by="test")
    handler = core.as_agent_handler(
        mission_resolver=lambda mission_id: mission,
        request_builder=lambda mission, context: ResearchRequest(
            mission_id=mission.mission_id,
            keywords=["topic"],
        ),
    )
    result = handler(
        AgentExecutionContext(
            mission_id=mission.mission_id,
            agent_id=AgentID.RESEARCH_CORE,
            stage=WorkflowStage.RESEARCH,
            dependency_results={},
        )
    )
    assert result.agent_id is AgentID.RESEARCH_CORE
    assert result.payload is not None
