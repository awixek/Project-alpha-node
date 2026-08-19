"""Deterministic, explainable research analysis primitives for AN-01."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Iterable

from shared.schemas import SourceRef, SourceReliability

from .models import ProviderSearchItem, ResearchAnalysisConfig, ResearchCandidate

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text) if len(token) > 1}


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return SequenceMatcher(None, left.lower(), right.lower()).ratio()
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union)
    sequence = SequenceMatcher(None, left.lower(), right.lower()).ratio()
    return 0.65 * jaccard + 0.35 * sequence


def _source_key(item: ProviderSearchItem) -> str:
    return item.url.strip().rstrip("/").lower()


def _authority(item: ProviderSearchItem, config: ResearchAnalysisConfig) -> float:
    if item.publisher and item.publisher in config.publisher_authority:
        return max(0.0, min(1.0, config.publisher_authority[item.publisher]))
    reliability = item.reliability.lower()
    return {
        SourceReliability.PRIMARY.value: 1.0,
        SourceReliability.SECONDARY.value: 0.72,
        SourceReliability.UNVERIFIED.value: 0.35,
        SourceReliability.DISPUTED.value: 0.15,
    }.get(reliability, 0.35)


def _freshness(item: ProviderSearchItem, now: datetime, half_life_hours: float) -> float:
    if item.published_at is None:
        return 0.35
    published = item.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
    return math.exp(-math.log(2) * age_hours / half_life_hours)


def _relevance(item: ProviderSearchItem, query_terms: set[str]) -> float:
    item_terms = _tokens(f"{item.title} {item.summary} {' '.join(item.keywords)}")
    if not query_terms or not item_terms:
        return 0.5
    return len(query_terms & item_terms) / len(query_terms)


def _completeness(item: ProviderSearchItem) -> float:
    fields = [bool(item.title.strip()), bool(item.summary.strip()), bool(item.url.strip()), bool(item.publisher)]
    return sum(fields) / len(fields)


@dataclass(slots=True)
class MergedCandidate:
    """Internal merged representation before clustering and scoring."""

    representative: ProviderSearchItem
    items: list[ProviderSearchItem]

    @property
    def providers(self) -> set[str]:
        return {item.provider for item in self.items}


def merge_duplicates(items: Iterable[ProviderSearchItem], config: ResearchAnalysisConfig) -> tuple[list[MergedCandidate], int]:
    """Merge exact URL/title duplicates and near duplicates, preserving sources."""
    groups: list[MergedCandidate] = []
    exact_index: dict[str, MergedCandidate] = {}
    removed = 0

    for item in items:
        key = _source_key(item)
        existing = exact_index.get(key)
        if existing is not None:
            existing.items.append(item)
            removed += 1
            continue

        match = next(
            (group for group in groups if _similarity(item.title, group.representative.title) >= config.near_duplicate_threshold),
            None,
        )
        if match is not None:
            match.items.append(item)
            removed += 1
            exact_index[key] = match
            continue

        group = MergedCandidate(representative=item, items=[item])
        groups.append(group)
        exact_index[key] = group

    for group in groups:
        group.items.sort(
            key=lambda item: (
                _authority(item, config),
                _completeness(item),
                item.published_at or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
    return groups, removed


def cluster_candidates(groups: list[MergedCandidate], config: ResearchAnalysisConfig) -> list[list[MergedCandidate]]:
    """Cluster semantically related candidates using deterministic token similarity."""
    clusters: list[list[MergedCandidate]] = []
    for group in groups:
        placed = False
        for cluster in clusters:
            representative = cluster[0].representative
            if _similarity(group.representative.title, representative.title) >= config.cluster_threshold:
                cluster.append(group)
                placed = True
                break
        if not placed:
            clusters.append([group])
    return clusters


def build_candidates(
    groups: list[MergedCandidate],
    *,
    clusters: list[list[MergedCandidate]],
    mission_id,
    query: str,
    config: ResearchAnalysisConfig,
    now: datetime | None = None,
) -> list[ResearchCandidate]:
    """Score merged candidates and produce explainable ranked outputs."""
    current = now or datetime.now(timezone.utc)
    query_terms = _tokens(query)
    cluster_ids = {id(group): f"cluster-{index:03d}" for index, cluster in enumerate(clusters, start=1) for group in cluster}
    weights = config.weights.normalized()
    candidates: list[ResearchCandidate] = []

    for group in groups:
        representative = group.representative
        sources: list[SourceRef] = []
        seen_urls: set[str] = set()
        for item in group.items:
            url = _source_key(item)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                reliability = SourceReliability(item.reliability.lower())
            except ValueError:
                reliability = SourceReliability.UNVERIFIED
            sources.append(
                SourceRef(
                    url=item.url,
                    title=item.title,
                    publisher=item.publisher,
                    published_at=item.published_at,
                    reliability=reliability,
                    retrieved_at=current,
                )
            )

        provider_count = len(group.providers)
        source_count = len(sources)
        freshness = max(_freshness(item, current, config.freshness_half_life_hours) for item in group.items)
        authority = sum(_authority(item, config) for item in group.items) / len(group.items)
        relevance = max(_relevance(item, query_terms) for item in group.items)
        completeness = max(_completeness(item) for item in group.items)
        confirmation = min(1.0, provider_count / 3.0) if provider_count else 0.0
        diversity = min(1.0, source_count / 4.0)
        confidence = min(
            1.0,
            0.35 * confirmation + 0.30 * authority + 0.20 * completeness + 0.15 * relevance,
        )
        breakdown = {
            "freshness": freshness * weights.freshness,
            "authority": authority * weights.authority,
            "cross_source_confirmation": confirmation * weights.cross_source_confirmation,
            "relevance": relevance * weights.relevance,
            "information_completeness": completeness * weights.information_completeness,
            "source_diversity": diversity * weights.source_diversity,
            "confidence": confidence * weights.confidence,
        }
        overall = sum(breakdown.values())
        candidates.append(
            ResearchCandidate(
                mission_id=mission_id,
                title=representative.title,
                summary=representative.summary,
                sources=sources,
                confidence_score=confidence,
                freshness_score=freshness,
                authority_score=authority,
                relevance_score=relevance,
                information_completeness=completeness,
                cross_source_confirmation=confirmation,
                source_diversity=diversity,
                overall_priority_score=overall,
                discovery_timestamp=current,
                cluster_id=cluster_ids[id(group)],
                supporting_providers=sorted(group.providers),
                score_breakdown=breakdown,
            )
        )

    candidates.sort(key=lambda candidate: candidate.overall_priority_score, reverse=True)
    return candidates[: config.max_candidates]
