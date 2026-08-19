"""Deterministic scene and prompt planning for AN-05."""
from __future__ import annotations

import re
from typing import Iterable

from agents.an03.models import ScriptDocument, ScriptSection
from shared.constants import AgentID
from shared.exceptions import InputValidationError

from .models import (
    CameraAngle,
    CameraMovement,
    ShotType,
    TransitionType,
    VisionPlanningConfig,
    VisionScene,
    VisionPlannerDefaults,
)


class ScenePlanner:
    """Maps script sections into provider-neutral cinematic scene briefs."""

    _SHOT_BY_SECTION = {
        "hook": ShotType.CLOSE_UP,
        "intro": ShotType.ESTABLISHING,
        "background": ShotType.WIDE,
        "main_explanation": ShotType.MEDIUM,
        "evidence_block": ShotType.STATIC,
        "historical_context": ShotType.WIDE,
        "counterpoints": ShotType.OVER_THE_SHOULDER,
        "conclusion": ShotType.MEDIUM,
        "call_to_action": ShotType.CLOSE_UP,
    }

    def __init__(self, *, defaults: VisionPlannerDefaults | None = None) -> None:
        self._defaults = defaults or VisionPlannerDefaults()

    def build_scenes(self, script: ScriptDocument, config: VisionPlanningConfig) -> list[VisionScene]:
        if not script.sections:
            raise InputValidationError(
                "AN-05 requires a script containing at least one section.",
                agent_id=AgentID.VISION_PLANNER,
                mission_id=script.mission_id,
                context={"operation": "build_scenes"},
            )
        durations = self._allocate_durations(script, config)
        scenes: list[VisionScene] = []
        previous_environment: str | None = None
        for index, (section, duration) in enumerate(zip(script.sections, durations), start=1):
            scenes.append(self._scene_for_section(section, index, duration, config, previous_environment))
            previous_environment = scenes[-1].environment
        return scenes

    def _allocate_durations(self, script: ScriptDocument, config: VisionPlanningConfig) -> list[float]:
        requested = [section.estimated_duration_seconds for section in script.sections]
        if all(value is not None and value > 0 for value in requested):
            values = [min(float(value), config.maximum_scene_duration_seconds) for value in requested]
        else:
            values = [self._defaults.scene_duration_seconds for _ in script.sections]
        if config.target_total_duration_seconds:
            total = sum(values)
            if total > 0:
                scale = config.target_total_duration_seconds / total
                values = [min(config.maximum_scene_duration_seconds, max(1.0, value * scale)) for value in values]
        return values

    def _scene_for_section(
        self,
        section: ScriptSection,
        number: int,
        duration: float,
        config: VisionPlanningConfig,
        previous_environment: str | None,
    ) -> VisionScene:
        section_key = section.section_type.value
        shot = self._SHOT_BY_SECTION.get(section_key, ShotType.MEDIUM)
        movement = {
            ShotType.CLOSE_UP: CameraMovement.SLOW_PUSH,
            ShotType.ESTABLISHING: CameraMovement.PAN,
            ShotType.WIDE: CameraMovement.SLOW_PUSH,
            ShotType.TRACKING: CameraMovement.TRACK,
        }.get(shot, CameraMovement.STATIC)
        angle = CameraAngle.EYE_LEVEL
        environment = self._environment(section)
        uncertainty: list[str] = []
        if environment.startswith("unspecified"):
            uncertainty.append("Environment was not explicitly specified by the script.")
        characters = self._characters(section)
        if not characters:
            uncertainty.append("No explicit character identity was supplied; no character identity was invented.")
        visual_goal = section.visual_notes or f"Visually communicate the verified narrative purpose of the {section_key} section."
        subject = section.heading or section.narration[:120].strip()
        historical_notes = [
            "Use only historical details explicitly supported by the supplied script and references.",
        ]
        if section_key == "historical_context":
            historical_notes.append("Historical period, architecture, clothing and objects remain unspecified unless verified.")
        image_prompt = self._image_prompt(subject, visual_goal, environment, config, characters)
        video_prompt = self._video_prompt(subject, visual_goal, shot, movement, duration, config)
        transition = config.transition_style if number == 1 else config.transition_style
        return VisionScene(
            order=number - 1,
            script_section_order=section.order,
            prompt=image_prompt,
            style_notes=f"Style={config.preferred_style.value}; realism={config.realism_level}; {config.camera_preference}.",
            duration_seconds=duration,
            scene_number=number,
            narrative_goal=section.narration[:240].strip(),
            visual_goal=visual_goal,
            camera_type=shot,
            camera_angle=angle,
            camera_movement=movement,
            subject=subject or "verified script subject",
            characters=characters,
            character_description="Unspecified unless explicitly supplied in the script.",
            character_emotion=self._emotion(section),
            character_pose="Natural, context-appropriate pose; do not infer historical specifics.",
            costume_description="Unspecified unless verified by the source material.",
            environment=environment,
            historical_accuracy_notes=historical_notes,
            architecture_style="Unspecified unless verified.",
            objects=self._objects(section),
            lighting="Naturalistic cinematic lighting consistent with the stated context; do not invent time-specific lighting facts.",
            time_of_day="unspecified",
            weather="unspecified",
            mood=self._mood(section),
            color_palette=config.color_theme,
            composition="Clear subject hierarchy, readable foreground/midground/background separation.",
            depth="Layered cinematic depth with physically plausible spatial relationships.",
            lens_suggestion="35mm equivalent for balanced cinematic perspective.",
            animation_suggestion=self._animation(section),
            transition_type=transition,
            on_screen_text=section.on_screen_text,
            sound_suggestion="Use narration-aligned ambient sound without introducing unsupported events.",
            music_mood=self._mood(section),
            image_prompt=image_prompt,
            negative_prompt="No invented historical facts, no anachronistic objects, no inconsistent characters, no text artifacts, no distorted anatomy, no unsupported geography.",
            video_prompt=video_prompt,
            asset_reuse_hint="Reuse matching environment/character reference assets from continuity package when available.",
            continuity_notes=["Preserve all established visual attributes from earlier scenes." if number > 1 else "Establish only attributes supported by the source material."],
            confidence_score=0.75 if not uncertainty else 0.55,
            uncertainty_notes=uncertainty,
            b_roll_recommendations=self._b_roll(section),
            overlay_recommendations=[section.on_screen_text] if section.on_screen_text else [],
            map_diagram_recommendations=self._diagrams(section),
        )

    @staticmethod
    def _environment(section: ScriptSection) -> str:
        notes = section.visual_notes or ""
        match = re.search(r"environment\s*:\s*([^.;]+)", notes, flags=re.IGNORECASE)
        return match.group(1).strip() if match else "unspecified environment; use only verified context"

    @staticmethod
    def _characters(section: ScriptSection) -> list[str]:
        notes = section.visual_notes or ""
        matches = re.findall(r"(?:character|person)\s*:\s*([A-Za-z][A-Za-z .'-]{1,80})", notes, flags=re.IGNORECASE)
        return list(dict.fromkeys(" ".join(item.split()).strip(" .,-") for item in matches))

    @staticmethod
    def _emotion(section: ScriptSection) -> str:
        text = f"{section.heading or ''} {section.narration}".casefold()
        for key, value in (("conflict", "tense"), ("challenge", "concerned"), ("discovery", "curious"), ("conclusion", "resolved")):
            if key in text:
                return value
        return "context-appropriate"

    @staticmethod
    def _mood(section: ScriptSection) -> str:
        text = f"{section.heading or ''} {section.narration}".casefold()
        if any(word in text for word in ("warning", "conflict", "controversy")):
            return "tense, serious"
        if any(word in text for word in ("discovery", "reveals", "breakthrough")):
            return "curious, revealing"
        return "focused, informative"

    @staticmethod
    def _objects(section: ScriptSection) -> list[str]:
        notes = section.visual_notes or ""
        match = re.search(r"objects?\s*:\s*([^.;]+)", notes, flags=re.IGNORECASE)
        if not match:
            return []
        return [item.strip() for item in match.group(1).split(",") if item.strip()]

    @staticmethod
    def _animation(section: ScriptSection) -> str:
        section_type = section.section_type.value
        if section_type in {"evidence_block", "main_explanation"}:
            return "Subtle diagram, chart, map or label animation only when supported by the evidence."
        return "Subtle camera or environmental motion; avoid gratuitous animation."

    @staticmethod
    def _b_roll(section: ScriptSection) -> list[str]:
        if section.section_type.value in {"background", "historical_context", "evidence_block"}:
            return ["Source-supported archival/context imagery", "Relevant maps, diagrams or documents when explicitly supported"]
        return ["Contextual detail shots directly grounded in the narration"]

    @staticmethod
    def _diagrams(section: ScriptSection) -> list[str]:
        text = f"{section.heading or ''} {section.narration}".casefold()
        if any(word in text for word in ("map", "location", "journey", "region", "geography")):
            return ["Verified geography map or route diagram"]
        if any(word in text for word in ("number", "percentage", "data", "statistic", "compare")):
            return ["Evidence-backed chart or comparison graphic"]
        return []

    @staticmethod
    def _image_prompt(subject: str, goal: str, environment: str, config: VisionPlanningConfig, characters: Iterable[str]) -> str:
        character_text = ", ".join(characters) if characters else "no specific character identity"
        return (
            f"{config.preferred_style.value} visual for {subject}. {goal} "
            f"Environment: {environment}. Characters: {character_text}. "
            f"{config.color_theme} palette, {config.realism_level} realism, coherent cinematic composition, "
            "historically and factually constrained to supplied evidence."
        )

    @staticmethod
    def _video_prompt(subject: str, goal: str, shot: ShotType, movement: CameraMovement, duration: float, config: VisionPlanningConfig) -> str:
        return (
            f"{shot.value} shot of {subject}; {movement.value} camera movement; approximately {duration:.1f} seconds. "
            f"{goal} Style: {config.preferred_style.value}; transition-friendly composition; no unsupported visual facts."
        )
