"""Storyboard construction for AN-05."""
from __future__ import annotations

from .models import Storyboard, TransitionType, VisionScene


class StoryboardEngine:
    """Creates deterministic sequence, timing, transition and pacing data."""

    def build(self, scenes: list[VisionScene]) -> Storyboard:
        durations = [scene.duration_seconds or 0.0 for scene in scenes]
        transitions = [scene.transition_type for scene in scenes]
        rhythm = [scene.mood for scene in scenes]
        if not scenes:
            pacing = "empty"
        elif len(scenes) <= 3:
            pacing = "measured"
        elif len(scenes) <= 8:
            pacing = "balanced"
        else:
            pacing = "dynamic"
        return Storyboard(
            sequence=[scene.scene_number for scene in scenes],
            timing=durations,
            transitions=transitions,
            pacing=pacing,
            emotional_rhythm=rhythm,
        )
