from __future__ import annotations

from .models import VoiceCoreConfig, VoiceQualityReport, VoiceSegment


class VoiceQualityValidator:
    def validate(self, segments: list[VoiceSegment], config: VoiceCoreConfig) -> VoiceQualityReport:
        if not segments:
            return VoiceQualityReport(passed=False, score=0.0, findings=["No narration segments were generated."], checked_segments=0)
        findings: list[str] = []
        score = 100.0
        ids: set[str] = set()
        previous_end = 0.0
        for segment in segments:
            if segment.segment_id in ids:
                findings.append(f"Duplicate segment id: {segment.segment_id}.")
                score -= 20
            ids.add(segment.segment_id)
            if not segment.audio_uri:
                findings.append(f"Segment {segment.segment_id}: missing audio URI.")
                score -= 20
            if segment.estimated_end_time <= segment.start_time:
                findings.append(f"Segment {segment.segment_id}: invalid timing.")
                score -= 15
            if segment.start_time < previous_end - 0.001:
                findings.append(f"Segment {segment.segment_id}: overlapping timing.")
                score -= 15
            previous_end = max(previous_end, segment.estimated_end_time)
            if not segment.processed_text.strip():
                findings.append(f"Segment {segment.segment_id}: empty processed narration.")
                score -= 20
        score = max(0.0, min(100.0, score))
        return VoiceQualityReport(
            passed=score >= config.minimum_quality_score and not findings,
            score=score,
            findings=findings,
            checked_segments=len(segments),
        )
