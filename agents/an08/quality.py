from __future__ import annotations
from .models import SubtitleSegment, SynchronizationMetrics, SubtitleQualityReport

class SubtitleQualityValidator:
    def validate(self, segments: list[SubtitleSegment], *, max_chars: int, max_lines: int, reading_speed: float, tolerance: float) -> SubtitleQualityReport:
        findings=[]; overlaps=invalid=speed=0
        duplicates=0
        seen_texts=set()
        ordered=sorted(segments,key=lambda s:(s.start_time,s.end_time,s.sequence))
        for i,s in enumerate(ordered):
            if s.end_time <= s.start_time or s.duration <= 0:
                invalid += 1; findings.append(f"Invalid timing: {s.subtitle_id}")
            if not s.text.strip():
                findings.append(f"Empty caption: {s.subtitle_id}")
            normalized=s.text.casefold().strip()
            if normalized in seen_texts:
                duplicates += 1; findings.append(f"Duplicate caption: {s.subtitle_id}")
            seen_texts.add(normalized)
            if len(s.text) > max_chars*max_lines:
                findings.append(f"Caption too long: {s.subtitle_id}")
            if s.line_count > max_lines:
                findings.append(f"Too many lines: {s.subtitle_id}")
            if s.duration > 0 and len(s.text)/s.duration > reading_speed + tolerance:
                speed += 1; findings.append(f"Reading speed exceeded: {s.subtitle_id}")
            if i and s.start_time < ordered[i-1].end_time - tolerance:
                overlaps += 1; findings.append(f"Overlapping subtitles: {ordered[i-1].subtitle_id}/{s.subtitle_id}")
        avg=sum(s.synchronization_score for s in ordered)/len(ordered) if ordered else 0
        metrics=SynchronizationMetrics(total_segments=len(ordered), overlaps=overlaps, invalid_timings=invalid, average_score=avg, average_drift_seconds=0.0, reading_speed_violations=speed)
        penalty=invalid*20+overlaps*15+speed*3+duplicates*10
        score=max(0.0, min(100.0, avg*100-penalty))
        return SubtitleQualityReport(passed=bool(ordered) and not invalid and not overlaps and not speed and not duplicates and score>=70, score=score, findings=findings, metrics=metrics)
