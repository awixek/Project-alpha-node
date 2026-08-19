from __future__ import annotations

from agents.an03.models import ScriptDocument
from agents.an04.models import SEOResult
from agents.an05.models import VisionPlan
from .models import ThumbnailConfig, ThumbnailLayout, ThumbnailStrategy, VisualAnalysis


class ThumbnailPlanner:
    """Creates fact-safe thumbnail concepts from upstream production evidence."""

    def choose_strategies(self, script: ScriptDocument, vision: VisionPlan,
                          seo: SEOResult | None, limit: int) -> list[ThumbnailStrategy]:
        text = " ".join([script.title, *(s.heading or "" for s in script.sections)]).lower()
        strategies: list[ThumbnailStrategy] = []
        if any(word in text for word in ("history", "historical", "ancient", "medieval")):
            strategies.extend([ThumbnailStrategy.HISTORICAL, ThumbnailStrategy.DOCUMENTARY])
        if "?" in script.title or any(word in text for word in ("why", "how", "mystery")):
            strategies.append(ThumbnailStrategy.QUESTION_STYLE)
        if any(word in text for word in ("myth", "fact", "false", "truth")):
            strategies.append(ThumbnailStrategy.MYTH_VS_FACT)
        if any(word in text for word in ("compare", "versus", "vs", "difference")):
            strategies.append(ThumbnailStrategy.COMPARISON)
        strategies.extend([ThumbnailStrategy.EDUCATIONAL, ThumbnailStrategy.CURIOSITY_GAP])
        unique = list(dict.fromkeys(strategies))
        return unique[:limit]

    def build(self, strategy: ThumbnailStrategy, analysis: VisualAnalysis,
              vision: VisionPlan, script: ScriptDocument, config: ThumbnailConfig) -> dict:
        scene = self._best_scene(vision, strategy)
        text = self._overlay(strategy, script.title, analysis, config.max_text_characters)
        return {
            "strategy": strategy,
            "title": f"{strategy.value.replace('_', ' ').title()} concept",
            "focal_subject": scene.subject,
            "emotional_hook": analysis.emotional_peak,
            "text_overlay": text,
            "layout": ThumbnailLayout(
                focal_region="left third" if strategy in {ThumbnailStrategy.QUESTION_STYLE, ThumbnailStrategy.CURIOSITY_GAP} else "center-right",
                text_region="upper-left" if text else "none",
                branding_region="lower-right",
                composition=config.layout_preferences,
                aspect_ratio=config.aspect_ratio,
                text_density=config.text_density,
                visual_hierarchy=["focal subject", "evidence cue", "short text", "subtle brand mark"],
            ),
            "scene_id": scene.scene_number,
            "asset_ids": [a.asset_id for a in getattr(scene, "asset_manifest", [])] if hasattr(scene, "asset_manifest") else [],
        }

    @staticmethod
    def _best_scene(vision: VisionPlan, strategy: ThumbnailStrategy):
        ranked = sorted(vision.scenes, key=lambda s: (s.confidence_score, len(s.subject)), reverse=True)
        if strategy == ThumbnailStrategy.HISTORICAL:
            historical = [s for s in ranked if s.historical_accuracy_notes]
            if historical:
                return historical[0]
        return ranked[0]

    @staticmethod
    def _overlay(strategy, title: str, analysis: VisualAnalysis, limit: int) -> str:
        if strategy == ThumbnailStrategy.QUESTION_STYLE:
            value = title.rstrip("?!") + "?"
        elif strategy == ThumbnailStrategy.MYTH_VS_FACT:
            value = "MYTH OR FACT?"
        elif strategy == ThumbnailStrategy.BEFORE_AFTER:
            value = "THEN → NOW"
        elif strategy == ThumbnailStrategy.COMPARISON:
            value = "WHAT'S THE DIFFERENCE?"
        elif strategy == ThumbnailStrategy.TIMELINE:
            value = "A TIMELINE OF CHANGE"
        else:
            value = analysis.dominant_subject.title()
        return value[:limit].strip()
