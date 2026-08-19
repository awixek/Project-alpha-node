from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from agents.an05.models import CameraAngle, CameraMovement, ContinuityPackage, PromptPackage, ShotType, Storyboard, TransitionType, VisionScene, VisualStyle, VisionPlan
from agents.an06.asset_manager import AssetManager
from agents.an06.continuity import ContinuityValidator
from agents.an06.models import AssetPackage, GenerationRequest, ProviderAssetResponse, VisionCreatorConfig, VisionCreatorRequest
from agents.an06.provider import VisionGenerationProvider, VisionProviderRouter
from agents.an06.quality import AssetQualityValidator
from agents.an06.vision_creator import VisionCreator
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import AgentResult, AssetType, ExecutionStatus

class FakeProvider(VisionGenerationProvider):
    def __init__(self,name="test",fail=False): self._name=name; self.fail=fail; self.calls=0
    @property
    def name(self): return self._name
    def call(self,request):
        self.calls+=1
        if self.fail: raise RuntimeError("provider failure")
        return ProviderAssetResponse(storage_path=f"memory://{self._name}/{request.scene_id}.png",provider=self._name,asset_type=AssetType.IMAGE,width_px=1920,height_px=1080,checksum=f"c-{request.scene_id}",content_bytes=b"data",mime_type="image/png")

def make_plan(n=2):
    mid=uuid4(); scenes=[]
    for i in range(1,n+1):
        scenes.append(VisionScene(order=i-1,script_section_order=i-1,prompt=f"p{i}",duration_seconds=8,scene_number=i,narrative_goal=f"g{i}",visual_goal=f"v{i}",camera_type=ShotType.MEDIUM,camera_angle=CameraAngle.EYE_LEVEL,camera_movement=CameraMovement.STATIC,subject="subject",characters=["A"] if i>1 else [],character_description="verified",character_emotion="focused",character_pose="natural",costume_description="verified",environment="verified",historical_accuracy_notes=["no invention"],architecture_style="verified",lighting="consistent",time_of_day="unspecified",weather="unspecified",mood="focused",color_palette="natural",composition="balanced",depth="layered",lens_suggestion="35mm",animation_suggestion="subtle",transition_type=TransitionType.CUT,sound_suggestion="ambient",music_mood="focused",image_prompt=f"image {i}",negative_prompt="no unsupported facts",video_prompt=f"video {i}",continuity_notes=["preserve"] ,confidence_score=.9))
    return VisionPlan(mission_id=mid,shots=[s.model_copy() for s in scenes],storyboard=Storyboard(sequence=list(range(1,n+1)),timing=[8.0]*n,transitions=[TransitionType.CUT]*n,pacing="steady",emotional_rhythm=["focused"]*n),scenes=scenes,prompt_package=PromptPackage(image_prompts={i:f"image {i}" for i in range(1,n+1)},video_prompts={i:f"video {i}" for i in range(1,n+1)},negative_prompts={i:"no unsupported facts" for i in range(1,n+1)},style=VisualStyle.CINEMATIC,language="en"),continuity_package=ContinuityPackage(),estimated_runtime_seconds=8*n,overall_confidence=.9)

def creator_with(*providers):
    cfg=VisionCreatorConfig(max_retries=0)
    router=VisionProviderRouter(config=cfg)
    for p,priority in providers: router.register(p,priority=priority)
    return VisionCreator(provider_router=router,config=cfg),router

def test_successful_generation_and_manifest():
    plan=make_plan(); provider=FakeProvider(); creator,_=creator_with((provider,1))
    package=creator.execute(VisionCreatorRequest(mission_id=plan.mission_id,vision_plan=plan))
    assert isinstance(package,AssetPackage); assert len(package.assets)==2; assert len(package.asset_manifest.items)==2; assert package.quality_report.passed

def test_provider_fallback():
    plan=make_plan(1); bad=FakeProvider("bad",True); good=FakeProvider("good"); creator,_=creator_with((bad,1),(good,2))
    package=creator.execute(VisionCreatorRequest(mission_id=plan.mission_id,vision_plan=plan))
    assert package.assets[0].provider=="good"; assert bad.calls>=1; assert good.calls==1

def test_retry_uses_shared_router():
    class Flaky(FakeProvider):
        def call(self,r):
            self.calls+=1
            if self.calls==1: raise RuntimeError("temporary")
            return ProviderAssetResponse(storage_path="memory://ok",provider=self.name,asset_type=AssetType.IMAGE,width_px=1920,height_px=1080,checksum="x")
    plan=make_plan(1); p=Flaky(); cfg=VisionCreatorConfig(max_retries=2); router=VisionProviderRouter(config=cfg); router.register(p); package=VisionCreator(provider_router=router,config=cfg).execute(VisionCreatorRequest(mission_id=plan.mission_id,vision_plan=plan))
    assert len(package.assets)==1 and p.calls==2

def test_quality_and_duplicate_detection():
    plan=make_plan(1); provider=FakeProvider(); creator,_=creator_with((provider,1)); package=creator.execute(VisionCreatorRequest(mission_id=plan.mission_id,vision_plan=plan)); low=package.assets[0].model_copy(update={"width_px":320,"height_px":180}); report=AssetQualityValidator().validate([low],VisionCreatorConfig(minimum_quality_score=90)); assert not report.passed
    manager=AssetManager(); manager.add(package.assets[0]); manager.add(package.assets[0].model_copy(update={"asset_id":uuid4()})); assert any("duplicate" in x for x in manager.optimize("standard").findings)

def test_continuity_missing_scene():
    plan=make_plan(2); provider=FakeProvider(); creator,_=creator_with((provider,1)); package=creator.execute(VisionCreatorRequest(mission_id=plan.mission_id,vision_plan=plan)); report=ContinuityValidator().validate(plan,package.assets[:1]); assert not report.passed and any("Scene 2" in x for x in report.findings)

def test_configuration_override():
    plan=make_plan(1); captured=[]
    class Capture(FakeProvider):
        def call(self,r): captured.append(r); return super().call(r)
    creator,_=creator_with((Capture(),1)); creator.execute(VisionCreatorRequest(mission_id=plan.mission_id,vision_plan=plan,runtime_overrides={"image_resolution":"1024x1024","aspect_ratio":"1:1","output_format":"webp"})); assert captured[0].resolution=="1024x1024" and captured[0].aspect_ratio=="1:1" and captured[0].output_format=="webp"

def test_invalid_mission_rejected():
    plan=make_plan(1); creator,_=creator_with((FakeProvider(),1))
    with pytest.raises(Exception): creator.execute(VisionCreatorRequest(mission_id=uuid4(),vision_plan=plan))

def test_provider_interface_is_vendor_independent():
    assert issubclass(FakeProvider,VisionGenerationProvider); assert VisionGenerationProvider.__module__=="agents.an06.provider"

def test_an17_handler_compatibility():
    plan=make_plan(1); creator,_=creator_with((FakeProvider(),1)); handler=creator.as_agent_handler(); result=handler(AgentExecutionContext(mission_id=plan.mission_id,agent_id=AgentID.VISION_CREATOR,stage=WorkflowStage.IMAGE_GENERATION,dependency_results={"an05":AgentResult(agent_id=AgentID.VISION_PLANNER,mission_id=plan.mission_id,status=ExecutionStatus.SUCCESS,payload=plan,started_at=datetime.now(timezone.utc),completed_at=datetime.now(timezone.utc))})); assert result.agent_id==AgentID.VISION_CREATOR and result.status==ExecutionStatus.SUCCESS and isinstance(result.payload,AssetPackage)

def test_invalid_provider_empty_payload_is_structured_partial_result():
    class EmptyProvider(FakeProvider):
        def call(self, request):
            self.calls += 1
            return ProviderAssetResponse(
                storage_path="memory://empty",
                provider=self.name,
                asset_type=AssetType.IMAGE,
                width_px=1920,
                height_px=1080,
                content_bytes=b"",
            )
    plan = make_plan(1)
    creator, _ = creator_with((EmptyProvider(), 1))
    package = creator.execute(VisionCreatorRequest(mission_id=plan.mission_id, vision_plan=plan))
    assert package.generation_metrics.scenes_failed == 1
    assert package.assets == []
    assert not package.quality_report.passed


def test_optional_video_asset_generation_uses_same_provider_contract():
    plan = make_plan(1)
    provider = FakeProvider()
    cfg = VisionCreatorConfig(max_retries=0, generate_video_assets=True)
    router = VisionProviderRouter(config=cfg)
    router.register(provider)
    package = VisionCreator(provider_router=router, config=cfg).execute(
        VisionCreatorRequest(mission_id=plan.mission_id, vision_plan=plan)
    )
    assert package.generation_metrics.assets_generated == 2
    assert provider.calls == 2
