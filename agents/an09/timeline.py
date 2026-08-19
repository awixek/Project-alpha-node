from __future__ import annotations

from agents.an05.models import VisionPlan
from agents.an06.models import AssetPackage
from agents.an07.models import VoicePackage
from agents.an08.models import SubtitlePackage
from shared.exceptions import ValidationError
from .models import Timeline, TimelineScene, RenderSettings


class TimelineBuilder:
    """Builds an editable master timeline without rendering media."""

    def build(self, vision: VisionPlan, assets: AssetPackage, voice: VoicePackage,
              subtitles: SubtitlePackage, settings: RenderSettings) -> Timeline:
        asset_by_scene: dict[int, list] = {}
        for asset in assets.assets:
            asset_by_scene.setdefault(asset.scene_id, []).append(asset)

        narration_by_scene: dict[int, list[str]] = {}
        for segment in voice.narration_segments:
            scene_id = self._scene_from_segment(segment.section_id, vision)
            narration_by_scene.setdefault(scene_id, []).append(segment.segment_id)

        subtitle_by_scene: dict[int, list[str]] = {}
        for track in subtitles.subtitle_tracks[:1]:
            for segment in track.segments:
                try:
                    scene_id = int(segment.scene_id)
                except ValueError:
                    scene_id = self._scene_from_segment(segment.scene_id, vision)
                subtitle_by_scene.setdefault(scene_id, []).append(segment.subtitle_id)

        scenes: list[TimelineScene] = []
        cursor = 0.0
        for index, scene in enumerate(sorted(vision.scenes, key=lambda s: s.scene_number)):
            duration = scene.duration_seconds or 0.0
            if duration <= 0:
                duration = self._duration_from_voice(scene.scene_number, voice) or 1.0
            duration = min(duration, max(duration, settings.timeout / settings.timeout * duration))
            transition = scene.transition_type.value
            scenes.append(TimelineScene(
                scene_id=scene.scene_number,
                order=index,
                start_time=cursor,
                end_time=cursor + duration,
                duration=duration,
                asset_ids=[a.asset_id for a in asset_by_scene.get(scene.scene_number, [])],
                narration_segment_ids=narration_by_scene.get(scene.scene_number, []),
                subtitle_segment_ids=subtitle_by_scene.get(scene.scene_number, []),
                transition_in=transition if index else "cut",
                transition_out=transition,
                motion_effects=[scene.animation_suggestion] if scene.animation_suggestion else [],
                overlays=scene.overlay_recommendations,
                background_music_placeholder=scene.music_mood or None,
                sound_effect_placeholders=[scene.sound_suggestion] if scene.sound_suggestion else [],
            ))
            cursor += duration

        if not scenes:
            raise ValidationError("VisionPlan contains no scenes for video composition.")
        return Timeline(mission_id=vision.mission_id, scenes=scenes, total_runtime=cursor)

    @staticmethod
    def _duration_from_voice(scene_number: int, voice: VoicePackage) -> float:
        durations = []
        for segment in voice.narration_segments:
            if segment.section_id == f"scene-{scene_number}" or segment.section_id.endswith(str(scene_number)):
                durations.append(segment.duration)
        return sum(durations)

    @staticmethod
    def _scene_from_segment(section_id: str, vision: VisionPlan) -> int:
        for scene in vision.scenes:
            if str(scene.script_section_order) == section_id:
                return scene.scene_number
        return vision.scenes[0].scene_number if vision.scenes else 1
