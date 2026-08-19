"""Provider-neutral destination adapter boundaries for AN-15."""
from __future__ import annotations

from typing import Protocol

from .models import PlatformProfile, PlatformMetadata, TransformedAsset
from shared.constants import Platform


class RepurposeAdapter(Protocol):
    """Optional adapter boundary for platform-specific transformations.

    Adapters do not upload content. They only apply destination rules. No
    vendor API is referenced here, keeping business logic provider-neutral.
    """

    @property
    def platform(self) -> Platform: ...

    def transform(self, *, source_title: str, source_text: str, profile: PlatformProfile) -> tuple[str, str]: ...

    def optimize_metadata(self, *, title: str, description: str, hashtags: list[str], tags: list[str],
                          profile: PlatformProfile) -> PlatformMetadata: ...


class AdapterRegistry:
    """Thread-safe-by-construction immutable snapshot registry."""

    def __init__(self, adapters: list[RepurposeAdapter] | None = None) -> None:
        self._adapters = {adapter.platform: adapter for adapter in (adapters or [])}

    def get(self, platform: Platform) -> RepurposeAdapter | None:
        return self._adapters.get(platform)

    def register(self, adapter: RepurposeAdapter) -> None:
        # Registration is intended during composition/bootstrap, before concurrent execution.
        self._adapters[adapter.platform] = adapter
