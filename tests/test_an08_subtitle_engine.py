from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from agents.an03.models import ScriptDocument, ScriptMetadata, ScriptOutline, ScriptSection, ScriptStyle, CitationMode, SectionType
from agents.an07.models import VoicePackage, VoiceSegment, VoiceMetadata, SynchronizationMetadata, VoiceQualityReport, GenerationMetrics
from agents.an08.models import SubtitleRequest, SubtitlePackage
from agents.an08.subtitle_builder import SubtitleBuilder
from agents.an08.subtitle_engine import SubtitleEngine, SubtitleEngineConfig
from agents.an08.formatter import SubtitleFormatter
from agents.an08.quality import SubtitleQualityValidator
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import AgentResult, ExecutionStatus

def make_voice(n=2):
    mid=uuid4()
    segs=[]
    for i in range(n):
        text="This is a readable sentence for synchronization. It preserves punctuation."
        start=i*3.0
        segs.append(VoiceSegment(segment_id=f"v{i}",section_id=f"s{i}",sequence=i,text=text,processed_text=text,start_time=start,estimated_end_time=start+3,duration=3,narrator="Narrator",language="en",emotion="neutral",speech_rate=1.0))
    return VoicePackage(
        mission_id=mid,narration_segments=segs,
        metadata=VoiceMetadata(language="en",voice="default",style="documentary",total_duration=n*3,segment_count=n,word_count=20*n),
        synchronization=SynchronizationMetadata(segments=segs,total_duration=n*3),
        quality_report=VoiceQualityReport(passed=True,score=100),
        generation_metrics=GenerationMetrics(segments_requested=n,segments_generated=n)
    )

def test_successful_subtitle_generation():
    v=make_voice()
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v,formats=["srt","vtt","json"]))
    assert isinstance(p,SubtitlePackage)
    assert p.subtitle_tracks and p.synchronization_metadata.total_segments > 0
    assert "srt" in p.exported_formats and p.exported_formats["srt"].startswith("1\n")

def test_sentence_and_character_segmentation():
    chunks=SubtitleBuilder(max_chars=20,max_lines=2).segment_text("One short sentence. Another sentence follows here.")
    assert len(chunks)>=2
    assert all(len(c.text)<=40 for c in chunks)

def test_precise_monotonic_synchronization():
    v=make_voice(1)
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v))
    ss=p.subtitle_tracks[0].segments
    assert ss[0].start_time >= 0
    assert all(a.end_time <= b.start_time+0.001 for a,b in zip(ss,ss[1:]))

def test_multilingual_and_bilingual_tracks():
    v=make_voice(1)
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v,language="hi",bilingual_mode=True,translated_text={"hi":"यह एक वाक्य है।"}))
    assert {t.language for t in p.subtitle_tracks}=={"hi"}

def test_translation_track_with_original_language_and_second_language():
    v=make_voice(1)
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v,bilingual_mode=True,translated_text={"fr":"Ceci est une phrase."}))
    assert {t.language for t in p.subtitle_tracks}=={"en","fr"}
    assert p.subtitle_tracks[1].segments[0].text.startswith("Ceci")

def test_all_export_formats():
    v=make_voice(1)
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v,formats=["srt","vtt","ass","ttml","json"]))
    assert set(p.exported_formats)=={"srt","vtt","ass","ttml","json"}
    assert p.exported_formats["vtt"].startswith("WEBVTT")
    assert "<tt" in p.exported_formats["ttml"]

def test_reading_speed_validation():
    v=make_voice(1)
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v,reading_speed=2))
    assert not p.quality_report.passed
    assert p.synchronization_metadata.reading_speed_violations > 0

def test_duplicate_detection():
    v=make_voice(1)
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v))
    seg=p.subtitle_tracks[0].segments[0]
    report=SubtitleQualityValidator().validate([seg,seg.model_copy(update={"subtitle_id":"dup","start_time":4,"end_time":5})],max_chars=42,max_lines=2,reading_speed=17,tolerance=.1)
    assert any("Duplicate" in x for x in report.findings)

def test_invalid_timing_detection():
    v=make_voice(1)
    p=SubtitleEngine().execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v))
    seg=p.subtitle_tracks[0].segments[0].model_copy(update={"start_time":2.0,"end_time":1.0,"duration":-1.0})
    # model validation prevents impossible duration; validate a valid but zero-length interval instead
    seg=p.subtitle_tracks[0].segments[0].model_copy(update={"start_time":2.0,"end_time":2.0,"duration":0.001})
    report=SubtitleQualityValidator().validate([seg],max_chars=42,max_lines=2,reading_speed=17,tolerance=.1)
    assert not report.passed and report.metrics.invalid_timings==1

def test_configuration_overrides():
    v=make_voice(1)
    e=SubtitleEngine(config=SubtitleEngineConfig(preferred_format="vtt",max_characters_per_line=50))
    p=e.execute(SubtitleRequest(mission_id=v.mission_id,voice_package=v,runtime_overrides={"preferred_format":"json","max_characters_per_line":30}))
    assert "json" in p.exported_formats
    assert p.formatting_metadata.font_size==42

def test_invalid_input_rejected():
    v=make_voice(1)
    with pytest.raises(Exception):
        SubtitleEngine().execute(SubtitleRequest(mission_id=uuid4(),voice_package=v))

def test_an17_compatibility():
    v=make_voice(1)
    e=SubtitleEngine()
    result=e.as_agent_handler()(AgentExecutionContext(mission_id=v.mission_id,agent_id=AgentID.SUBTITLE_ENGINE,stage=WorkflowStage.SUBTITLE,dependency_results={
        "an07": AgentResult(agent_id=AgentID.VOICE_CORE,mission_id=v.mission_id,status=ExecutionStatus.SUCCESS,payload=v,started_at=datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc))
    }))
    assert result.status==ExecutionStatus.SUCCESS
    assert isinstance(result.payload,SubtitlePackage)
