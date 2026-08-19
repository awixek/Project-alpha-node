"""Provider-neutral content transformation engine."""
from __future__ import annotations

import re
from textwrap import shorten

from .adapters import AdapterRegistry
from .models import PlatformProfile, TransformationType, TransformedAsset
from shared.constants import Platform


class ContentTransformer:
    def __init__(self, registry: AdapterRegistry | None = None) -> None:
        self._registry = registry or AdapterRegistry()

    def transform(self, *, platform: Platform, transformation: TransformationType, source_title: str,
                  source_text: str, profile: PlatformProfile, source_reference: str) -> TransformedAsset:
        adapter = self._registry.get(platform)
        if adapter:
            title, body = adapter.transform(source_title=source_title, source_text=source_text, profile=profile)
        else:
            title, body = self._deterministic_transform(platform, transformation, source_title, source_text, profile)
        title = self._limit(title, profile.max_title_chars)
        body = self._limit(body, profile.max_text_chars)
        duration = profile.max_duration_seconds if transformation in {TransformationType.REEL, TransformationType.SHORT_VIDEO} else None
        return TransformedAsset(platform=platform, transformation=transformation, source_reference=source_reference,
                                title=title, body=body, duration_seconds=duration, aspect_ratio=profile.aspect_ratio,
                                source_asset_ids=[source_reference])

    def _deterministic_transform(self, platform: Platform, transformation: TransformationType,
                                 title: str, text: str, profile: PlatformProfile) -> tuple[str, str]:
        clean = self._clean(text)
        if transformation in {TransformationType.REEL, TransformationType.SHORT_VIDEO}:
            words = clean.split()
            body = " ".join(words[:160])
            return title, body
        if transformation is TransformationType.SOCIAL_THREAD:
            chunks = self._chunks(clean, 250)
            body = "\n\n".join(f"{i}. {chunk}" for i, chunk in enumerate(chunks[:10], 1))
            return title, body
        if transformation is TransformationType.LINKEDIN_ARTICLE:
            return title, f"Key insight\n\n{clean}\n\nWhat this means: review the evidence and context before applying the conclusion."
        if transformation is TransformationType.BLOG_ARTICLE:
            return title, f"# {title}\n\n{clean}\n\n## Key takeaway\n\nThe article preserves the canonical source while adapting presentation for the web."
        if transformation is TransformationType.TELEGRAM_POST:
            return title, clean
        return title, clean

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()

    @staticmethod
    def _limit(text: str, limit: int | None) -> str:
        if not limit or len(text) <= limit:
            return text
        return shorten(text, width=limit, placeholder="…")

    @staticmethod
    def _chunks(text: str, size: int) -> list[str]:
        words = text.split()
        chunks: list[str] = []
        current: list[str] = []
        length = 0
        for word in words:
            if current and length + len(word) + 1 > size:
                chunks.append(" ".join(current))
                current, length = [], 0
            current.append(word)
            length += len(word) + 1
        if current:
            chunks.append(" ".join(current))
        return chunks
