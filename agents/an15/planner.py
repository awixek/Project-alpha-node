"""Deterministic platform and transformation planning."""
from __future__ import annotations

from agents.an13.models import AnalyticsReport
from agents.an14.models import EvolutionReport
from agents.an12.models import PublishPackage
from .models import DistributionPlan, PlatformProfile, RepurposeConfig, TransformationType
from shared.constants import Platform


class RepurposePlanner:
    def plan(self, publish: PublishPackage, analytics: AnalyticsReport, evolution: EvolutionReport,
              config: RepurposeConfig) -> DistributionPlan:
        requested = list(config.enabled_platforms) if config.enabled_platforms else list(dict.fromkeys(r.platform for r in publish.platform_records))
        if not requested:
            requested = [Platform.YOUTUBE]
        priority_order = config.distribution_priority or requested
        ordered = sorted(dict.fromkeys(requested), key=lambda p: priority_order.index(p) if p in priority_order else len(priority_order))
        rationale = [f"Selected {p.value} because it is enabled or represented in the canonical publication package." for p in ordered]
        if evolution.optimization_recommendations:
            rationale.append(f"Applied {len(evolution.optimization_recommendations)} evolution recommendations as optimization context.")
        if analytics.trend_analysis.reports:
            rationale.append(f"Used {len(analytics.trend_analysis.reports)} analytics trend signals for destination planning.")
        return DistributionPlan(ordered_platforms=ordered, rationale=rationale, staged=False)

    def profile(self, platform: Platform, config: RepurposeConfig) -> PlatformProfile:
        defaults = {
            Platform.YOUTUBE: dict(max_duration_seconds=None, max_title_chars=100, max_text_chars=5000, max_hashtags=15, aspect_ratio="16:9", cta_style="soft", title_strategy="clear", caption_strategy="detailed", thumbnail_required=True),
            Platform.INSTAGRAM: dict(max_duration_seconds=180, max_title_chars=100, max_text_chars=2200, max_hashtags=5, aspect_ratio="9:16", cta_style="direct", title_strategy="hook", caption_strategy="concise", thumbnail_required=True),
            Platform.TIKTOK: dict(max_duration_seconds=600, max_title_chars=150, max_text_chars=2200, max_hashtags=5, aspect_ratio="9:16", cta_style="direct", title_strategy="hook", caption_strategy="concise", thumbnail_required=True),
            Platform.FACEBOOK: dict(max_duration_seconds=90, max_title_chars=100, max_text_chars=5000, max_hashtags=5, aspect_ratio="9:16", cta_style="soft", title_strategy="clear", caption_strategy="detailed", thumbnail_required=True),
            Platform.X: dict(max_duration_seconds=140, max_title_chars=280, max_text_chars=280, max_hashtags=3, aspect_ratio="16:9", cta_style="question", title_strategy="concise", caption_strategy="thread", thumbnail_required=False, supports_threads=True),
            Platform.LINKEDIN: dict(max_duration_seconds=None, max_title_chars=200, max_text_chars=3000, max_hashtags=5, aspect_ratio="16:9", cta_style="professional", title_strategy="insight", caption_strategy="professional", thumbnail_required=True),
            Platform.TELEGRAM: dict(max_duration_seconds=None, max_title_chars=200, max_text_chars=4096, max_hashtags=10, aspect_ratio="16:9", cta_style="soft", title_strategy="clear", caption_strategy="detailed", thumbnail_required=False),
            Platform.WEBSITE: dict(max_duration_seconds=None, max_title_chars=180, max_text_chars=20000, max_hashtags=10, aspect_ratio="16:9", cta_style="soft", title_strategy="search", caption_strategy="article", thumbnail_required=True),
        }
        values = defaults.get(platform, dict(max_title_chars=160, max_text_chars=4000, max_hashtags=5))
        override = config.default_profiles.get(platform.value, {})
        values = {**values, **override, "platform": platform}
        return PlatformProfile(**values)

    @staticmethod
    def transformation_for(platform: Platform, config: RepurposeConfig) -> TransformationType:
        overrides = config.transformation_rules.get(platform.value)
        if isinstance(overrides, str):
            try:
                return TransformationType(overrides)
            except ValueError:
                pass
        return {
            Platform.INSTAGRAM: TransformationType.REEL,
            Platform.TIKTOK: TransformationType.SHORT_VIDEO,
            Platform.FACEBOOK: TransformationType.REEL,
            Platform.X: TransformationType.SOCIAL_THREAD,
            Platform.LINKEDIN: TransformationType.LINKEDIN_ARTICLE,
            Platform.TELEGRAM: TransformationType.TELEGRAM_POST,
            Platform.WEBSITE: TransformationType.BLOG_ARTICLE,
            Platform.YOUTUBE: TransformationType.COMMUNITY_UPDATE,
        }.get(platform, TransformationType.CAPTION_VARIANT)
