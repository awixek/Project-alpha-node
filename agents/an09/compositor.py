from __future__ import annotations

from .effects import EffectsEngine
from .models import Timeline, RenderSettings, Transition


class SceneCompositor:
    """Creates a deterministic composition manifest for a rendering backend."""

    def __init__(self, effects: EffectsEngine | None = None) -> None:
        self._effects = effects or EffectsEngine()

    def compose(self, timeline: Timeline, transitions: list[Transition], settings: RenderSettings) -> dict:
        transition_map = {(t.from_scene, t.to_scene): t for t in transitions}
        scenes = []
        for scene in timeline.scenes:
            scenes.append({
                "scene_id": scene.scene_id,
                "start": scene.start_time,
                "end": scene.end_time,
                "assets": [str(value) for value in scene.asset_ids],
                "narration": list(scene.narration_segment_ids),
                "subtitles": list(scene.subtitle_segment_ids),
                "effects": self._effects.effects_for_scene(scene, settings),
                "transition_out": scene.transition_out,
                "transition_to_next": transition_map.get((scene.scene_id, scene.order + 2)).transition_type
                if (scene.scene_id, scene.order + 2) in transition_map else None,
                "overlays": list(scene.overlays),
            })
        return {"timeline": scenes, "total_runtime": timeline.total_runtime}
