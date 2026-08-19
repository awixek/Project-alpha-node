from __future__ import annotations

from datetime import datetime

from agents.an04.models import SEOResult
from .models import PlatformMetadata, PublisherConfig
from shared.constants import Platform


class MetadataBuilder:
    """Builds platform-neutral metadata from the canonical AN-04 SEO package."""

    def build(self, *, seo: SEOResult, platform: Platform, config: PublisherConfig,
              scheduled_at: datetime | None = None) -> PlatformMetadata:
        tags = list(dict.fromkeys(seo.tags))
        hashtags = list(dict.fromkeys(seo.hashtags))
        title = seo.optimized_title.strip()
        description = seo.description.strip()
        category = None
        if platform is Platform.YOUTUBE:
            category = "Education"
        return PlatformMetadata(
            title=title,
            description=description,
            hashtags=hashtags,
            tags=tags,
            category=category,
            locale=config.localization,
            visibility=config.default_visibility,
            scheduled_at=scheduled_at,
            thumbnail_required=True,
        )
