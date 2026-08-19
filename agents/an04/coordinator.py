"""AN-04 orchestration pipeline."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re

from shared.constants import AgentID, EventName, LogCategory
from shared.event_bus import EventBus, get_event_bus
from shared.exceptions import AgentExecutionError, AllProvidersFailedError, InputValidationError
from shared.logger import AlphaLogger, get_agent_logger

from .keyword_engine import KeywordEngine
from .metadata import MetadataBuilder
from .models import SEOConfig, SEORequest, SEOResult
from .optimizer import SEOOptimizer
from .providers import SEOGenerationProviderRegistry


class SEOBrainCoordinator:
    """Coordinates deterministic SEO analysis with optional provider enrichment."""

    def __init__(
        self,
        *,
        config: SEOConfig | None = None,
        provider_registry: SEOGenerationProviderRegistry | None = None,
        keyword_engine: KeywordEngine | None = None,
        optimizer: SEOOptimizer | None = None,
        metadata_builder: MetadataBuilder | None = None,
        event_bus: EventBus | None = None,
        logger: AlphaLogger | None = None,
    ) -> None:
        self._config = config or SEOConfig.from_shared_config()
        self._providers = provider_registry
        self._keywords = keyword_engine or KeywordEngine()
        self._optimizer = optimizer or SEOOptimizer(keyword_engine=self._keywords)
        self._metadata = metadata_builder or MetadataBuilder()
        self._event_bus = event_bus or get_event_bus()
        self._logger = logger or get_agent_logger(AgentID.SEO_BRAIN)

    def run(self, request: SEORequest) -> SEOResult:
        self._validate(request)
        config = self._apply_overrides(request)
        self._emit(EventName.AGENT_STARTED, request.mission_id, {"stage": "seo"})
        self._logger.info("SEO optimization started.", category=LogCategory.AGENT, agent_id=AgentID.SEO_BRAIN, mission_id=request.mission_id)

        corpus = self._content(request)
        primary, secondary, long_tail, clusters = self._keywords.build(corpus, config)
        self._logger.info("SEO keywords extracted.", category=LogCategory.QUALITY, agent_id=AgentID.SEO_BRAIN, mission_id=request.mission_id, metadata={"primary": len(primary), "secondary": len(secondary), "long_tail": len(long_tail)})

        optimized_title = self._optimizer.generate_title(request.script.title, primary, config)
        alternatives = self._optimizer.title_variations(optimized_title, primary, config)
        description = self._description(request.script, optimized_title, primary, config)
        excerpt = self._excerpt(request.script, config)
        tags = self._keywords.deduplicate(primary + secondary + long_tail, limit=config.max_tags)
        hashtags = self._hashtags(primary + secondary, config.max_hashtags)
        slug = self._optimizer.slug(optimized_title)

        # Optional provider enrichment is deliberately best-effort. The deterministic
        # package remains usable when no provider is registered or a provider fails.
        if self._providers is not None and self._providers.provider_names:
            try:
                generated = self._providers.generate(
                    self._providers.request_type(
                        mission_id=request.mission_id,
                        script_title=request.script.title,
                        corpus=corpus,
                        optimized_title=optimized_title,
                        language=config.language,
                    )
                )
                if generated.description:
                    description = self._trim(generated.description, config.description_max_length)
                if generated.alternative_titles:
                    alternatives = self._keywords.deduplicate(generated.alternative_titles + alternatives, limit=config.title_variations)
            except AllProvidersFailedError as exc:
                self._logger.warning("SEO provider enrichment failed; using deterministic fallback.", category=LogCategory.API, agent_id=AgentID.SEO_BRAIN, mission_id=request.mission_id, metadata={"reason": exc.code})

        readability = self._optimizer.readability(corpus)
        density = self._optimizer.keyword_density(corpus, primary[:3])
        clickbait = self._optimizer.clickbait_score(optimized_title)
        title_quality = self._optimizer.title_quality(optimized_title, primary, config)
        coverage = self._keyword_coverage(corpus, primary)
        completeness = self._content_completeness(request.script)
        seo_score, breakdown = self._optimizer.score(
            title_quality=title_quality,
            keyword_coverage=coverage,
            readability=readability,
            keyword_density=density,
            content_completeness=completeness,
            clickbait_score=clickbait,
            config=config,
        )
        recommendations = self._optimizer.recommendations(
            title_quality=title_quality,
            keyword_density=density,
            clickbait_score=clickbait,
            readability=readability,
            primary_keywords=primary,
            description=description,
            config=config,
        )

        metadata, open_graph, twitter_card = self._metadata.build(
            script=request.script,
            optimized_title=optimized_title,
            alternative_titles=alternatives,
            description=description,
            tags=tags,
            hashtags=hashtags,
            slug=slug,
            config=config,
        )
        result = SEOResult(
            mission_id=request.mission_id,
            optimized_title=optimized_title,
            alternative_titles=alternatives,
            primary_keywords=primary,
            secondary_keywords=secondary,
            long_tail_keywords=long_tail,
            semantic_clusters=clusters,
            hashtags=hashtags,
            slug=slug,
            description=description,
            excerpt=excerpt,
            tags=tags,
            metadata=metadata,
            open_graph=open_graph,
            twitter_card=twitter_card,
            readability_score=readability,
            seo_score=seo_score,
            clickbait_score=clickbait,
            keyword_density=density,
            score_breakdown=breakdown,
            recommendations=recommendations,
        )
        self._logger.info("SEO optimization completed.", category=LogCategory.QUALITY, agent_id=AgentID.SEO_BRAIN, mission_id=request.mission_id, metadata={"seo_score": round(seo_score, 2), "readability": round(readability, 2)})
        self._emit(EventName.AGENT_COMPLETED, request.mission_id, {"stage": "seo", "score": f"{seo_score:.2f}"})
        return result

    @staticmethod
    def _validate(request: SEORequest) -> None:
        if request.script.mission_id != request.mission_id:
            raise InputValidationError("Script mission_id does not match SEO request mission_id.", agent_id=AgentID.SEO_BRAIN, mission_id=request.mission_id, context={"operation": "validate_request"})
        if not request.script.sections:
            raise InputValidationError("SEO Brain requires a script containing at least one section.", agent_id=AgentID.SEO_BRAIN, mission_id=request.mission_id, context={"operation": "validate_request"})

    def _apply_overrides(self, request: SEORequest) -> SEOConfig:
        base = self._config
        values = {
            "language": base.language,
            "title_min_length": base.title_min_length,
            "title_max_length": base.title_max_length,
            "description_max_length": base.description_max_length,
            "excerpt_max_length": base.excerpt_max_length,
            "max_primary_keywords": base.max_primary_keywords,
            "max_secondary_keywords": base.max_secondary_keywords,
            "max_long_tail_keywords": base.max_long_tail_keywords,
            "max_semantic_clusters": base.max_semantic_clusters,
            "max_hashtags": base.max_hashtags,
            "max_tags": base.max_tags,
            "keyword_density_warning": base.keyword_density_warning,
            "clickbait_warning": base.clickbait_warning,
            "preferred_keyword_length": base.preferred_keyword_length,
            "title_variations": base.title_variations,
            "site_url": base.site_url,
            "locale": base.locale,
            "score_weights": dict(base.score_weights),
        }
        values.update(request.runtime_overrides)
        if "score_weights" in request.runtime_overrides:
            weights = dict(base.score_weights)
            weights.update(dict(request.runtime_overrides["score_weights"]))
            values["score_weights"] = weights
        try:
            return SEOConfig(**values)
        except (TypeError, ValueError) as exc:
            raise InputValidationError("Invalid AN-04 runtime configuration override.", agent_id=AgentID.SEO_BRAIN, mission_id=request.mission_id, context={"operation": "apply_overrides"}, cause=exc) from exc

    @staticmethod
    def _content(request: SEORequest) -> str:
        parts = [request.script.title]
        for section in request.script.sections:
            if section.heading:
                parts.append(section.heading)
            parts.append(section.narration)
            if section.on_screen_text:
                parts.append(section.on_screen_text)
        return "\n".join(part for part in parts if part.strip())

    @staticmethod
    def _description(script, title: str, primary: list[str], config: SEOConfig) -> str:
        narration = " ".join(section.narration.strip() for section in script.sections if section.narration.strip())
        prefix = f"{title}. "
        if primary:
            prefix += f"Explore {', '.join(primary[:2])} with evidence-based context. "
        return SEOBrainCoordinator._trim(prefix + narration, config.description_max_length)

    @staticmethod
    def _excerpt(script, config: SEOConfig) -> str:
        text = " ".join(section.narration.strip() for section in script.sections if section.narration.strip())
        return SEOBrainCoordinator._trim(text or script.title, config.excerpt_max_length)

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        clean = " ".join(text.split())
        if len(clean) <= limit:
            return clean
        clipped = clean[:limit - 1].rsplit(" ", 1)[0]
        return clipped.rstrip(".,;:-") + "…"

    @staticmethod
    def _hashtags(keywords: list[str], limit: int) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            tag = "#" + re.sub(r"[^\w]", "", keyword.title().replace(" ", ""))
            if len(tag) <= 1 or tag.casefold() in seen:
                continue
            seen.add(tag.casefold())
            result.append(tag)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _keyword_coverage(corpus: str, primary: list[str]) -> float:
        if not primary:
            return 0.0
        lowered = corpus.casefold()
        covered = sum(keyword.casefold() in lowered for keyword in primary)
        return covered / len(primary) * 100.0

    @staticmethod
    def _content_completeness(script) -> float:
        headings = sum(bool(section.heading) for section in script.sections)
        evidence = bool(script.evidence_sources)
        narrative = sum(bool(section.narration.strip()) for section in script.sections)
        section_score = min(1.0, narrative / max(1, len(script.sections)))
        structure_score = min(1.0, headings / max(1, len(script.sections)))
        evidence_score = 1.0 if evidence else 0.5
        return (section_score * 0.5 + structure_score * 0.2 + evidence_score * 0.3) * 100.0

    def _emit(self, event: EventName, mission_id, payload: dict[str, str]) -> None:
        try:
            self._event_bus.emit(event, mission_id=mission_id, agent_id=AgentID.SEO_BRAIN, payload=payload)
        except Exception:  # noqa: BLE001 - observability must never break SEO generation
            self._logger.warning("Failed to emit SEO lifecycle event.", category=LogCategory.ERROR, agent_id=AgentID.SEO_BRAIN, mission_id=mission_id)
