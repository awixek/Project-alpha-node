from __future__ import annotations

from shared.constants import AgentID, Platform
from shared.exceptions import ValidationError
from .adapters import VerificationAdapter, VerificationRequest
from .models import PlatformMetadata, VerificationReport, VerificationStatus


class PublicationVerifier:
    def __init__(self) -> None:
        self._adapters: dict[Platform, VerificationAdapter] = {}

    def register(self, adapter: VerificationAdapter) -> None:
        self._adapters[adapter.platform] = adapter

    def verify(self, *, mission_id, platform: Platform, platform_id: str,
               metadata: PlatformMetadata, thumbnail_uri: str | None) -> VerificationReport:
        adapter = self._adapters.get(platform)
        if adapter is None:
            raise ValidationError("No verification adapter registered for platform.", agent_id=AgentID.PUBLISHER, mission_id=mission_id, context={"platform": platform.value})
        response = adapter.verify(VerificationRequest(
            mission_id=mission_id, platform=platform, platform_id=platform_id,
            expected_metadata=metadata.model_dump(mode="json"), expected_thumbnail_uri=thumbnail_uri,
        ))
        passed = response.exists and response.processing_complete and response.metadata_integrity and response.thumbnail_integrity
        return VerificationReport(
            platform=platform,
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            platform_id=platform_id,
            url=response.url,
            upload_confirmed=response.exists,
            processing_confirmed=response.processing_complete,
            metadata_integrity=response.metadata_integrity,
            thumbnail_integrity=response.thumbnail_integrity,
            notes=[response.message] if response.message else [],
        )
