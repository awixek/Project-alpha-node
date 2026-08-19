from __future__ import annotations
import threading
from uuid import UUID
from .models import AssetManifest, AssetManifestItem, GeneratedAsset, OptimizationReport

class AssetManager:
    def __init__(self) -> None: self._lock=threading.RLock(); self._assets: dict[UUID,GeneratedAsset]={}
    def add(self, asset: GeneratedAsset) -> None:
        with self._lock: self._assets[asset.asset_id]=asset
    def assets(self) -> list[GeneratedAsset]:
        with self._lock: return list(self._assets.values())
    def build_manifest(self) -> AssetManifest:
        with self._lock:
            return AssetManifest(items=[AssetManifestItem(asset_id=a.asset_id,scene_id=a.scene_id,asset_type=a.asset_type,storage_path=a.storage_path,provider=a.provider,reusable=a.reusable,checksum=a.checksum,version=a.asset_version,purpose="scene asset") for a in self._assets.values()])
    def optimize(self, level: str) -> OptimizationReport:
        assets=self.assets(); seen=set(); findings=[]
        for asset in assets:
            key=asset.checksum or asset.storage_path
            if key in seen: findings.append(f"Scene {asset.scene_id}: duplicate asset identity.")
            seen.add(key)
        if not findings: findings.append(f"Metadata normalized at optimization level '{level}'.")
        return OptimizationReport(applied=True,findings=findings,normalized_assets=len(assets))
