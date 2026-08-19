"""Deterministic planning, evidence normalization, and quality validation for AN-03."""
from __future__ import annotations

from collections import OrderedDict

from shared.constants import AgentID
from shared.exceptions import InputValidationError
from shared.schemas import SourceRef

from .models import (
    CitationMode,
    ScriptDocument,
    ScriptGenerationConfig,
    ScriptOutline,
    ScriptRequest,
    ScriptSection,
    SectionType,
)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


class ScriptPlanner:
    """Builds a deterministic narrative outline from verified inputs."""

    def build_outline(self, request: ScriptRequest, config: ScriptGenerationConfig) -> ScriptOutline:
        candidates = self._deduplicate_candidates(request)
        if not candidates:
            raise InputValidationError(
                "AN-03 requires at least one research candidate.",
                agent_id=AgentID.SCRIPT_FORGE,
                mission_id=request.mission_id,
                context={"operation": "build_outline"},
            )
        primary = candidates[0]
        thesis = primary.summary.strip() or primary.title.strip()
        return ScriptOutline(
            title=primary.title,
            thesis=thesis,
            sections=list(config.section_order),
            audience="general",
            style=config.style,
        )

    @staticmethod
    def _deduplicate_candidates(request: ScriptRequest):
        seen: set[tuple[str, str]] = set()
        result = []
        for candidate in request.research.candidates:
            key = (_normalize(candidate.title), _normalize(candidate.summary))
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result


class EvidenceLinker:
    """Collects and deduplicates evidence from AN-01 and AN-02."""

    def collect_sources(self, request: ScriptRequest) -> list[SourceRef]:
        sources: OrderedDict[str, SourceRef] = OrderedDict()
        for candidate in request.research.candidates:
            for source in candidate.sources:
                sources.setdefault(source.url.strip(), source)
        for claim in request.fact_check.claims:
            for source in claim.supporting_sources:
                sources.setdefault(source.url.strip(), source)
            for source in claim.conflicting_sources:
                sources.setdefault(source.url.strip(), source)
        return list(sources.values())

    def evidence_context(self, request: ScriptRequest, *, citation_mode: CitationMode) -> str:
        lines: list[str] = []
        for candidate in request.research.candidates:
            lines.append(f"RESEARCH: {candidate.title} — {candidate.summary}")
        for claim in request.fact_check.claims:
            if claim.verdict.value == "verified_true" or claim.verdict.value == "partially_true":
                lines.append(f"VERIFIED CLAIM: {claim.claim} | confidence={claim.confidence:.2f}")
                for source in claim.supporting_sources:
                    lines.append(f"SOURCE: {source.title or source.url} | {source.url}")
            else:
                lines.append(f"CAUTION: {claim.claim} | verdict={claim.verdict.value}")
        if citation_mode is CitationMode.NONE:
            return "\n".join(lines)
        return "\n".join(lines)


class ScriptQualityValidator:
    """Validates structural and evidence-preservation invariants."""

    WORDS_PER_MINUTE = 130

    def validate(
        self,
        document: ScriptDocument,
        request: ScriptRequest,
        config: ScriptGenerationConfig,
    ) -> ScriptDocument:
        errors: list[str] = []
        if not document.sections:
            errors.append("Script contains no sections.")
        narration_length = sum(len(section.narration) for section in document.sections)
        if narration_length > config.max_length:
            errors.append("Narration exceeds configured maximum length.")
        expected = list(config.section_order)
        actual = [section.section_type for section in document.sections]
        if actual != expected:
            errors.append("Generated section order does not match configured section order.")
        if request.fact_check.manual_review_required:
            errors.append("AN-02 requires manual review; script is not fully fact-cleared.")
        if not document.evidence_sources and request.fact_check.claims:
            errors.append("Fact-checked claims exist but no evidence sources were preserved.")

        word_count = sum(len(section.narration.split()) for section in document.sections)
        estimated = word_count / self.WORDS_PER_MINUTE * 60
        metadata = document.metadata.model_copy(update={"word_count": word_count, "estimated_duration_seconds": estimated})
        quality = 1.0
        if errors:
            quality -= min(0.7, 0.15 * len(errors))
        if request.fact_check.overall_pass:
            quality = min(1.0, quality + 0.05)
        document = document.model_copy(update={
            "metadata": metadata,
            "quality_score": max(0.0, quality),
            "validation_errors": errors,
        })
        return document


class ScriptSectionPlanner:
    """Creates provider-independent section instructions before generation."""

    def expand_instructions(self, outline: ScriptOutline, request: ScriptRequest) -> list[ScriptSection]:
        candidate = request.research.candidates[0]
        base = {
            SectionType.HOOK: f"Open with a concise, accurate hook about {candidate.title}.",
            SectionType.INTRO: f"Introduce the topic and establish the central question: {outline.thesis}",
            SectionType.BACKGROUND: "Provide only background supported by the supplied research.",
            SectionType.MAIN_EXPLANATION: "Explain the main subject clearly, in logical sequence.",
            SectionType.EVIDENCE_BLOCK: "Present verified evidence and preserve source links where citations are enabled.",
            SectionType.HISTORICAL_CONTEXT: "Add historical context only where supported by the research package.",
            SectionType.COUNTERPOINTS: "Present relevant counterpoints and explicitly distinguish them from verified facts.",
            SectionType.CONCLUSION: "Synthesize the verified findings without introducing new unsupported claims.",
            SectionType.CALL_TO_ACTION: "Close with a proportionate, non-misleading call to action.",
        }
        return [
            ScriptSection(order=index, heading=section.value.replace("_", " ").title(), narration=base[section], section_type=section)
            for index, section in enumerate(outline.sections)
        ]
