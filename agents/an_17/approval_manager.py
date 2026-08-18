"""AN-17 human-approval coordination for Milestone 3.

ApprovalManager owns approval lifecycle state only. It exposes a transport
protocol so a later Telegram adapter can be injected without coupling AN-17
to Telegram, HTTP, or any other provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Protocol
from uuid import UUID

from shared.constants import AgentID, EventName, LogCategory, MissionStatus, WorkflowStage
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import MissionError
from shared.logger import AlphaLogger, get_agent_logger
from shared.schemas import ApprovalDecision, ApprovalRequest, MissionState, WorkflowEvent

from .state_manager import StateManager


class ApprovalManagerError(MissionError):
    """Approval lifecycle operation failed safely."""

    default_code = "approval_manager_error"


class ApprovalNotifier(Protocol):
    """Future human-notification boundary; no provider is implemented here."""

    def notify_request(self, request: ApprovalRequest) -> None:
        """Deliver an approval request through an external channel."""
        ...

    def notify_decision(self, request: ApprovalRequest) -> None:
        """Deliver a resolved approval decision through an external channel."""
        ...


class ApprovalManager:
    """Thread-safe manager for manual approval checkpoints."""

    def __init__(
        self,
        *,
        state_manager: StateManager,
        event_bus: EventBus | None = None,
        notifier: ApprovalNotifier | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._state_manager = state_manager
        self._event_bus = event_bus or get_event_bus()
        self._notifier = notifier
        self._logger = logger or get_agent_logger(AgentID.ORCHESTRATOR)
        self._lock = threading.RLock()
        self._requests: dict[UUID, ApprovalRequest] = {}
        self._mission_requests: dict[UUID, UUID] = {}

    def create_request(self, mission_id: UUID) -> ApprovalRequest:
        """Pause a mission at the canonical APPROVAL checkpoint."""
        with self._lock:
            state = self._require_state(mission_id)
            if state.status is MissionStatus.WAITING_APPROVAL:
                existing_id = self._mission_requests.get(mission_id)
                if existing_id is not None:
                    return self._requests[existing_id]

            updated = self._state_manager.transition(
                mission_id,
                status=MissionStatus.WAITING_APPROVAL,
                stage=WorkflowStage.APPROVAL,
                current_agent=None,
                reason="approval_requested",
            )
            request = ApprovalRequest(
                mission_id=mission_id,
                requested_stage=updated.stage,
            )
            self._requests[request.approval_id] = request
            self._mission_requests[mission_id] = request.approval_id

            self._logger.info(
                "Manual approval requested.",
                category=LogCategory.WORKFLOW,
                mission_id=mission_id,
                workflow_stage=WorkflowStage.APPROVAL,
                metadata={"approval_id": str(request.approval_id)},
            )
            if self._notifier is not None:
                self._notifier.notify_request(request)
            return request

    def receive_decision(
        self,
        approval_id: UUID,
        decision: ApprovalDecision,
        *,
        reviewer: str,
        comments: str | None = None,
    ) -> ApprovalRequest:
        """Apply a human decision and update mission state atomically."""
        if decision is ApprovalDecision.PENDING:
            raise ApprovalManagerError(
                "A pending decision cannot be submitted.",
                agent_id=AgentID.ORCHESTRATOR,
                context={"operation": "receive_decision"},
            )
        if not reviewer.strip():
            raise ApprovalManagerError(
                "Approval reviewer must not be empty.",
                agent_id=AgentID.ORCHESTRATOR,
                context={"operation": "receive_decision"},
            )

        with self._lock:
            request = self._requests.get(approval_id)
            if request is None:
                raise ApprovalManagerError(
                    "Approval request was not found.",
                    agent_id=AgentID.ORCHESTRATOR,
                    context={"approval_id": str(approval_id)},
                )
            if request.decision is not ApprovalDecision.PENDING:
                raise ApprovalManagerError(
                    "Approval request has already been resolved.",
                    agent_id=AgentID.ORCHESTRATOR,
                    mission_id=request.mission_id,
                    context={"approval_id": str(approval_id)},
                )

            if decision is ApprovalDecision.APPROVED:
                self._state_manager.transition(
                    request.mission_id,
                    status=MissionStatus.RUNNING,
                    stage=WorkflowStage.PUBLISHING,
                    current_agent=None,
                    last_error=None,
                    reason="approval_approved",
                )
            else:
                # The frozen workflow graph has no APPROVAL -> SCRIPT edge.
                # Rejection/change requests therefore pause at APPROVAL rather
                # than inventing an illegal backward transition. A later
                # milestone can introduce a dedicated revision workflow.
                self._state_manager.transition(
                    request.mission_id,
                    status=MissionStatus.PAUSED,
                    stage=WorkflowStage.APPROVAL,
                    current_agent=None,
                    reason=f"approval_{decision.value}",
                )

            resolved = request.model_copy(
                update={
                    "decision": decision,
                    "reviewer": reviewer.strip(),
                    "comments": comments.strip() if comments else None,
                    "resolved_at": datetime.now(timezone.utc),
                }
            )
            self._requests[approval_id] = resolved
            self._mission_requests.pop(request.mission_id, None)

            self._event_bus.publish(
                WorkflowEvent(
                    mission_id=request.mission_id,
                    agent_id=AgentID.ORCHESTRATOR,
                    event_type=EventName.APPROVAL_RECEIVED.value,
                    payload={
                        "approval_id": str(approval_id),
                        "decision": decision.value,
                        "reviewer": reviewer.strip(),
                    },
                )
            )
            self._logger.info(
                "Approval decision recorded.",
                category=LogCategory.WORKFLOW,
                mission_id=request.mission_id,
                workflow_stage=WorkflowStage.APPROVAL,
                metadata={"approval_id": str(approval_id), "decision": decision.value},
            )
            if self._notifier is not None:
                self._notifier.notify_decision(resolved)
            return resolved

    def get_request(self, approval_id: UUID) -> ApprovalRequest | None:
        """Return an approval request snapshot, if present."""
        with self._lock:
            return self._requests.get(approval_id)

    def get_pending_request(self, mission_id: UUID) -> ApprovalRequest | None:
        """Return the currently pending request for a mission, if any."""
        with self._lock:
            request_id = self._mission_requests.get(mission_id)
            return self._requests.get(request_id) if request_id else None

    def _require_state(self, mission_id: UUID) -> MissionState:
        state = self._state_manager.get_state(mission_id)
        if state is None:
            raise ApprovalManagerError(
                "Cannot create approval for an unknown mission.",
                agent_id=AgentID.ORCHESTRATOR,
                mission_id=mission_id,
                context={"operation": "create_request"},
            )
        return state
