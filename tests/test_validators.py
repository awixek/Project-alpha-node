"""
tests/test_validators.py

Purpose
-------
shared/validators.py is the central validation framework every agent
should route checks through. The smoke-test bar: the module imports,
each validator class's core success and failure paths behave as
documented, and — since this module explicitly says it *delegates*
rather than duplicates (SchemaValidator wraps pydantic, ConfigValidator
delegates to shared.config's ConfigValidator) — that delegation
actually reaches the underlying implementation instead of silently
no-op'ing.

Strategy
--------
* GenericValidators: one success + one failure case per method.
* SchemaValidator.validate: valid data builds the model; invalid data
  raises SchemaValidationError (not a raw pydantic ValidationError).
* validators.ConfigValidator.validate_required actually delegates to
  shared.config.ConfigValidator (business-rule failure surfaces here).
* APIInputValidator: sanitize_string strips control chars; validate_url
  / validate_uuid / validate_platform accept good input and reject bad.
* WorkflowValidator: a legal transition passes, an illegal one raises
  WorkflowValidationError, and is_terminal is correct at both ends of
  the pipeline.
* MissionValidator: a well-formed Mission passes; an inactive-platform
  Mission and a bad status/stage combination both raise.
* FileValidator: extension, path-traversal, and size checks.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from shared.config import AlphaConfig, MissingConfigError
from shared.constants import FileExtension, MissionStatus, Platform, WorkflowStage
from shared.exceptions import (
    FileValidationError,
    InputValidationError,
    MissionValidationError,
    SchemaValidationError,
    WorkflowValidationError,
)
from shared.schemas import Mission, MissionState, Topic
from shared.validators import (
    APIInputValidator,
    ConfigValidator,
    FileValidator,
    GenericValidators,
    MissionValidator,
    SchemaValidator,
    WorkflowValidator,
)


# -- GenericValidators --------------------------------------------------

def test_require_non_empty_string_strips_and_returns():
    assert GenericValidators.require_non_empty_string("  hi  ", field_name="x") == "hi"


def test_require_non_empty_string_rejects_blank():
    with pytest.raises(InputValidationError):
        GenericValidators.require_non_empty_string("   ", field_name="x")


def test_require_uuid_accepts_valid_string_and_uuid_object():
    value = uuid4()
    assert GenericValidators.require_uuid(str(value), field_name="id") == value
    assert GenericValidators.require_uuid(value, field_name="id") == value


def test_require_uuid_rejects_malformed_string():
    with pytest.raises(InputValidationError):
        GenericValidators.require_uuid("not-a-uuid", field_name="id")


def test_require_url_accepts_https_and_rejects_missing_scheme():
    assert GenericValidators.require_url("https://example.com", field_name="url") == "https://example.com"
    with pytest.raises(InputValidationError):
        GenericValidators.require_url("example.com", field_name="url")


def test_require_positive_number():
    assert GenericValidators.require_positive_number(1.0, field_name="n") == 1.0
    with pytest.raises(InputValidationError):
        GenericValidators.require_positive_number(0, field_name="n")


def test_require_in_range():
    assert GenericValidators.require_in_range(5, field_name="n", minimum=0, maximum=10) == 5
    with pytest.raises(InputValidationError):
        GenericValidators.require_in_range(15, field_name="n", minimum=0, maximum=10)


# -- SchemaValidator ------------------------------------------------------

def test_schema_validator_builds_valid_model():
    topic = SchemaValidator.validate(Topic, {"title": "Test Topic"})
    assert isinstance(topic, Topic)
    assert topic.title == "Test Topic"


def test_schema_validator_wraps_pydantic_error_as_alpha_exception():
    with pytest.raises(SchemaValidationError):
        SchemaValidator.validate(Topic, {"not_a_field": "oops"})


# -- ConfigValidator delegation --------------------------------------------

def test_config_validator_delegation_reaches_business_rules():
    cfg = AlphaConfig(telegram={"enabled": True})  # missing token/chat_id
    with pytest.raises(MissingConfigError):
        ConfigValidator.validate_required(cfg)


# -- APIInputValidator ------------------------------------------------------

def test_sanitize_string_strips_control_characters():
    dirty = "hello\x00world  "
    assert APIInputValidator.sanitize_string(dirty, field_name="text") == "helloworld"


def test_sanitize_string_rejects_non_string_input():
    with pytest.raises(InputValidationError):
        APIInputValidator.sanitize_string(123, field_name="text")  # type: ignore[arg-type]


def test_validate_url_and_uuid_pass_through_to_generic_validators():
    assert APIInputValidator.validate_url("https://example.com") == "https://example.com"
    value = uuid4()
    assert APIInputValidator.validate_uuid(str(value)) == value


def test_validate_platform_accepts_active_and_rejects_inactive():
    assert APIInputValidator.validate_platform("telegram") == Platform.TELEGRAM
    with pytest.raises(InputValidationError):
        APIInputValidator.validate_platform("tiktok")  # defined but not active


def test_validate_platform_rejects_unknown_value():
    with pytest.raises(InputValidationError):
        APIInputValidator.validate_platform("not_a_platform")


# -- WorkflowValidator ------------------------------------------------------

def test_workflow_validator_allows_documented_transition():
    WorkflowValidator.validate_transition(WorkflowStage.RESEARCH, WorkflowStage.FACT_CHECK)


def test_workflow_validator_rejects_illegal_transition():
    with pytest.raises(WorkflowValidationError):
        WorkflowValidator.validate_transition(WorkflowStage.RESEARCH, WorkflowStage.PUBLISHING)


def test_workflow_validator_is_terminal_only_at_mission_complete():
    assert WorkflowValidator.is_terminal(WorkflowStage.MISSION_COMPLETE) is True
    assert WorkflowValidator.is_terminal(WorkflowStage.RESEARCH) is False


# -- MissionValidator ------------------------------------------------------

def test_mission_validator_passes_for_well_formed_mission():
    mission = Mission(
        topic=Topic(title="Valid Topic"),
        requested_by="abhishek",
        target_platforms=[Platform.YOUTUBE],
    )
    assert MissionValidator.validate_mission(mission) is mission


def test_mission_validator_rejects_mission_with_no_platforms():
    mission = Mission(topic=Topic(title="X"), requested_by="abhishek")
    with pytest.raises(MissionValidationError):
        MissionValidator.validate_mission(mission)


def test_mission_validator_rejects_inactive_platform():
    mission = Mission(
        topic=Topic(title="X"),
        requested_by="abhishek",
        target_platforms=[Platform.TIKTOK],
    )
    with pytest.raises(MissionValidationError):
        MissionValidator.validate_mission(mission)


def test_mission_validator_status_stage_combination_valid_case():
    MissionValidator.validate_status_stage_combination(
        MissionStatus.PENDING, WorkflowStage.MISSION_CREATED
    )


def test_mission_validator_status_stage_combination_invalid_case():
    with pytest.raises(MissionValidationError):
        MissionValidator.validate_status_stage_combination(
            MissionStatus.COMPLETED, WorkflowStage.RESEARCH
        )


def test_mission_validator_rejects_started_status_still_at_mission_created():
    with pytest.raises(MissionValidationError):
        MissionValidator.validate_status_stage_combination(
            MissionStatus.RUNNING, WorkflowStage.MISSION_CREATED
        )


def test_mission_validator_validates_full_mission_state():
    state = MissionState(mission_id=uuid4(), status=MissionStatus.PENDING)
    assert MissionValidator.validate_mission_state(state) is state


# -- FileValidator ------------------------------------------------------

def test_file_validator_accepts_allowed_extension():
    path = Path("script.json")
    assert FileValidator.validate_extension(path, allowed=[FileExtension.JSON]) is path


def test_file_validator_rejects_disallowed_extension():
    with pytest.raises(FileValidationError):
        FileValidator.validate_extension(Path("script.exe"), allowed=[FileExtension.JSON])


def test_file_validator_rejects_path_traversal(tmp_path):
    with pytest.raises(FileValidationError):
        FileValidator.validate_safe_path(tmp_path, Path("../../etc/passwd"))


def test_file_validator_accepts_path_within_base_dir(tmp_path):
    (tmp_path / "safe.txt").write_text("hi", encoding="utf-8")
    resolved = FileValidator.validate_safe_path(tmp_path, Path("safe.txt"))
    assert resolved == (tmp_path / "safe.txt").resolve()


def test_file_validator_rejects_oversized_file(tmp_path):
    big_file = tmp_path / "big.txt"
    big_file.write_bytes(b"x" * 1024)
    with pytest.raises(FileValidationError):
        FileValidator.validate_max_size(big_file, max_bytes=100)


def test_file_validator_accepts_file_within_size_limit(tmp_path):
    small_file = tmp_path / "small.txt"
    small_file.write_bytes(b"x" * 10)
    assert FileValidator.validate_max_size(small_file, max_bytes=100) == small_file


def test_file_validator_rejects_missing_file_for_size_check(tmp_path):
    with pytest.raises(FileValidationError):
        FileValidator.validate_max_size(tmp_path / "missing.txt", max_bytes=100)
