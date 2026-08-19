from __future__ import annotations
from shared.schemas import AssetType
from .models import GeneratedAsset, QualityReport, VisionCreatorConfig

class AssetQualityValidator:
    def validate(self, assets: list[GeneratedAsset], config: VisionCreatorConfig) -> QualityReport:
        if not assets: return QualityReport(passed=False, score=0.0, findings=["No generated assets were returned."], checked_assets=0)
        findings=[]; score=100.0; paths=set(); checksums=set()
        for asset in assets:
            if asset.storage_path in paths: findings.append(f"Scene {asset.scene_id}: duplicate storage path."); score-=15
            paths.add(asset.storage_path)
            if asset.checksum and asset.checksum in checksums: findings.append(f"Scene {asset.scene_id}: duplicate checksum."); score-=15
            if asset.checksum: checksums.add(asset.checksum)
            if asset.asset_type == AssetType.IMAGE and (asset.width_px is None or asset.height_px is None): findings.append(f"Scene {asset.scene_id}: missing image dimensions."); score-=10
            elif asset.asset_type == AssetType.IMAGE and (asset.width_px < 640 or asset.height_px < 360): findings.append(f"Scene {asset.scene_id}: resolution below minimum."); score-=20
        score=max(0.0,min(100.0,score)); return QualityReport(passed=score>=config.minimum_quality_score and not findings, score=score, findings=findings, checked_assets=len(assets))
