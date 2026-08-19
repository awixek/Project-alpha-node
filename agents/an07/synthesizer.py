from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from shared.constants import AgentID, LogCategory
from shared.exceptions import AlphaBaseException, AgentExecutionError
from shared.logger import AlphaLogger, get_agent_logger

from .models import VoiceCoreConfig, VoiceProviderRequest, VoiceProviderResponse, VoiceProfile
from .pronunciation import PronunciationProcessor
from .provider import VoiceProviderRouter


@dataclass(frozen=True, slots=True)
class SynthesisOutcome:
    response: VoiceProviderResponse
    elapsed_ms: float


class VoiceSynthesizer:
    def __init__(
        self,
        provider_router: VoiceProviderRouter,
        *,
        config: VoiceCoreConfig,
        pronunciation: PronunciationProcessor | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._providers = provider_router
        self._config = config
        self._pronunciation = pronunciation or PronunciationProcessor()
        self._logger = logger or get_agent_logger(AgentID.VOICE_CORE)

    def synthesize(
        self,
        *,
        mission_id,
        segment_id: str,
        text: str,
        profile: VoiceProfile,
        pronunciation_dictionary: dict[str, str],
    ) -> SynthesisOutcome:
        processed, entries = self._pronunciation.process(text, pronunciation_dictionary)
        request = VoiceProviderRequest(
            mission_id=mission_id,
            segment_id=segment_id,
            text=processed,
            language=profile.language,
            profile=profile,
            pronunciation=entries,
            timeout=self._config.timeout,
            output_format=self._config.output_format,
        )
        started = time.perf_counter()
        try:
            response = self._providers.synthesize(request)
        except AlphaBaseException:
            raise
        except Exception as exc:
            raise AgentExecutionError(
                "Voice provider execution failed.",
                agent_id=AgentID.VOICE_CORE,
                mission_id=mission_id,
                retryable=True,
                cause=exc,
            ) from exc
        return SynthesisOutcome(response=response, elapsed_ms=(time.perf_counter() - started) * 1000.0)
