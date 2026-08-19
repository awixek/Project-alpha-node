"""Deterministic claim extraction, conflict detection, and scoring for AN-02."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Iterable

from shared.schemas import FactVerdict, SourceRef, SourceReliability

from .models import (
    ClaimType,
    EvidenceItem,
    FactAnalysisConfig,
    VerificationStatus,
    VerifiedClaim,
)

_SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")
_YEAR_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2}|21\d{2})\b")
_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*%?\b")
_QUOTE_RE = re.compile(r'(["“”][^"“”]{4,}["“”])')
_OPINION_RE = re.compile(r"\b(i think|i believe|in my opinion|arguably|seems|appears|may|might|could)\b", re.I)
_PREDICTION_RE = re.compile(r"\b(will|expected to|forecast|predict|predicted|likely to|future)\b", re.I)
_DATE_RE = re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:,\s*|\s+)\d{4}\b", re.I)
_NEGATION_RE = re.compile(r"\b(no|not|never|none|without|false|did not|does not|cannot|can't|isn't|wasn't|weren't|won't)\b", re.I)


def _tokens(text: str) -> set[str]:
    return {x.lower() for x in re.findall(r"[\w]+", text, re.UNICODE) if len(x) > 1}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()
    jaccard = len(ta & tb) / len(ta | tb)
    sequence = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return 0.65 * jaccard + 0.35 * sequence


def _source_key(source: SourceRef) -> str:
    return source.url.strip().rstrip("/").lower()


def classify_claim(claim: str) -> ClaimType:
    """Classify a claim using deterministic lexical rules."""
    if _OPINION_RE.search(claim):
        return ClaimType.OPINION
    if _PREDICTION_RE.search(claim):
        return ClaimType.PREDICTION
    if _QUOTE_RE.search(claim):
        return ClaimType.QUOTE
    if _DATE_RE.search(claim) or (_YEAR_RE.search(claim) and not _NUMBER_RE.search(claim.replace(_YEAR_RE.search(claim).group(), ""))):
        return ClaimType.DATE
    if _NUMBER_RE.search(claim):
        return ClaimType.STATISTIC
    return ClaimType.FACT


def _text_from_research(research: Any) -> tuple[list[str], list[SourceRef], Any]:
    """Extract text without requiring AN-01 schema changes."""
    claims: list[str] = []
    sources: list[SourceRef] = []
    research_id = getattr(research, "research_id", None)

    candidates = getattr(research, "candidates", None)
    if candidates is not None:
        for candidate in candidates:
            title = getattr(candidate, "title", "")
            summary = getattr(candidate, "summary", "")
            for text in (title, summary):
                if text:
                    claims.extend(_split_claims(text))
            sources.extend(getattr(candidate, "sources", []))
    else:
        summary = getattr(research, "summary", "") or ""
        claims.extend(_split_claims(summary))
        for point in getattr(research, "key_points", []) or []:
            claims.extend(_split_claims(point))
        sources.extend(getattr(research, "sources", []) or [])

    # Accept dict payloads as a defensive integration boundary.
    if isinstance(research, dict):
        research_id = research.get("research_id")
        candidates = research.get("candidates")
        if candidates:
            for candidate in candidates:
                title = candidate.get("title", "")
                summary = candidate.get("summary", "")
                claims.extend(_split_claims(f"{title}. {summary}"))
                sources.extend(candidate.get("sources", []))
        else:
            claims.extend(_split_claims(str(research.get("summary", ""))))
            claims.extend(_split_claims(" ".join(map(str, research.get("key_points", [])))))
            sources.extend(research.get("sources", []))

    normalized_sources = []
    for source in sources:
        if isinstance(source, SourceRef):
            normalized_sources.append(source)
        elif isinstance(source, dict) and source.get("url"):
            try:
                normalized_sources.append(SourceRef(**source))
            except Exception:
                continue

    unique_claims: list[str] = []
    for claim in claims:
        clean = re.sub(r"\s+", " ", claim).strip(" -")
        if len(clean) < 12:
            continue
        if not any(_similarity(clean, existing) >= 0.96 for existing in unique_claims):
            unique_claims.append(clean)
    return unique_claims, normalized_sources, research_id


def _split_claims(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()]


def extract_claims(research: Any, *, max_claims: int) -> tuple[list[str], list[SourceRef], Any]:
    claims, sources, research_id = _text_from_research(research)
    return claims[:max_claims], sources, research_id


def _authority(source: SourceRef) -> float:
    return {
        SourceReliability.PRIMARY: 1.0,
        SourceReliability.SECONDARY: 0.72,
        SourceReliability.UNVERIFIED: 0.35,
        SourceReliability.DISPUTED: 0.15,
    }.get(source.reliability, 0.35)


def _is_official(source: SourceRef) -> bool:
    url = source.url.lower()
    publisher = (source.publisher or "").lower()
    return (
        ".gov" in url
        or ".gov." in url
        or ".edu" in url
        or ".ac." in url
        or "official" in publisher
    )


def _freshness(source: SourceRef, now: datetime, half_life: float) -> float:
    if source.published_at is None:
        return 0.35
    published = source.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published).total_seconds() / 3600)
    return math.exp(-math.log(2) * age_hours / half_life)


def _citation_quality(source: SourceRef) -> float:
    fields = [bool(source.url), bool(source.title), bool(source.publisher), source.published_at is not None]
    return sum(fields) / len(fields)


def _independent_sources(evidence: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    result: list[EvidenceItem] = []
    seen: set[str] = set()
    for item in evidence:
        key = _source_key(item.source)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _contradiction(evidence: list[EvidenceItem]) -> tuple[float, list[EvidenceItem], list[str]]:
    """Detect numerical/date/polarity conflicts without silently discarding evidence."""
    if len(evidence) < 2:
        return 0.0, [], []

    notes: list[str] = []
    conflicts: list[EvidenceItem] = []

    values: dict[str, list[EvidenceItem]] = {}
    dates: dict[str, list[EvidenceItem]] = {}
    polarity: dict[bool, list[EvidenceItem]] = {True: [], False: []}

    for item in evidence:
        statement = item.evidence_statement or item.excerpt
        for value in _NUMBER_RE.findall(statement):
            values.setdefault(value.replace(",", ""), []).append(item)
        for year in _YEAR_RE.findall(statement):
            dates.setdefault(year, []).append(item)
        if item.supports_claim is not None:
            polarity[item.supports_claim].append(item)

    if len(values) > 1:
        conflicts.extend(item for items in values.values() for item in items)
        notes.append("Independent evidence reports different numerical values.")
    if len(dates) > 1:
        conflicts.extend(item for items in dates.values() for item in items)
        notes.append("Independent evidence reports different dates or years.")
    if polarity[True] and polarity[False]:
        conflicts.extend(polarity[True] + polarity[False])
        notes.append("Independent evidence contains contradictory support signals.")

    unique_conflicts = []
    seen = set()
    for item in conflicts:
        key = _source_key(item.source)
        if key not in seen:
            seen.add(key)
            unique_conflicts.append(item)

    severity = min(1.0, 0.45 * bool(values and len(values) > 1) +
                       0.35 * bool(dates and len(dates) > 1) +
                       0.60 * bool(polarity[True] and polarity[False]))
    return severity, unique_conflicts, notes


def verify_claim(
    claim: str,
    evidence: list[EvidenceItem],
    *,
    config: FactAnalysisConfig,
    now: datetime | None = None,
) -> VerifiedClaim:
    """Produce one deterministic, explainable verification decision."""
    current = now or datetime.now(timezone.utc)
    claim_type = classify_claim(claim)
    independent = _independent_sources(evidence)
    supporting = [item for item in independent if item.supports_claim is True]
    conflicting = [item for item in independent if item.supports_claim is False]
    contradiction_severity, contradiction_items, contradiction_notes = _contradiction(independent)

    authority = (
        sum(_authority(item.source) for item in independent) / len(independent)
        if independent else 0.0
    )
    confirmations = min(1.0, len(supporting) / 3.0)
    consistency = 1.0 - contradiction_severity
    freshness = (
        sum(_freshness(item.source, current, config.freshness_half_life_hours) for item in independent)
        / len(independent)
        if independent else 0.0
    )
    citation = (
        sum(_citation_quality(item.source) for item in independent) / len(independent)
        if independent else 0.0
    )
    official = 1.0 if any(_is_official(item.source) for item in independent) else 0.0
    contradiction_component = 1.0 - contradiction_severity

    weights = config.weights.normalized()
    breakdown = {
        "source_authority": authority * weights.source_authority,
        "independent_confirmations": confirmations * weights.independent_confirmations,
        "evidence_consistency": consistency * weights.evidence_consistency,
        "freshness": freshness * weights.freshness,
        "citation_quality": citation * weights.citation_quality,
        "official_source": official * weights.official_source,
        "contradiction_severity": contradiction_component * weights.contradiction_severity,
    }
    reliability = max(0.0, min(1.0, sum(breakdown.values())))

    if claim_type in {ClaimType.OPINION, ClaimType.PREDICTION}:
        status = VerificationStatus.OPINION
        verdict = FactVerdict.OPINION
        confidence = 1.0
        manual_review = False
    elif not independent:
        status = VerificationStatus.UNSUPPORTED
        verdict = FactVerdict.UNVERIFIABLE
        confidence = 0.0
        manual_review = True
    elif conflicting and supporting:
        status = VerificationStatus.CONTRADICTED
        verdict = FactVerdict.PARTIALLY_TRUE
        confidence = reliability * 0.75
        manual_review = config.manual_review_on_conflict
    elif conflicting and not supporting:
        status = VerificationStatus.CONTRADICTED
        verdict = FactVerdict.VERIFIED_FALSE
        confidence = reliability * 0.80
        manual_review = config.manual_review_on_conflict
    elif len(independent) == 1:
        status = VerificationStatus.PARTIALLY_VERIFIED
        verdict = FactVerdict.PARTIALLY_TRUE
        confidence = reliability * 0.75
        manual_review = config.manual_review_on_single_source
    elif len(supporting) >= 2:
        status = VerificationStatus.VERIFIED
        verdict = FactVerdict.VERIFIED_TRUE
        confidence = reliability
        manual_review = False
    else:
        status = VerificationStatus.UNVERIFIABLE
        verdict = FactVerdict.UNVERIFIABLE
        confidence = reliability * 0.60
        manual_review = True

    stale = bool(independent) and freshness < 0.15 and claim_type not in {ClaimType.OPINION, ClaimType.PREDICTION}
    if stale and status == VerificationStatus.VERIFIED:
        status = VerificationStatus.OUTDATED
        manual_review = True

    notes = list(contradiction_notes)
    if not independent:
        notes.append("No independent evidence was returned.")
    if len(independent) == 1:
        notes.append("Only one independent source was available.")
    if stale:
        notes.append("Available evidence is materially old relative to the configured freshness window.")
    if not official:
        notes.append("No official or academic-style source was identified.")
    if conflicting:
        notes.append("Conflicting evidence has been preserved for downstream review.")

    evidence_summary = (
        f"{len(supporting)} supporting source(s), {len(conflicting)} conflicting source(s), "
        f"{len(independent)} independent source(s)."
    )
    supporting_refs = [item.source for item in supporting]
    conflicting_refs = [item.source for item in contradiction_items]
    return VerifiedClaim(
        claim=claim,
        verdict=verdict,
        confidence=max(0.0, min(1.0, confidence)),
        supporting_sources=supporting_refs,
        notes=" ".join(notes),
        claim_type=claim_type,
        verification_status=status,
        conflicting_sources=conflicting_refs,
        evidence_summary=evidence_summary,
        verification_notes=notes,
        verification_timestamp=current,
        reliability_score=reliability,
        manual_review_required=manual_review or confidence < config.min_verification_confidence,
        evidence_quality=(authority + citation + consistency) / 3.0 if independent else 0.0,
        independent_confirmations=len(supporting),
        contradiction_severity=contradiction_severity,
    )
