from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class CaptionChunk:
    text: str
    word_count: int

class SubtitleBuilder:
    """Deterministically converts timed narration into readable caption chunks."""

    _SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
    def __init__(self, max_chars: int = 42, max_lines: int = 2, reading_speed: float = 17.0):
        self.max_chars, self.max_lines, self.reading_speed = max_chars, max_lines, reading_speed

    def segment_text(self, text: str) -> list[CaptionChunk]:
        text = " ".join(text.split())
        if not text:
            return []
        sentences = [s.strip() for s in self._SENTENCE_RE.split(text) if s.strip()]
        chunks: list[CaptionChunk] = []
        for sentence in sentences:
            words = sentence.split()
            current: list[str] = []
            for word in words:
                candidate = " ".join(current + [word])
                if current and len(candidate) > self.max_chars * self.max_lines:
                    chunks.extend(self._balance(current))
                    current = [word]
                else:
                    current.append(word)
            if current:
                chunks.extend(self._balance(current))
        return chunks

    def _balance(self, words: list[str]) -> list[CaptionChunk]:
        if not words:
            return []
        chunks: list[CaptionChunk] = []
        current: list[str] = []
        for word in words:
            candidate = " ".join(current + [word])
            if current and len(candidate) > self.max_chars:
                chunks.append(CaptionChunk(" ".join(current), len(current)))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(CaptionChunk(" ".join(current), len(current)))
        return chunks
