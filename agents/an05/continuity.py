"""Continuity tracking and deterministic validation for AN-05."""
from __future__ import annotations

import re
from collections import OrderedDict

from shared.exceptions import InputValidationError

from .models import (
    CharacterContinuity,
    ContinuityPackage,
    EnvironmentContinuity,
    VisionScene,
)


def _norm(value: str) -> str:
    return " ".join(value.casefold().split())


class ContinuityManager:
    """Builds conservative continuity records without inventing identities."""

    def build(self, scenes: list[VisionScene], uncertainty: list[str]) -> ContinuityPackage:
        character_records: OrderedDict[str, CharacterContinuity] = OrderedDict()
        environment_records: OrderedDict[str, EnvironmentContinuity] = OrderedDict()
        for scene in scenes:
            for character in scene.characters:
                key = _norm(character)
                existing = character_records.get(key)
                if existing:
                    character_records[key] = existing.model_copy(update={
                        "scenes": [*existing.scenes, scene.scene_number],
                        "emotional_progression": [*existing.emotional_progression, scene.character_emotion or "unspecified"],
                    })
                else:
                    character_records[key] = CharacterContinuity(
                        character_key=character,
                        appearance=scene.character_description or "unspecified; no verified appearance supplied",
                        clothing=scene.costume_description or "unspecified; preserve only if later verified",
                        hairstyle="unspecified; do not invent",
                        accessories="unspecified; do not invent",
                        emotional_progression=[scene.character_emotion or "unspecified"],
                        scenes=[scene.scene_number],
                    )
            key = _norm(scene.environment)
            existing_env = environment_records.get(key)
            if existing_env:
                environment_records[key] = existing_env.model_copy(update={"scenes": [*existing_env.scenes, scene.scene_number]})
            else:
                environment_records[key] = EnvironmentContinuity(
                    environment_key=scene.environment,
                    description=scene.environment,
                    architecture=scene.architecture_style or "unspecified; preserve verified architecture only",
                    geography="unspecified; do not infer",
                    period="unspecified; use verified timeline only",
                    weather=scene.weather,
                    lighting=scene.lighting,
                    props=scene.objects,
                    scenes=[scene.scene_number],
                )
        return ContinuityPackage(
            characters=list(character_records.values()),
            environments=list(environment_records.values()),
            global_rules=[
                "Do not change recurring character appearance unless verified by source material.",
                "Do not introduce historical, geographic, architectural, costume, or prop details that are not supported by the input.",
                "Treat unspecified attributes as uncertainty rather than facts.",
            ],
            uncertainty_notes=uncertainty,
        )

    @staticmethod
    def validate(scenes: list[VisionScene], continuity: ContinuityPackage) -> list[str]:
        issues: list[str] = []
        seen_scenes: set[int] = set()
        for scene in scenes:
            if scene.scene_number in seen_scenes:
                issues.append(f"Duplicate scene number: {scene.scene_number}.")
            seen_scenes.add(scene.scene_number)
            if not scene.image_prompt.strip() or not scene.video_prompt.strip():
                issues.append(f"Scene {scene.scene_number} has an empty visual prompt.")
        character_map = {item.character_key.casefold(): item for item in continuity.characters}
        for scene in scenes:
            for character in scene.characters:
                if character.casefold() not in character_map:
                    issues.append(f"Character continuity missing for scene {scene.scene_number}: {character}.")
        for previous, current in zip(scenes, scenes[1:]):
            if previous.transition_type is None or current.transition_type is None:
                issues.append(f"Scene transition missing between {previous.scene_number} and {current.scene_number}.")
        return issues

    @staticmethod
    def extract_explicit_characters(text: str) -> list[str]:
        """Extract only explicit role/name markers; avoids guessing from prose."""
        patterns = [
            r"\bcharacter\s*:\s*([A-Za-z][A-Za-z .'-]{1,80})",
            r"\bperson\s*:\s*([A-Za-z][A-Za-z .'-]{1,80})",
        ]
        found: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                value = " ".join(match.group(1).split()).strip(" .,-")
                if value and value.casefold() not in {item.casefold() for item in found}:
                    found.append(value)
        return found
