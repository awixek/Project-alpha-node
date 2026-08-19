from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from shared.constants import AgentID, LogCategory
from shared.exceptions import AgentExecutionError, AllProvidersFailedError
from shared.logger import AlphaLogger, get_agent_logger
from .models import GeneratedAsset, GenerationKind, GenerationRequest, GenerationStatus, ProviderAssetResponse, VisionCreatorConfig
from .provider import VisionProviderRouter

@dataclass(frozen=True, slots=True)
class RenderOutcome:
    asset: GeneratedAsset
    provider: str
    elapsed_ms: float

class SceneRenderer:
    def __init__(self, provider_router: VisionProviderRouter, *, config: VisionCreatorConfig, logger: AlphaLogger | None = None) -> None:
        self._router = provider_router; self._config = config; self._logger = logger or get_agent_logger(AgentID.VISION_CREATOR)
    def render(self, request: GenerationRequest) -> RenderOutcome:
        started = time.perf_counter()
        try: response = self._router.generate(request)
        except AllProvidersFailedError as exc:
            raise AgentExecutionError("All configured vision providers failed.", agent_id=AgentID.VISION_CREATOR, mission_id=request.mission_id, retryable=True, context={"scene_id": request.scene_id}, cause=exc) from exc
        elapsed = (time.perf_counter() - started) * 1000.0
        if response.content_bytes is not None and not response.content_bytes:
            raise AgentExecutionError("Provider returned an empty asset payload.", agent_id=AgentID.VISION_CREATOR, mission_id=request.mission_id, retryable=True, context={"scene_id": request.scene_id})
        asset = GeneratedAsset(asset_id=uuid4(), mission_id=request.mission_id, asset_type=response.asset_type, storage_path=response.storage_path, provider=response.provider, generated_at=datetime.now(timezone.utc), checksum=response.checksum, scene_id=request.scene_id, generation_kind=request.kind, generation_status=GenerationStatus.GENERATED, generation_time_ms=elapsed, width_px=response.width_px, height_px=response.height_px, duration_seconds=response.duration_seconds, mime_type=response.mime_type, source_checksum=response.checksum)
        self._logger.info("AN-06 scene generation completed.", category=LogCategory.AGENT, mission_id=request.mission_id, agent_id=AgentID.VISION_CREATOR, metadata={"scene_id": request.scene_id, "provider": response.provider})
        return RenderOutcome(asset=asset, provider=response.provider, elapsed_ms=elapsed)
