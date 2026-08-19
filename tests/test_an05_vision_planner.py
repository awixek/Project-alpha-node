from __future__ import annotations

from uuid import uuid4

import pytest

from agents.an03.models import ScriptDocument, ScriptMetadata, ScriptOutline, ScriptSection, ScriptStyle, SectionType, CitationMode
from agents.an05.models import VisionPlanningConfig, VisionPlanningRequest, VisualStyle
from agents.an05.vision_planner import VisionPlanner
from agents.an05.coordinator import VisionPlannerCoordinator
from agents.an17.dispatcher import AgentExecutionContext
from shared.constants import AgentID, WorkflowStage
from shared.schemas import AgentResult, ExecutionStatus


def make_script():
    mission_id = uuid4()
    section = ScriptSection(
        order=0,
        heading="Historical Context",
        narration="A verified account explains the historical development of the subject.",
        visual_notes="environment: documented historical setting; character: researcher",
        section_type=SectionType.HISTORICAL_CONTEXT,
        estimated_duration_seconds=8,
    )
    return ScriptDocument(
        mission_id=mission_id,
        title="Verified History",
        sections=[section],
        tone="educational",
        outline=ScriptOutline(
            title="Verified History",
            thesis="A verified historical account.",
            sections=[SectionType.HISTORICAL_CONTEXT],
            style=ScriptStyle.EDUCATIONAL,
        ),
        metadata=ScriptMetadata(
            style=ScriptStyle.EDUCATIONAL,
            language="en",
            tone="educational",
            target_duration_seconds=8,
            estimated_duration_seconds=8,
            word_count=12,
            citation_mode=CitationMode.INLINE,
        ),
    )


def make_planner():
    return VisionPlanner(coordinator=VisionPlannerCoordinator())


def test_script_parsing_and_scene_generation():
    script = make_script()
    result = make_planner().execute(VisionPlanningRequest(mission_id=script.mission_id, script=script))
    assert result.status is ExecutionStatus.SUCCESS
    assert result.payload is not None
    assert len(result.payload.scenes) == 1
    assert result.payload.scenes[0].script_section_order == 0
    assert result.payload.scenes[0].image_prompt


def test_storyboard_generation_and_timing():
    script = make_script()
    result = make_planner().execute(VisionPlanningRequest(mission_id=script.mission_id, script=script))
    plan = result.payload
    assert plan is not None
    assert plan.storyboard.sequence == [1]
    assert plan.storyboard.timing == [8.0]
    assert plan.estimated_runtime_seconds == 8.0


def test_prompt_package_is_provider_independent():
    script = make_script()
    result = make_planner().execute(VisionPlanningRequest(mission_id=script.mission_id, script=script))
    scene = result.payload.scenes[0]
    assert "provider" not in scene.image_prompt.casefold()
    assert "api" not in scene.image_prompt.casefold()
    assert scene.video_prompt
    assert scene.negative_prompt


def test_continuity_preserves_explicit_character():
    script = make_script()
    result = make_planner().execute(VisionPlanningRequest(mission_id=script.mission_id, script=script))
    continuity = result.payload.continuity_package
    assert len(continuity.characters) == 1
    assert continuity.characters[0].character_key == "researcher"
    assert continuity.characters[0].scenes == [1]


def test_duplicate_scene_detection():
    script = make_script()
    second = script.sections[0].model_copy(update={"order": 1})
    script = script.model_copy(update={"sections": [script.sections[0], second]})
    result = make_planner().execute(VisionPlanningRequest(mission_id=script.mission_id, script=script))
    assert any("duplicate scene" in issue.lower() for issue in result.payload.validation_issues)


def test_configuration_override_changes_style_and_duration():
    script = make_script()
    request = VisionPlanningRequest(
        mission_id=script.mission_id,
        script=script,
        runtime_overrides={"preferred_style": "documentary", "maximum_scene_duration_seconds": 5},
    )
    result = make_planner().execute(request)
    assert result.payload.prompt_package.style is VisualStyle.DOCUMENTARY
    assert result.payload.scenes[0].duration_seconds == 5.0


def test_invalid_mission_id_returns_structured_failure():
    script = make_script()
    result = make_planner().execute(VisionPlanningRequest(mission_id=uuid4(), script=script))
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.agent_id is AgentID.VISION_PLANNER


def test_empty_script_is_rejected():
    script = make_script().model_copy(update={"sections": []})
    result = make_planner().execute(VisionPlanningRequest(mission_id=script.mission_id, script=script))
    assert result.status is ExecutionStatus.FAILED
    assert result.error is not None


def test_an17_handler_compatibility():
    script = make_script()
    planner = make_planner()
    script_result = AgentResult(
        agent_id=AgentID.SCRIPT_FORGE,
        mission_id=script.mission_id,
        status=ExecutionStatus.SUCCESS,
        payload=script,
        started_at=script.created_at,
        completed_at=script.created_at,
    )
    context = AgentExecutionContext(
        mission_id=script.mission_id,
        agent_id=AgentID.VISION_PLANNER,
        stage=WorkflowStage.VISUAL_PLANNING,
        dependency_results={"script": script_result},
    )
    result = planner.as_agent_handler()(context)
    assert result.status is ExecutionStatus.SUCCESS
    assert result.agent_id is AgentID.VISION_PLANNER
    assert result.payload is not None


def test_optional_seo_dependency_does_not_break_planning():
    script = make_script()
    seo = AgentResult(
        agent_id=AgentID.SEO_BRAIN,
        mission_id=script.mission_id,
        status=ExecutionStatus.SUCCESS,
        payload=ScriptMetadata(
            style=ScriptStyle.EDUCATIONAL,
            language="en",
            tone="educational",
            target_duration_seconds=8,
            estimated_duration_seconds=8,
            word_count=12,
            citation_mode=CitationMode.INLINE,
        ),
        started_at=script.created_at,
        completed_at=script.created_at,
    )
    script_result = AgentResult(
        agent_id=AgentID.SCRIPT_FORGE,
        mission_id=script.mission_id,
        status=ExecutionStatus.SUCCESS,
        payload=script,
        started_at=script.created_at,
        completed_at=script.created_at,
    )
    context = AgentExecutionContext(
        mission_id=script.mission_id,
        agent_id=AgentID.VISION_PLANNER,
        stage=WorkflowStage.VISUAL_PLANNING,
        dependency_results={"script": script_result, "seo": seo},
    )
    result = make_planner().as_agent_handler()(context)
    assert result.status is ExecutionStatus.SUCCESS


def test_provider_independence_has_no_provider_dependency():
    script = make_script()
    coordinator = VisionPlannerCoordinator()
    assert not hasattr(coordinator, "_provider")
    result = coordinator.run(VisionPlanningRequest(mission_id=script.mission_id, script=script))
    assert result.scenes
