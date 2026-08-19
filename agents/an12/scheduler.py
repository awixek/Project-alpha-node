from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shared.exceptions import ValidationError
from shared.constants import AgentID
from .models import PublisherConfig, PublishRequest, SchedulingMode


class PublicationScheduler:
    """Validates and normalizes timezone-aware publication scheduling."""

    def resolve(self, request: PublishRequest, config: PublisherConfig) -> datetime | None:
        mode = request.scheduling_mode
        if mode is SchedulingMode.IMMEDIATE:
            return None
        if mode is SchedulingMode.DRY_RUN:
            return request.scheduled_at
        if mode is SchedulingMode.SCHEDULED:
            if request.scheduled_at is None:
                raise ValidationError("scheduled_at is required for scheduled publishing.", agent_id=AgentID.PUBLISHER, mission_id=request.mission_id)
            return self._normalize(request.scheduled_at, request.timezone, request.mission_id)
        if mode is SchedulingMode.DELAYED:
            if request.scheduled_at is None:
                raise ValidationError("scheduled_at is required for delayed publishing.", agent_id=AgentID.PUBLISHER, mission_id=request.mission_id)
            target = self._normalize(request.scheduled_at, request.timezone, request.mission_id)
            if target <= datetime.now(timezone.utc):
                raise ValidationError("Delayed publication time must be in the future.", agent_id=AgentID.PUBLISHER, mission_id=request.mission_id)
            return target
        if mode is SchedulingMode.STAGED:
            return self._normalize(request.scheduled_at, request.timezone, request.mission_id) if request.scheduled_at else None
        raise ValidationError("Unsupported scheduling mode.", agent_id=AgentID.PUBLISHER, mission_id=request.mission_id)

    @staticmethod
    def _normalize(value: datetime, timezone_name: str, mission_id) -> datetime:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError("Invalid publication timezone.", agent_id=AgentID.PUBLISHER, mission_id=mission_id, context={"timezone": timezone_name}) from exc
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(timezone_name))
        return value.astimezone(timezone.utc)
