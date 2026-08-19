from __future__ import annotations

from agents.an06.models import AssetPackage
from agents.an07.models import VoicePackage
from agents.an08.models import SubtitlePackage
from .models import Timeline, VideoQualityReport, SynchronizationReport


class VideoQualityValidator:
    def validate(self, timeline: Timeline, assets: AssetPackage, voice: VoicePackage,
                 subtitles: SubtitlePackage) -> tuple[VideoQualityReport, SynchronizationReport]:
        findings: list[str] = []
        missing = sum(1 for scene in timeline.scenes if not scene.asset_ids)
        invalid = sum(1 for scene in timeline.scenes if scene.end_time <= scene.start_time)
        duplicates = len(timeline.scenes) - len({scene.scene_id for scene in timeline.scenes})
        if missing:
            findings.append(f"{missing} scene(s) have no generated visual assets.")
        if invalid:
            findings.append(f"{invalid} scene(s) have invalid timing.")
        if duplicates:
            findings.append(f"{duplicates} duplicate scene identifier(s) detected.")

        audio_end = voice.synchronization.total_duration
        subtitle_end = max((s.end_time for t in subtitles.subtitle_tracks for s in t.segments), default=0.0)
        timeline_end = timeline.total_runtime
        audio_drift = abs(timeline_end - audio_end) if audio_end else 0.0
        subtitle_drift = abs(timeline_end - subtitle_end) if subtitle_end else 0.0
        sync_findings: list[str] = []
        if audio_end and audio_drift > 0.25:
            sync_findings.append(f"Narration drift is {audio_drift:.3f}s.")
        if subtitle_end and subtitle_drift > 0.25:
            sync_findings.append(f"Subtitle drift is {subtitle_drift:.3f}s.")

        quality_score = max(0.0, 100.0 - missing * 25.0 - invalid * 25.0 - duplicates * 20.0)
        sync_score = max(0.0, 100.0 - min(100.0, (audio_drift + subtitle_drift) * 20.0))
        return (
            VideoQualityReport(
                passed=not findings,
                score=quality_score,
                findings=findings,
                missing_assets=missing,
                invalid_timing=invalid,
                duplicate_clips=duplicates,
            ),
            SynchronizationReport(
                passed=not sync_findings,
                score=sync_score,
                findings=sync_findings,
                narration_drift_seconds=audio_drift,
                subtitle_drift_seconds=subtitle_drift,
            ),
        )
