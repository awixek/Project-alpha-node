from __future__ import annotations

from .models import CTRScore, ThumbnailConcept, ThumbnailConfig, ThumbnailLayout, VisualAnalysis


class CTRScorer:
    """Deterministic, explainable CTR scoring engine."""

    def score(self, analysis: VisualAnalysis, layout: ThumbnailLayout,
              strategy: str, text: str | None, config: ThumbnailConfig) -> CTRScore:
        curiosity = 90.0 if strategy in {"curiosity_gap", "question_style", "myth_vs_fact"} else 76.0
        readability = self._readability(text, config.max_text_characters)
        contrast = analysis.contrast
        composition = max(0.0, 100.0 - analysis.visual_clutter * 0.55)
        branding = 90.0 if config.branding.lower() in {"subtle", "minimal"} else 76.0
        mobile = analysis.mobile_visibility if len(text or "") <= config.max_text_characters else 55.0
        confidence = max(0.0, min(100.0, analysis.color_harmony * 0.3 + (100 - analysis.visual_clutter) * 0.35 + analysis.contrast * 0.35))
        values = {
            "curiosity": curiosity, "readability": readability, "contrast": contrast,
            "composition": composition, "branding": branding, "mobile_visibility": mobile,
            "confidence": confidence,
        }
        weights = dict(config.ctr_weights)
        total_weight = sum(weights.values()) or 1.0
        overall = sum(values[k] * weights.get(k, 0.0) for k in values) / total_weight
        reason = self._reason(values, overall)
        return CTRScore(
            overall=round(overall, 2), curiosity=curiosity, readability=readability,
            contrast=contrast, composition=composition, branding=branding,
            mobile_visibility=mobile, confidence=confidence,
            recommendation_reason=reason, score_breakdown={k: round(v, 2) for k, v in values.items()},
        )

    @staticmethod
    def _readability(text: str | None, limit: int) -> float:
        if not text:
            return 78.0
        n = len(text)
        if n <= min(24, limit):
            return 96.0
        if n <= limit:
            return 86.0
        return max(35.0, 100.0 - (n - limit) * 2.5)

    @staticmethod
    def _reason(values: dict[str, float], overall: float) -> str:
        strongest = max(values, key=values.get)
        weakest = min(values, key=values.get)
        return f"Score {overall:.1f}/100: strongest factor is {strongest}; improve {weakest} if more optimization is required."
