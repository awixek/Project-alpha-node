from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from agents.an05.models import VisionPlan, VisionScene, Storyboard, PromptPackage, ContinuityPackage, VisualStyle, ShotType, CameraAngle, CameraMovement, TransitionType
from agents.an06.models import AssetPackage, AssetManifest, GenerationMetrics as AssetGenerationMetrics, GeneratedAsset, GenerationKind, GenerationStatus, QualityStatus, QualityReport, ContinuityReport, OptimizationReport
from agents.an07.models import VoicePackage, VoiceSegment, VoiceMetadata, SynchronizationMetadata, VoiceQualityReport, GenerationMetrics
from agents.an08.models import SubtitlePackage, SubtitleTrack, SubtitleSegment, SubtitleMetadata, SynchronizationMetrics, SubtitleQualityReport
from agents.an09.models import VideoRequest, RenderSettings, RenderStatus, VideoPackage
from agents.an09.video_forge import VideoForge
from agents.an09.renderer import VideoRenderProvider, VideoRenderRouter
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import AgentResult, ExecutionStatus


def make_inputs(scene_count=2):
    mid = uuid4()
    scenes=[]
    for i in range(1, scene_count+1):
        scenes.append(VisionScene(
            order=i-1, script_section_order=i-1, prompt=f"Verified scene {i}", duration_seconds=3,
            scene_number=i, narrative_goal=f"Goal {i}", visual_goal=f"Visual {i}", camera_type=ShotType.WIDE,
            camera_angle=CameraAngle.EYE_LEVEL, camera_movement=CameraMovement.STATIC, subject=f"Subject {i}",
            environment="documented setting", lighting="soft", time_of_day="day", weather="clear", mood="calm",
            color_palette="natural", composition="balanced", depth="layered", lens_suggestion="50mm",
            animation_suggestion="slow push", transition_type=TransitionType.CUT, sound_suggestion="ambient",
            music_mood="documentary", image_prompt=f"Verified image {i}", negative_prompt="no invented details",
            video_prompt=f"Verified video {i}", confidence_score=1.0
        ))
    vision=VisionPlan(mission_id=mid, shots=[], scenes=scenes,
        storyboard=Storyboard(sequence=list(range(1,scene_count+1)),timing=[3.0]*scene_count,transitions=[TransitionType.CUT]*scene_count,pacing="steady",emotional_rhythm=["calm"]*scene_count),
        prompt_package=PromptPackage(image_prompts={i:f"p{i}" for i in range(1,scene_count+1)},video_prompts={},negative_prompts={},style=VisualStyle.CINEMATIC,language="en"),
        continuity_package=ContinuityPackage(), estimated_runtime_seconds=3*scene_count, overall_confidence=1.0)
    assets=[]
    for i in range(1,scene_count+1):
        assets.append(GeneratedAsset(mission_id=mid,scene_id=i,generation_kind=GenerationKind.IMAGE,generation_status=GenerationStatus.GENERATED,storage_path=f"asset://scene-{i}",provider="mock",asset_type="image",quality_status=QualityStatus.PASSED))
    asset_package=AssetPackage(mission_id=mid,assets=assets,asset_manifest=AssetManifest(),generation_metrics=AssetGenerationMetrics(scenes_requested=scene_count,scenes_completed=scene_count,assets_generated=scene_count),provider_statistics=[],quality_report=QualityReport(passed=True,score=100),continuity_report=ContinuityReport(passed=True),optimization_report=OptimizationReport(applied=False))
    vseg=[]
    for i in range(scene_count):
        vseg.append(VoiceSegment(segment_id=f"v{i}",section_id=f"scene-{i+1}",sequence=i,text="A verified narration sentence.",processed_text="A verified narration sentence.",start_time=i*3,estimated_end_time=(i+1)*3,duration=3,narrator="Narrator",language="en",emotion="neutral",speech_rate=1.0))
    voice=VoicePackage(mission_id=mid,narration_segments=vseg,metadata=VoiceMetadata(language="en",voice="default",style="documentary",total_duration=3*scene_count,segment_count=scene_count,word_count=scene_count*4),synchronization=SynchronizationMetadata(segments=vseg,total_duration=3*scene_count),quality_report=VoiceQualityReport(passed=True,score=100),generation_metrics=GenerationMetrics(segments_requested=scene_count,segments_generated=scene_count))
    ssegs=[]
    for i in range(scene_count):
        ssegs.append(SubtitleSegment(scene_id=str(i+1),sequence=i,start_time=i*3,end_time=(i+1)*3,duration=3,language="en",speaker="Narrator",text="A verified narration sentence."))
    track=SubtitleTrack(language="en",label="English",segments=ssegs,format="srt")
    subtitles=SubtitlePackage(mission_id=mid,subtitle_tracks=[track],synchronization_metadata=SynchronizationMetrics(total_segments=scene_count,average_score=1),exported_formats={"srt":"1\n00:00:00,000 --> 00:00:03,000\nA verified narration sentence."},quality_report=SubtitleQualityReport(passed=True,score=100,metrics=SynchronizationMetrics(total_segments=scene_count,average_score=1)),metadata=SubtitleMetadata(mission_id=mid,language="en",track_count=1,segment_count=scene_count,total_duration=3*scene_count,formats=["srt"]))
    return mid, vision, asset_package, voice, subtitles


class MockRenderProvider(VideoRenderProvider):
    def __init__(self, name="mock", fail=False): self._name=name; self.fail=fail; self.calls=0
    @property
    def name(self): return self._name
    def call(self, request):
        self.calls += 1
        if self.fail: raise RuntimeError("temporary render failure")
        return __import__('agents.an09.models',fromlist=['VideoProviderResponse']).VideoProviderResponse(provider=self.name,video_uri="video://final",duration_seconds=request.timeline.total_runtime,format=request.render_settings.export_format,resolution=request.render_settings.resolution,fps=request.render_settings.fps,codec=request.render_settings.codec,bitrate=request.render_settings.bitrate,completed_scene_ids=[s.scene_id for s in request.timeline.scenes],rendered_frames=180)


def forge_with_provider():
    settings=RenderSettings(max_retries=1)
    router=VideoRenderRouter(settings=settings)
    provider=MockRenderProvider()
    router.register(provider,priority=0)
    return VideoForge(settings=settings,router=router),provider


def test_timeline_and_successful_render():
    mid,v,a,voice,subs=make_inputs()
    forge,p=forge_with_provider()
    package=forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))
    assert isinstance(package,VideoPackage)
    assert package.timeline.total_runtime==6
    assert package.video_uri=="video://final"
    assert package.render_job.status is RenderStatus.COMPLETED
    assert p.calls==1


def test_asset_synchronization():
    mid,v,a,voice,subs=make_inputs()
    forge,_=forge_with_provider(); package=forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))
    assert all(scene.asset_ids for scene in package.timeline.scenes)
    assert package.timeline.scenes[0].narration_segment_ids


def test_subtitle_and_narration_sync():
    mid,v,a,voice,subs=make_inputs()
    forge,_=forge_with_provider(); package=forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))
    assert package.synchronization_report.passed
    assert package.synchronization_report.narration_drift_seconds == 0


def test_transition_engine_is_provider_neutral():
    mid,v,a,voice,subs=make_inputs()
    forge,_=forge_with_provider(); package=forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))
    assert package.timeline.scenes[1].transition_in == "cut"


def test_export_configuration_override():
    mid,v,a,voice,subs=make_inputs()
    forge,_=forge_with_provider(); package=forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs,runtime_overrides={"export_format":"webm","fps":60}))
    assert package.export_metadata.format == "webm"
    assert package.export_metadata.fps == 60


def test_provider_fallback():
    mid,v,a,voice,subs=make_inputs()
    settings=RenderSettings(max_retries=0)
    router=VideoRenderRouter(settings=settings)
    bad=MockRenderProvider("bad",True); good=MockRenderProvider("good")
    router.register(bad,priority=0); router.register(good,priority=1)
    package=VideoForge(settings=settings,router=router).execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))
    assert package.export_metadata.uri == "video://final" and good.calls==1


def test_invalid_assets_are_rejected():
    mid,v,a,voice,subs=make_inputs()
    a=a.model_copy(update={"assets":[]})
    forge,_=forge_with_provider()
    with pytest.raises(Exception): forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))


def test_invalid_mission_is_structured_failure_via_handler():
    mid,v,a,voice,subs=make_inputs()
    forge,_=forge_with_provider()
    context=AgentExecutionContext(mission_id=uuid4(),agent_id=AgentID.VIDEO_FORGE,stage=WorkflowStage.VIDEO_EDITING,dependency_results={})
    result=forge.as_agent_handler()(context)
    assert result.status is ExecutionStatus.FAILED and result.error is not None


def test_an17_compatibility():
    mid,v,a,voice,subs=make_inputs()
    forge,_=forge_with_provider()
    now=datetime.now(timezone.utc)
    deps={
      AgentID.VISION_PLANNER.value:AgentResult(agent_id=AgentID.VISION_PLANNER,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=v,started_at=now,completed_at=now),
      AgentID.VISION_CREATOR.value:AgentResult(agent_id=AgentID.VISION_CREATOR,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=a,started_at=now,completed_at=now),
      AgentID.VOICE_CORE.value:AgentResult(agent_id=AgentID.VOICE_CORE,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=voice,started_at=now,completed_at=now),
      AgentID.SUBTITLE_ENGINE.value:AgentResult(agent_id=AgentID.SUBTITLE_ENGINE,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=subs,started_at=now,completed_at=now),
    }
    context=AgentExecutionContext(mission_id=mid,agent_id=AgentID.VIDEO_FORGE,stage=WorkflowStage.VIDEO_EDITING,dependency_results=deps)
    result=forge.as_agent_handler()(context)
    assert result.status is ExecutionStatus.SUCCESS and result.agent_id is AgentID.VIDEO_FORGE


def test_provider_failure_is_structured():
    mid,v,a,voice,subs=make_inputs()
    settings=RenderSettings(max_retries=0)
    router=VideoRenderRouter(settings=settings); router.register(MockRenderProvider("bad",True),priority=0)
    forge=VideoForge(settings=settings,router=router)
    now=datetime.now(timezone.utc)
    deps={AgentID.VISION_PLANNER.value:AgentResult(agent_id=AgentID.VISION_PLANNER,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=v,started_at=now,completed_at=now),AgentID.VISION_CREATOR.value:AgentResult(agent_id=AgentID.VISION_CREATOR,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=a,started_at=now,completed_at=now),AgentID.VOICE_CORE.value:AgentResult(agent_id=AgentID.VOICE_CORE,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=voice,started_at=now,completed_at=now),AgentID.SUBTITLE_ENGINE.value:AgentResult(agent_id=AgentID.SUBTITLE_ENGINE,mission_id=mid,status=ExecutionStatus.SUCCESS,payload=subs,started_at=now,completed_at=now)}
    result=forge.as_agent_handler()(AgentExecutionContext(mission_id=mid,agent_id=AgentID.VIDEO_FORGE,stage=WorkflowStage.VIDEO_EDITING,dependency_results=deps))
    assert result.status is ExecutionStatus.FAILED and result.error is not None


def test_duplicate_scene_quality_detection():
    mid,v,a,voice,subs=make_inputs(2)
    duplicate=v.scenes[1].model_copy(update={"scene_number":1})
    v=v.model_copy(update={"scenes":[v.scenes[0],duplicate]})
    forge,_=forge_with_provider()
    package=forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))
    assert package.quality_report.duplicate_clips == 1
    assert not package.quality_report.passed


def test_render_settings_are_configurable():
    settings=RenderSettings(resolution="1080x1920",orientation="portrait",transition_style="fade",animation_intensity=.9)
    assert settings.orientation.value == "portrait"
    assert settings.transition_style.value == "fade"
    assert settings.animation_intensity == .9


def test_video_package_contains_downstream_metadata():
    mid,v,a,voice,subs=make_inputs()
    forge,_=forge_with_provider(); package=forge.execute(VideoRequest(mission_id=mid,vision_plan=v,asset_package=a,voice_package=voice,subtitle_package=subs))
    assert package.timeline and package.render_metrics and package.export_metadata
    assert package.asset_usage_report["asset_count"] == 2
