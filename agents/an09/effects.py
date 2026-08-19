from __future__ import annotations

from .models import RenderSettings, TimelineScene


class EffectsEngine:
    """Translates planner animation hints into backend-neutral effects."""

    def effects_for_scene(self, scene: TimelineScene, settings: RenderSettings) -> list[str]:
        if not scene.motion_effects:
            return []
        intensity = settings.animation_intensity
        return [f"{effect}; intensity={intensity:.2f}" for effect in scene.motion_effects]
