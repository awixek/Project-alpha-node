"""
tests/test_schemas.py

Purpose
-------
shared/schemas.py is the pydantic data-contract layer every agent
(including AN-17, the Orchestrator) will construct, pass around, and
serialize. The smoke-test bar: the module imports cleanly, its most
structurally important models (Mission, MissionState, AgentResult,
ErrorReport) build and reject bad input as designed, immutability is
enforced where declared, and the four vocabulary enums genuinely come
from shared.constants rather than a drifted local redefinition (the
exact bug class the Phase 2.1 Foundation Review called out).

Strategy
--------
* Import + `__all__` completeness check.
* Instantiate one "leaf" model (SourceRef) and one "root" model
  (Mission) with minimal valid data.
* Confirm `extra="forbid"` actually rejects an unknown field.
* Confirm ImmutableAlphaModel subclasses (e.g. DecisionRecord) reject
  attribute mutation after construction.
* Confirm AgentResult, being Generic, works with a concrete payload
  type and defaults `payload`/`error` sensibly.
* Confirm AgentID/MissionStatus/WorkflowStage/Platform are the same
  objects as shared.constants' (identity, not just equal value).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from shared import constants, schemas


def test_module_imports_and_exports_everything_declared():
    for name in schemas.__all__:
        assert hasattr(schemas, name), f"{name} listed in __all__ but missing from module"


def test_vocabulary_enums_are_imported_from_constants_not_redefined():
    assert schemas.AgentID is constants.AgentID
    assert schemas.MissionStatus is constants.MissionStatus
    assert schemas.WorkflowStage is constants.WorkflowStage
    assert schemas.Platform is constants.Platform


def test_source_ref_instantiates_with_defaults():
    ref = schemas.SourceRef(url="https://example.com/article")
    assert ref.url == "https://example.com/article"
    assert ref.reliability == schemas.SourceReliability.UNVERIFIED
    assert ref.schema_version == schemas.SCHEMA_VERSION


def test_mission_instantiates_with_required_fields_only():
    mission = schemas.Mission(
        topic=schemas.Topic(title="Ancient Roman Aqueducts"),
        requested_by="abhishek",
    )
    assert mission.topic.title == "Ancient Roman Aqueducts"
    assert mission.priority == 5  # documented default
    assert mission.requires_human_approval is True
    assert mission.target_platforms == []


def test_mission_missing_required_field_raises_validation_error():
    with pytest.raises(ValidationError):
        schemas.Mission(requested_by="abhishek")  # topic is required


def test_base_model_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        schemas.Topic(title="X", not_a_real_field="oops")


def test_mission_priority_is_bounded_one_to_ten():
    with pytest.raises(ValidationError):
        schemas.Mission(
            topic=schemas.Topic(title="X"),
            requested_by="abhishek",
            priority=11,
        )


def test_immutable_model_rejects_mutation_after_construction():
    decision = schemas.DecisionRecord(
        mission_id=uuid4(),
        made_by="AN-17",
        decision="proceed to script stage",
    )
    with pytest.raises(ValidationError):
        decision.decision = "changed my mind"


def test_mutable_mission_state_allows_reassignment():
    state = schemas.MissionState(
        mission_id=uuid4(),
        status=constants.MissionStatus.PENDING,
    )
    state.status = constants.MissionStatus.RUNNING
    assert state.status == constants.MissionStatus.RUNNING


def test_agent_result_generic_payload_round_trips():
    class DummyPayload(BaseModel):
        note: str

    result = schemas.AgentResult[DummyPayload](
        agent_id=constants.AgentID.RESEARCH_CORE,
        mission_id=uuid4(),
        status=schemas.ExecutionStatus.SUCCESS,
        payload=DummyPayload(note="ok"),
        started_at=datetime.now(timezone.utc),
    )
    assert result.payload.note == "ok"
    assert result.error is None
    assert result.retry_count == 0


def test_error_report_requires_agent_id_and_message():
    report = schemas.ErrorReport(
        agent_id=constants.AgentID.ORCHESTRATOR,
        severity=schemas.Severity.ERROR,
        code="test_error",
        message="something failed",
    )
    assert report.retryable is False
    assert report.context == {}
