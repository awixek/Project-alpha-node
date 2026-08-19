"""Platform metadata formatting and canonical-source preservation."""
from __future__ import annotations

import re

from .models import PlatformMetadata, PlatformProfile, RepurposeRequest


class MetadataFormatter:
    def build(self, request: RepurposeRequest, *, title: str, body: str, profile: PlatformProfile) -> PlatformMetadata:
        canonical = request.publish.platform_metadata
        source = next(iter(canonical.values()), None)
        hashtags = list(source.hashtags) if source else []
        tags = list(source.tags) if source else []
        hashtags = self._dedupe([self._hashtag(h) for h in hashtags])[:profile.max_hashtags]
        tags = self._dedupe(tags)
        description = self._clean(body)
        if profile.max_text_chars:
            description = description[:profile.max_text_chars]
        cta = self._cta(profile.cta_style)
        return PlatformMetadata(title=title, description=description, hashtags=hashtags, tags=tags,
                                cta=cta, locale=source.locale if source else None,
                                metadata={"strategy": "canonical_first", "aspect_ratio": profile.aspect_ratio})

    @staticmethod
    def _hashtag(value: str) -> str:
        value = value.strip().lstrip("#")
        return f"#{value}" if value else ""

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                out.append(value)
        return out

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @staticmethod
    def _cta(style: str) -> str:
        return {
            "direct": "Watch the full story and explore the evidence.",
            "question": "What do you think?",
            "professional": "Share your perspective or practical takeaway.",
            "search": "Read the full evidence-based article.",
        }.get(style, "Explore the full story.")
