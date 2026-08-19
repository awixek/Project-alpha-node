from __future__ import annotations
from dataclasses import dataclass
from .models import SubtitleSegment
from agents.an07.models import VoiceSegment
from .subtitle_builder import SubtitleBuilder

@dataclass(frozen=True)
class TimedChunk:
    text: str
    start: float
    end: float

class SubtitleSynchronizer:
    def __init__(self, builder: SubtitleBuilder, offset: float = 0.0):
        self.builder, self.offset = builder, offset

    def synchronize(self, voice_segments: list[VoiceSegment], language: str, speaker_labels: bool = True) -> list[SubtitleSegment]:
        result: list[SubtitleSegment] = []
        sequence = 0
        for voice in voice_segments:
            chunks = self.builder.segment_text(voice.processed_text or voice.text)
            if not chunks:
                continue
            total_words = sum(c.word_count for c in chunks)
            cursor = max(0.0, voice.start_time + self.offset)
            available = max(0.001, voice.estimated_end_time - voice.start_time)
            for chunk in chunks:
                duration = max(0.05, available * chunk.word_count / total_words)
                start = cursor
                end = min(voice.estimated_end_time + self.offset, start + duration)
                if end <= start:
                    end = start + 0.05
                result.append(SubtitleSegment(
                    subtitle_id=f"{voice.segment_id}-{sequence}",
                    scene_id=voice.section_id,
                    sequence=sequence,
                    start_time=start,
                    end_time=end,
                    duration=end-start,
                    language=language,
                    speaker=voice.narrator if speaker_labels else None,
                    text=chunk.text,
                    confidence=1.0,
                    synchronization_score=1.0,
                    line_count=max(1, (len(chunk.text)+self.builder.max_chars-1)//self.builder.max_chars),
                    word_count=chunk.word_count,
                ))
                sequence += 1
                cursor = end
        return result
