"""Deterministic SEO analysis, title optimization, and scoring."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .keyword_engine import KeywordEngine
from .models import SEOConfig, SEOScoreBreakdown


class SEOOptimizer:
    """Pure, deterministic optimization operations."""

    _CLICKBAIT_TERMS = frozenset(
        "shocking unbelievable secret exposed you won't believe insane destroys destroys everything".split()
    )

    def __init__(self, *, keyword_engine: KeywordEngine | None = None) -> None:
        self._keywords = keyword_engine or KeywordEngine()

    def title_quality(self, title: str, primary_keywords: list[str], config: SEOConfig) -> float:
        score = 60.0
        length = len(title.strip())
        if config.title_min_length <= length <= config.title_max_length:
            score += 25.0
        elif length < config.title_min_length:
            score -= min(20.0, (config.title_min_length - length) * 1.2)
        else:
            score -= min(25.0, (length - config.title_max_length) * 1.0)
        lowered = title.casefold()
        if primary_keywords and any(keyword.casefold() in lowered for keyword in primary_keywords[:3]):
            score += 10.0
        if title.count("!") > 1 or title.count("?") > 1:
            score -= 10.0
        if re.search(r"([!?])\1", title):
            score -= 8.0
        if title.isupper() and len(title) > 10:
            score -= 8.0
        return max(0.0, min(100.0, score))

    def clickbait_score(self, text: str) -> float:
        words = self._keywords.tokens(text)
        if not words:
            return 0.0
        term_hits = sum(word in self._CLICKBAIT_TERMS for word in words)
        punctuation = text.count("!") + text.count("?")
        score = (term_hits / len(words)) * 1000.0 + min(25.0, punctuation * 8.0)
        return max(0.0, min(100.0, score))

    def keyword_density(self, text: str, keywords: list[str]) -> float:
        words = self._keywords.tokens(text)
        if not words or not keywords:
            return 0.0
        normalized = [self._keywords.normalize(keyword) for keyword in keywords if keyword.strip()]
        hits = 0
        joined = " ".join(words)
        for keyword in normalized:
            hits += len(re.findall(rf"(?<!\w){re.escape(keyword)}(?!\w)", joined))
        return min(100.0, hits / len(words) * 100.0)

    def readability(self, text: str) -> float:
        words = self._keywords.tokens(text)
        sentences = max(1, len(re.findall(r"[.!?]+", text)))
        if not words:
            return 0.0
        syllables = sum(self._estimate_syllables(word) for word in words)
        words_count = len(words)
        score = 206.835 - 1.015 * (words_count / sentences) - 84.6 * (syllables / words_count)
        return max(0.0, min(100.0, score))

    @staticmethod
    def _estimate_syllables(word: str) -> int:
        word = unicodedata.normalize("NFKD", word.casefold())
        letters = re.sub(r"[^a-z]", "", word)
        if not letters:
            # Conservative heuristic for non-Latin tokens.
            return max(1, len(word) // 3)
        groups = len(re.findall(r"[aeiouy]+", letters))
        if letters.endswith("e") and groups > 1:
            groups -= 1
        return max(1, groups)

    def generate_title(self, original: str, primary_keywords: list[str], config: SEOConfig) -> str:
        title = " ".join(original.split()).strip()
        if not primary_keywords:
            return title[: config.title_max_length].rstrip(" -:")
        keyword = primary_keywords[0].strip()
        if keyword.casefold() in title.casefold():
            return title[: config.title_max_length].rstrip(" -:")
        separator = ": "
        candidate = f"{keyword.title()}{separator}{title}"
        if len(candidate) <= config.title_max_length:
            return candidate
        return title[: config.title_max_length].rstrip(" -:")

    def title_variations(self, title: str, primary_keywords: list[str], config: SEOConfig) -> list[str]:
        keyword = primary_keywords[0] if primary_keywords else ""
        bases = [
            title,
            f"{title}: Key Facts and Evidence",
            f"{title}: What the Evidence Shows",
            f"{title}: A Clear Guide",
            f"Understanding {title}",
            f"{keyword.title()}: {title}" if keyword else f"The Story Behind {title}",
        ]
        return self._keywords.deduplicate([item[: config.title_max_length].rstrip(" -:") for item in bases], limit=config.title_variations)

    def slug(self, title: str) -> str:
        normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
        normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.casefold()).strip("-")
        return normalized or "content"

    def score(
        self,
        *,
        title_quality: float,
        keyword_coverage: float,
        readability: float,
        keyword_density: float,
        content_completeness: float,
        clickbait_score: float,
        config: SEOConfig,
    ) -> tuple[float, SEOScoreBreakdown]:
        density_score = max(0.0, min(100.0, 100.0 - max(0.0, keyword_density - config.keyword_density_warning) * 20.0))
        clickbait_penalty = max(0.0, min(100.0, clickbait_score))
        breakdown = SEOScoreBreakdown(
            title_quality=title_quality,
            keyword_coverage=keyword_coverage,
            readability=readability,
            keyword_density=density_score,
            content_completeness=content_completeness,
            clickbait_penalty=100.0 - clickbait_penalty,
        )
        weights = dict(config.score_weights)
        total = sum(weights.values())
        normalized = {key: value / total for key, value in weights.items()}
        score = sum(getattr(breakdown, key) * weight for key, weight in normalized.items())
        return max(0.0, min(100.0, score)), breakdown

    def recommendations(
        self,
        *,
        title_quality: float,
        keyword_density: float,
        clickbait_score: float,
        readability: float,
        primary_keywords: list[str],
        description: str,
        config: SEOConfig,
    ) -> list[str]:
        recommendations: list[str] = []
        if title_quality < 70:
            recommendations.append("Refine the title for clearer search intent and an appropriate length.")
        if primary_keywords and not any(keyword.casefold() in description.casefold() for keyword in primary_keywords[:2]):
            recommendations.append("Include the primary keyword naturally in the description.")
        if keyword_density > config.keyword_density_warning:
            recommendations.append("Reduce repeated keyword usage to avoid keyword stuffing.")
        if clickbait_score >= config.clickbait_warning:
            recommendations.append("Reduce sensational wording and make the title more evidence-aligned.")
        if readability < 55:
            recommendations.append("Shorten long sentences and simplify wording for easier reading.")
        if not recommendations:
            recommendations.append("SEO package is balanced; maintain natural keyword usage and evidence-aligned wording.")
        return recommendations
