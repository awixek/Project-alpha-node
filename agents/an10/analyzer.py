from __future__ import annotations

import re
from collections import Counter

from agents.an03.models import ScriptDocument
from agents.an04.models import SEOResult
from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an09.models import VideoPackage
from .models import VisualAnalysis


class ThumbnailAnalyzer:
    """Extracts only evidence-supported visual signals from completed production."""

    def analyze(self, video: VideoPackage, vision: VisionPlan, assets: AssetPackage,
                script: ScriptDocument, seo: SEOResult | None = None) -> VisualAnalysis:
        scenes = vision.scenes
        if not scenes:
            raise ValueError("VisionPlan contains no scenes.")
        dominant = self._dominant_subject(scenes)
        emotional = next((s.character_emotion for s in scenes if s.character_emotion), None) or "focused, purposeful emotion"
        educational = next((s.visual_goal for s in scenes if s.visual_goal), scenes[0].narrative_goal)
        curiosity = next((s.subject for s in scenes if s.subject), scenes[0].visual_goal)
        negative = self._negative_space(scenes)
        focal = self._focal_path(scenes)
        palette = 82.0 if any(s.color_palette for s in scenes) else 55.0
        contrast = 84.0 if any("contrast" in s.composition.lower() for s in scenes) else 76.0
        clutter = min(100.0, 25.0 + max(0, len(scenes) - 8) * 4.0)
        mobile = max(55.0, 94.0 - clutter * 0.35)
        evidence = [f"{len(scenes)} planned visual scenes", f"{len(assets.assets)} generated assets", f"video runtime {video.timeline.total_runtime:.2f}s"]
        if seo:
            evidence.append("SEO title/metadata available for factual text alignment")
        return VisualAnalysis(
            dominant_subject=dominant,
            emotional_peak=emotional,
            educational_highlight=educational,
            curiosity_moment=curiosity,
            negative_space=negative,
            focal_path=focal,
            color_harmony=palette,
            contrast=contrast,
            visual_clutter=clutter,
            mobile_visibility=mobile,
            evidence_basis=evidence,
        )

    @staticmethod
    def _dominant_subject(scenes) -> str:
        tokens = []
        for scene in scenes:
            tokens.extend(re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9-]{2,}", scene.subject.lower()))
        if not tokens:
            return scenes[0].subject
        return Counter(tokens).most_common(1)[0][0]

    @staticmethod
    def _negative_space(scenes) -> str:
        for scene in scenes:
            if "negative" in scene.composition.lower():
                return scene.composition
        return "Reserve uncluttered space opposite the focal subject for minimal text overlay."

    @staticmethod
    def _focal_path(scenes) -> str:
        return "Primary subject first, supporting evidence second, restrained text last."
