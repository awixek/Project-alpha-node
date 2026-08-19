from __future__ import annotations

from .models import Timeline, Transition, RenderSettings


class TransitionEngine:
    """Produces provider-neutral transition instructions."""

    def build(self, timeline: Timeline, settings: RenderSettings) -> list[Transition]:
        transitions: list[Transition] = []
        for previous, current in zip(timeline.scenes, timeline.scenes[1:]):
            transition_type = current.transition_in or settings.transition_style.value
            duration = min(0.75, previous.duration / 4, current.duration / 4)
            if transition_type == "cut":
                duration = 0.0
            transitions.append(Transition(
                from_scene=previous.scene_id,
                to_scene=current.scene_id,
                transition_type=transition_type,
                duration=max(0.0, duration),
            ))
        return transitions
