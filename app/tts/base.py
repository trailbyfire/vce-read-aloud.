"""Common interface every TTS engine (local SAPI5, Azure cloud, dev
fallback) implements, so the rest of the app doesn't care which one is
active.

Engines receive the exact text to speak (pronunciation substitutions are
applied by the caller) and report word timings as character offsets into
that same text. The synth service is responsible for mapping those offsets
back to the displayed essay text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Voice:
    id: str
    name: str
    language: str = ""
    gender: str = ""


@dataclass
class WordTiming:
    char_start: int
    char_end: int
    offset_ms: int


@dataclass
class SynthesisResult:
    audio_path: Path
    audio_format: str  # "wav" or "mp3"
    duration_ms: int
    word_timings: list[WordTiming] = field(default_factory=list)


class TTSEngine(Protocol):
    engine_id: str

    def list_voices(self) -> list[Voice]:
        ...

    def synthesize(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
        out_path: Path,
    ) -> SynthesisResult:
        """Render `text` to a WAV file at out_path, returning word timings
        (char offsets into `text`, millisecond audio offsets).

        rate and pitch are -10..10 relative values; volume is 0..100.
        """
        ...


def evenly_spaced_timings(text: str, duration_ms: int) -> list[WordTiming]:
    """Estimate word timings proportionally by character position. Used as
    a fallback when an engine can't report real word boundaries, so
    highlighting still tracks approximately.
    """
    import re

    words = list(re.finditer(r"\S+", text))
    if not words or duration_ms <= 0:
        return []
    total_chars = len(text)
    timings = []
    for m in words:
        offset = int(duration_ms * (m.start() / total_chars))
        timings.append(WordTiming(char_start=m.start(), char_end=m.end(), offset_ms=offset))
    return timings
