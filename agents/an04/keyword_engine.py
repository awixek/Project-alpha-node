"""Deterministic keyword extraction and semantic clustering for AN-04."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .models import SEOConfig


_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)


_STOPWORDS = frozenset(
    "a an and are as at be been being by for from has have he her his i in is it its of on or our that the their them they this to was we were what when where which who why will with you your about after before into over than then there these those how can could should would may might not very more most some such only also just using used use based through during between while do does did each many much own same other another all any both few first last new old one two three four five six seven eight nine ten".split()
)


class KeywordEngine:
    """Extracts deterministic keywords without external NLP dependencies."""

    def __init__(self, *, stopwords: frozenset[str] | None = None) -> None:
        self._stopwords = stopwords or _STOPWORDS

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    def tokens(self, text: str) -> list[str]:
        return [token.casefold() for token in _TOKEN_RE.findall(text) if len(token) > 2]

    def extract(self, text: str, *, limit: int = 20) -> list[str]:
        counts = Counter(
            token for token in self.tokens(text)
            if token not in self._stopwords and not token.isdigit()
        )
        # Stable deterministic ordering: frequency first, then lexical order.
        return [term for term, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]

    def phrases(self, text: str, *, min_words: int = 2, max_words: int = 4, limit: int = 20) -> list[str]:
        tokens = [token for token in self.tokens(text) if token not in self._stopwords]
        counts: Counter[str] = Counter()
        for size in range(min_words, max_words + 1):
            for index in range(0, max(0, len(tokens) - size + 1)):
                phrase = " ".join(tokens[index:index + size])
                if len(set(tokens[index:index + size])) == 1:
                    continue
                counts[phrase] += 1
        return [term for term, _ in sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))[:limit]]

    def deduplicate(self, values: list[str], *, limit: int | None = None) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            cleaned = " ".join(value.split()).strip()
            key = self.normalize(cleaned)
            if not cleaned or key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
            if limit is not None and len(result) >= limit:
                break
        return result

    def build(self, text: str, config: SEOConfig) -> tuple[list[str], list[str], list[str], dict[str, list[str]]]:
        primary_candidates = self.extract(text, limit=max(config.max_primary_keywords * 3, 10))
        phrases = self.phrases(text, min_words=2, max_words=3, limit=30)
        primary = self.deduplicate(phrases + primary_candidates, limit=config.max_primary_keywords)

        secondary = self.deduplicate(
            [phrase for phrase in phrases if phrase not in primary] + primary_candidates,
            limit=config.max_secondary_keywords,
        )
        long_tail = self.deduplicate(
            self.phrases(text, min_words=3, max_words=5, limit=40),
            limit=config.max_long_tail_keywords,
        )

        clusters: dict[str, list[str]] = defaultdict(list)
        for keyword in self.deduplicate(primary + secondary + long_tail):
            head = self.tokens(keyword)[0] if self.tokens(keyword) else keyword
            clusters[head].append(keyword)
        ordered = sorted(clusters.items(), key=lambda item: (-len(item[1]), item[0]))[:config.max_semantic_clusters]
        return primary, secondary, long_tail, {key: self.deduplicate(value) for key, value in ordered}
