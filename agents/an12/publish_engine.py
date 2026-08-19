from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid5, NAMESPACE_URL

from shared.constants import AgentID, Platform
from shared.exceptions import AlphaBaseException, AgentExecutionError, ValidationError
from .adapters import AdapterRouter, PublishAdapterRequest
from .models import PlatformMetadata, PublicationRecord, PublicationStatus, PublishAttempt, UploadPackage, PublisherConfig, VerificationStatus
from .verifier import PublicationVerifier


class PublishEngine:
    def __init__(self, router: AdapterRouter, verifier: PublicationVerifier) -> None:
        self._router = router
        self._verifier = verifier

    def publish(self, *, mission_id, platform: Platform, video_uri: str, thumbnail_uri: str | None,
                metadata: PlatformMetadata, config: PublisherConfig) -> tuple[PublicationRecord, list[PublishAttempt]]:
        key = str(uuid5(NAMESPACE_URL, f"alpha-node:{mission_id}:{platform.value}"))
        package = UploadPackage(platform=platform, video_uri=video_uri, thumbnail_uri=thumbnail_uri, metadata=metadata, idempotency_key=key)
        attempts: list[PublishAttempt] = []
        last_error: AlphaBaseException | None = None
        for attempt_no in range(1, config.max_attempts + 1):
            started = datetime.now(timezone.utc)
            try:
                response = self._router.publish(PublishAdapterRequest(
                    mission_id=mission_id, platform=platform, video_uri=package.video_uri,
                    thumbnail_uri=package.thumbnail_uri, metadata=package.metadata.model_dump(mode="json"), idempotency_key=package.idempotency_key,
                ))
                if not response.upload_success or not response.platform_id:
                    raise AgentExecutionError("Platform upload did not return a successful publication.", agent_id=AgentID.PUBLISHER, mission_id=mission_id, retryable=True, context={"platform": platform.value})
                completed = datetime.now(timezone.utc)
                attempt = PublishAttempt(platform=platform, attempt_number=attempt_no, status=PublicationStatus.PUBLISHED,
                                         started_at=started, completed_at=completed, provider=response.provider,
                                         platform_id=response.platform_id, url=response.url)
                attempts.append(attempt)
                verification = self._verifier.verify(mission_id=mission_id, platform=platform, platform_id=response.platform_id,
                                                     metadata=metadata, thumbnail_uri=thumbnail_uri)
                status = PublicationStatus.VERIFIED if verification.status is VerificationStatus.PASSED else PublicationStatus.FAILED
                return PublicationRecord(platform=platform, status=status, platform_id=response.platform_id, url=response.url,
                                         scheduled_at=metadata.scheduled_at, attempts=attempts, verification=verification), attempts
            except AlphaBaseException as exc:
                last_error = exc
                attempts.append(PublishAttempt(platform=platform, attempt_number=attempt_no, status=PublicationStatus.FAILED,
                                               started_at=started, completed_at=datetime.now(timezone.utc), error_code=exc.code,
                                               error_message=exc.message, retryable=exc.retryable))
                if not exc.retryable:
                    break
        return PublicationRecord(platform=platform, status=PublicationStatus.FAILED, attempts=attempts), attempts
