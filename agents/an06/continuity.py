from __future__ import annotations
from collections import defaultdict
from agents.an05.models import VisionPlan
from .models import ContinuityReport, GeneratedAsset

class ContinuityValidator:
    def validate(self, plan: VisionPlan, assets: list[GeneratedAsset]) -> ContinuityReport:
        findings=[]; by_scene=defaultdict(list)
        for asset in assets: by_scene[asset.scene_id].append(asset)
        ids=[s.scene_number for s in plan.scenes]
        if len(ids)!=len(set(ids)): findings.append("VisionPlan contains duplicate scene numbers.")
        for scene in plan.scenes:
            if not by_scene.get(scene.scene_number): findings.append(f"Scene {scene.scene_number}: no generated asset is available.")
        for previous,current in zip(plan.scenes,plan.scenes[1:]):
            if set(previous.characters)&set(current.characters) and not current.continuity_notes:
                findings.append(f"Scenes {previous.scene_number}-{current.scene_number}: recurring characters lack continuity notes.")
        return ContinuityReport(passed=not findings, findings=findings, checked_scenes=len(plan.scenes))
