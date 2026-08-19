"""SEO metadata construction for AN-04."""
from __future__ import annotations

from agents.an03.models import ScriptDocument
from shared.schemas import SEOMetadata

from .models import OpenGraphMetadata, SEOConfig, TwitterCardMetadata


class MetadataBuilder:
    """Builds standards-oriented metadata without provider-specific logic."""

    def build(
        self,
        *,
        script: ScriptDocument,
        optimized_title: str,
        alternative_titles: list[str],
        description: str,
        tags: list[str],
        hashtags: list[str],
        slug: str,
        config: SEOConfig,
    ) -> tuple[SEOMetadata, OpenGraphMetadata, TwitterCardMetadata]:
        url = None
        if config.site_url:
            url = f"{config.site_url.rstrip('/')}/{slug}"
        metadata = SEOMetadata(
            mission_id=script.mission_id,
            primary_title=optimized_title,
            alt_titles=alternative_titles,
            description=description,
            tags=tags,
            hashtags=hashtags,
        )
        open_graph = OpenGraphMetadata(
            title=optimized_title,
            description=description,
            type="article",
            url=url,
            locale=config.locale,
        )
        twitter_card = TwitterCardMetadata(
            title=optimized_title,
            description=description,
        )
        return metadata, open_graph, twitter_card
