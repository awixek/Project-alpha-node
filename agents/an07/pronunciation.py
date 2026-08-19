from __future__ import annotations

import re
from collections import Counter

from shared.exceptions import InputValidationError
from shared.constants import AgentID

from .models import PronunciationEntry


class PronunciationProcessor:
    """Deterministic pronunciation normalization; it never invents phonetics."""

    _TOKEN_RE = re.compile(r"[^\s,.;:!?()\[\]{}]+")

    def process(self, text: str, dictionary: dict[str, str]) -> tuple[str, list[PronunciationEntry]]:
        if not text.strip():
            raise InputValidationError("Narration text must not be empty.", agent_id=AgentID.VOICE_CORE)
        normalized = text
        entries: list[PronunciationEntry] = []
        for original, pronunciation in sorted(dictionary.items(), key=lambda item: len(item[0]), reverse=True):
            if not original.strip() or not pronunciation.strip():
                continue
            count = len(re.findall(re.escape(original), normalized, flags=re.IGNORECASE))
            if count:
                normalized = re.sub(re.escape(original), pronunciation, normalized, flags=re.IGNORECASE)
                entries.append(PronunciationEntry(original=original, pronunciation=pronunciation, occurrences=count))
        return normalized, entries

    @staticmethod
    def merge_dictionaries(*dictionaries: dict[str, str]) -> dict[str, str]:
        merged: dict[str, str] = {}
        for dictionary in dictionaries:
            for key, value in dictionary.items():
                if key.strip() and value.strip():
                    merged[key] = value
        return merged
