"""Deterministic validation of repurposed platform packages."""
from __future__ import annotations

from .models import PlatformDistribution, PlatformProfile, ValidationIssue


class DistributionValidator:
    def validate(self, distribution: PlatformDistribution) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        profile = distribution.profile
        if not distribution.assets:
            issues.append(ValidationIssue(severity="error", code="missing_assets", message="No transformed asset was produced.", recommendation="Provide a valid source asset or transformation rule."))
        if not distribution.metadata.title.strip():
            issues.append(ValidationIssue(severity="error", code="missing_title", message="Platform title is empty."))
        if profile.max_title_chars and len(distribution.metadata.title) > profile.max_title_chars:
            issues.append(ValidationIssue(severity="error", code="title_limit", message="Title exceeds platform profile limit.", field="metadata.title"))
        if profile.max_text_chars and len(distribution.metadata.description) > profile.max_text_chars:
            issues.append(ValidationIssue(severity="error", code="text_limit", message="Description exceeds platform profile limit.", field="metadata.description"))
        if len(distribution.metadata.hashtags) > profile.max_hashtags:
            issues.append(ValidationIssue(severity="error", code="hashtag_limit", message="Hashtag count exceeds platform profile limit.", field="metadata.hashtags"))
        if profile.max_duration_seconds:
            for asset in distribution.assets:
                if asset.duration_seconds and asset.duration_seconds > profile.max_duration_seconds:
                    issues.append(ValidationIssue(severity="error", code="duration_limit", message="Transformed duration exceeds platform profile limit.", field="assets.duration_seconds"))
        seen: set[tuple[str, str]] = set()
        for asset in distribution.assets:
            key = (asset.platform.value, asset.body.casefold())
            if key in seen:
                issues.append(ValidationIssue(severity="warning", code="duplicate_asset", message="Duplicate transformed content detected.", field="assets"))
            seen.add(key)
        if not distribution.metadata.description and distribution.assets[0].body:
            issues.append(ValidationIssue(severity="warning", code="empty_description", message="Metadata description is empty despite available source text."))
        return issues

    @staticmethod
    def status(issues: list[ValidationIssue]) -> str:
        return "failed" if any(i.severity == "error" for i in issues) else ("warning" if issues else "ready")
