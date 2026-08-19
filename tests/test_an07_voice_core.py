from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an03.models import ScriptDocument, ScriptMetadata, ScriptOutline, ScriptSection, ScriptStyle, CitationMode, SectionType
from agents.an07.models import VoiceCoreConfig, VoicePackage, VoiceProviderResponse, VoiceRequest
from agents.an07.provider import VoiceProvider, VoiceProviderRouter
from agents.an07.quality import VoiceQualityValidator
from agents.an07.voice_core import VoiceCore
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import AgentResult, ExecutionStatus


class FakeVoiceProvider(VoiceProvider):
    def __init__(self, name="test", fail=False, empty=False):
        self._name = name
        self.fail = fail
        self.empty = empty
        self.calls = 0

    @property
    def name(self):
        return self._name

    def call(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failure")
        return VoiceProviderResponse(
            provider=self.name,
            audio_uri=f"memory://{self.name}/{request.segment_id}.mp3",
            duration_seconds=max(1.0, len(request.text.split()) / 2.5),
            mime_type="audio/mpeg",
            content_bytes=None if self.empty else b"audio",
        )


def make_script(section_count=2):
    mission_id = uuid4()
    sections = [
        ScriptSection(
            order=i,
            heading=f"Section {i + 1}",
            narration=("Sanskrit history and भारत are important subjects. " if i == 0 else "The evidence is presented clearly."),
            estimated_duration_seconds=4.0,
            section_type=SectionType.INTRO,
        )
        for i in range(section_count)
    ]
    return ScriptDocument(
        mission_id=mission_id,
        title="Test Script",
        sections=sections,
        tone="documentary",
        outline=ScriptOutline(title="Test Script", thesis="A verified thesis.", sections=[SectionType.INTRO], style=ScriptStyle.DOCUMENTARY),
        metadata=ScriptMetadata(style=ScriptStyle.DOCUMENTARY, language="en", tone="documentary", target_duration_seconds=30, estimated_duration_seconds=8, word_count=20, citation_mode=CitationMode.INLINE),
    )


def make_core(*providers, config=None):
    cfg = config or VoiceCoreConfig(max_retries=0)
    router = VoiceProviderRouter(config=cfg)
    for provider, priority in providers:
        router.register(provider, priority=priority)
    return VoiceCore(provider_router=router, config=cfg)


def test_successful_narration_generation_and_sync_metadata():
    script = make_script()
    core = make_core((FakeVoiceProvider(), 1))
    package = core.execute(VoiceRequest(mission_id=script.mission_id, script=script))
    assert isinstance(package, VoicePackage)
    assert len(package.narration_segments) == 2
    assert package.synchronization.total_duration > 0
    assert package.quality_report.passed
    assert package.metadata.segment_count == 2


def test_provider_abstraction_and_fallback():
    script = make_script(1)
    bad = FakeVoiceProvider("bad", fail=True)
    good = FakeVoiceProvider("good")
    package = make_core((bad, 1), (good, 2)).execute(VoiceRequest(mission_id=script.mission_id, script=script))
    assert package.narration_segments[0].provider == "good"
    assert bad.calls >= 1 and good.calls == 1


def test_shared_router_retry_is_used():
    class Flaky(FakeVoiceProvider):
        def call(self, request):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary")
            return super().call(request)

    script = make_script(1)
    provider = Flaky()
    cfg = VoiceCoreConfig(max_retries=1)
    package = make_core((provider, 1), config=cfg).execute(VoiceRequest(mission_id=script.mission_id, script=script))
    assert package.generation_metrics.segments_generated == 1
    assert provider.calls >= 2


def test_pronunciation_dictionary_is_applied_and_recorded():
    script = make_script(1)
    provider = FakeVoiceProvider()
    package = make_core((provider, 1)).execute(
        VoiceRequest(mission_id=script.mission_id, script=script, pronunciation_dictionary={"Sanskrit": "SANS-krit", "भारत": "BHA-rat"})
    )
    segment = package.narration_segments[0]
    assert "SANS-krit" in segment.processed_text
    assert any(item.original == "Sanskrit" for item in package.pronunciation_metadata)


def test_multilingual_configuration_override():
    script = make_script(1)
    provider = FakeVoiceProvider()
    package = make_core((provider, 1)).execute(
        VoiceRequest(mission_id=script.mission_id, script=script, runtime_overrides={"language": "hi", "voice": "hindi-1", "speaking_rate": 0.9})
    )
    assert package.metadata.language == "hi"
    assert package.narration_segments[0].narrator == "hindi-1"
    assert package.narration_segments[0].speech_rate == 0.9


def test_invalid_input_is_rejected():
    script = make_script(1)
    core = make_core((FakeVoiceProvider(), 1))
    with pytest.raises(Exception):
        core.execute(VoiceRequest(mission_id=uuid4(), script=script))


def test_empty_script_sections_are_rejected():
    script = make_script(1).model_copy(update={"sections": []})
    core = make_core((FakeVoiceProvider(), 1))
    with pytest.raises(Exception):
        core.execute(VoiceRequest(mission_id=script.mission_id, script=script))


def test_invalid_provider_response_becomes_structured_partial_package():
    class Invalid(FakeVoiceProvider):
        def call(self, request):
            self.calls += 1
            return VoiceProviderResponse(provider=self.name, audio_uri=" ", duration_seconds=1.0)

    script = make_script(1)
    package = make_core((Invalid(), 1)).execute(VoiceRequest(mission_id=script.mission_id, script=script))
    assert package.generation_metrics.segments_failed == 1
    assert package.narration_segments == []
    assert not package.quality_report.passed


def test_quality_validator_detects_duplicate_and_timing_errors():
    script = make_script(1)
    package = make_core((FakeVoiceProvider(), 1)).execute(VoiceRequest(mission_id=script.mission_id, script=script))
    segment = package.narration_segments[0]
    duplicate = segment.model_copy(update={"segment_id": segment.segment_id, "start_time": 0.0, "estimated_end_time": 0.1})
    report = VoiceQualityValidator().validate([segment, duplicate], VoiceCoreConfig(minimum_quality_score=99))
    assert not report.passed
    assert any("Duplicate" in item for item in report.findings)


def test_an17_handler_compatibility():
    script = make_script(1)
    core = make_core((FakeVoiceProvider(), 1))
    result = core.as_agent_handler()(AgentExecutionContext(
        mission_id=script.mission_id,
        agent_id=AgentID.VOICE_CORE,
        stage=WorkflowStage.VOICE_GENERATION,
        dependency_results={"an03": AgentResult(
            agent_id=AgentID.SCRIPT_FORGE,
            mission_id=script.mission_id,
            status=ExecutionStatus.SUCCESS,
            payload=script,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )},
    ))
    assert result.agent_id == AgentID.VOICE_CORE
    assert result.status == ExecutionStatus.SUCCESS
    assert isinstance(result.payload, VoicePackage)


def test_provider_interface_has_no_vendor_dependency():
    assert issubclass(FakeVoiceProvider, VoiceProvider)
    assert VoiceProvider.__module__ == "agents.an07.provider"
