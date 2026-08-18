from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from agents.an_17 import (
    AlphaOrchestrator,
    DispatchTask,
    Dispatcher,
    RecoveryDisposition,
    WorkflowPlan,
)
from shared.constants import AgentID, MissionStatus, Platform, WorkflowStage
from shared.retry import RetryExecutor, RetryPolicy
from shared.schemas import AgentResult, ApprovalDecision, ExecutionStatus, Mission, Topic


class _Payload(BaseModel):
    value: str


def _mission(*, requires_approval: bool = True) -> Mission:
    return Mission(
        topic=Topic(title="AN-17 integration test"),
        requested_by="test-suite",
        target_platforms=[Platform.TELEGRAM],
        requires_human_approval=requires_approval,
    )


def _handler(agent_id: AgentID):
    def execute(context):
        now = datetime.now(timezone.utc)
        return AgentResult(
            agent_id=agent_id,
            mission_id=context.mission_id,
            status=ExecutionStatus.SUCCESS,
            payload=_Payload(value=agent_id.value),
            started_at=now,
            completed_at=now,
        )

    return execute


def _plan() -> WorkflowPlan:
    pairs = [
        (WorkflowStage.RESEARCH, AgentID.RESEARCH_CORE),
        (WorkflowStage.FACT_CHECK, AgentID.FACT_GUARDIAN),
        (WorkflowStage.SCRIPT, AgentID.SCRIPT_FORGE),
        (WorkflowStage.SEO, AgentID.SEO_BRAIN),
        (WorkflowStage.VISUAL_PLANNING, AgentID.VISION_PLANNER),
        (WorkflowStage.IMAGE_GENERATION, AgentID.VISION_CREATOR),
        (WorkflowStage.VOICE_GENERATION, AgentID.VOICE_CORE),
        (WorkflowStage.SUBTITLE, AgentID.SUBTITLE_ENGINE),
        (WorkflowStage.VIDEO_EDITING, AgentID.VIDEO_FORGE),
        (WorkflowStage.THUMBNAIL, AgentID.THUMBNAIL_STUDIO),
        (WorkflowStage.QUALITY_REVIEW, AgentID.QUALITY_SENTINEL),
        (WorkflowStage.PUBLISHING, AgentID.PUBLISHER),
        (WorkflowStage.ANALYTICS, AgentID.ANALYTICS_BRAIN),
    ]
    tasks = []
    previous = None
    for stage, agent_id in pairs:
        dependencies = (previous,) if previous else ()
        tasks.append(
            DispatchTask(
                task_id=stage.value,
                stage=stage,
                agent_id=agent_id,
                handler=_handler(agent_id),
                dependencies=dependencies,
            )
        )
        previous = stage.value
    return WorkflowPlan.from_tasks(tasks)


def test_complete_mission_lifecycle_with_approval() -> None:
    orchestrator = AlphaOrchestrator()
    mission = _mission()

    orchestrator.run_mission(mission, _plan())
    state = orchestrator.get_mission_state(mission.mission_id)
    assert state is not None
    assert state.stage is WorkflowStage.APPROVAL

    request = orchestrator.approval_manager.get_pending_request(mission.mission_id)
    assert request is not None
    orchestrator.receive_approval(
        request.approval_id,
        ApprovalDecision.APPROVED,
        reviewer="integration-test",
    )
    orchestrator.resume_mission(mission, _plan())

    final_state = orchestrator.get_mission_state(mission.mission_id)
    assert final_state is not None
    assert final_state.stage is WorkflowStage.MISSION_COMPLETE
    assert final_state.status.value == "completed"


def test_dispatcher_retries_retryable_agent_failure() -> None:
    attempts = 0

    # Retryability is intentionally supplied through the Alpha exception boundary.
    from shared.exceptions import AgentExecutionError

    def retryable_flaky(context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AgentExecutionError(
                "transient",
                agent_id=AgentID.RESEARCH_CORE,
                mission_id=context.mission_id,
                retryable=True,
            )
        return _handler(AgentID.RESEARCH_CORE)(context)

    dispatcher = Dispatcher(
        retry_executor=RetryExecutor(
            RetryPolicy(
                max_attempts=2,
                delay_seconds=0,
                backoff_multiplier=1,
                timeout_seconds=10,
            )
        )
    )
    mission = _mission(requires_approval=False)
    task = DispatchTask(
        task_id="research",
        stage=WorkflowStage.RESEARCH,
        agent_id=AgentID.RESEARCH_CORE,
        handler=retryable_flaky,
    )
    result = dispatcher.dispatch(task, mission=mission, dependency_results={})
    assert result.status is ExecutionStatus.SUCCESS
    assert result.retry_count == 1
    assert attempts == 2


def test_recovery_restores_failed_mission() -> None:
    orchestrator = AlphaOrchestrator()
    mission = _mission(requires_approval=False)
    orchestrator.receive_mission(mission)
    orchestrator.state_manager.transition(
        mission.mission_id,
        status=MissionStatus.FAILED,
        stage=WorkflowStage.RESEARCH,
        reason="test_failure",
    )

    assessment = orchestrator.recovery_manager.assess(mission.mission_id)
    assert assessment.disposition is RecoveryDisposition.RECOVERABLE

    recovered = orchestrator.recover_mission(
        mission.mission_id,
        lambda state: state.stage.value,
    )
    assert recovered.state.status.value == "retrying"
    assert recovered.value == WorkflowStage.RESEARCH.value
