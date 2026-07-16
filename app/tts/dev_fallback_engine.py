"""Development/testing fallback engine for non-Windows machines (this app
targets Windows + SAPI5 for real local voices). If espeak-ng is installed
it produces real speech; otherwise it writes silent audio of a plausible
length. Word timings are estimated either way, so the full UI pipeline can
be exercised anywhere.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import wave
from pathlib import Path

from .base import SynthesisResult, Voice, evenly_spaced_timings

_SAMPLE_RATE = 22050


class DevFallbackEngine:
    engine_id = "local"

    def list_voices(self) -> list[Voice]:
        if shutil.which("espeak-ng"):
            return [Voice(id="espeak:en", name="eSpeak English (dev fallback)", language="en")]
        return [Voice(id="silence", name="Silent placeholder (dev fallback)", language="en")]

    def synthesize(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
        out_path: Path,
    ) -> SynthesisResult:
        if shutil.which("espeak-ng"):
            # espeak-ng rate: words/min, default ~175. Map -10..10 onto 80..350.
            wpm = int(175 + rate * 13)
            espeak_pitch = int(50 + pitch * 4)  # 0..99, default 50
            amplitude = int(volume * 2)  # 0..200, default 100
            subprocess.run(
                [
                    "espeak-ng", "-v", "en", "-s", str(wpm), "-p", str(espeak_pitch),
                    "-a", str(amplitude), "-w", str(out_path), text,
                ],
                check=True,
                capture_output=True,
            )
        else:
            words = len(re.findall(r"\S+", text))
            duration_s = max(1.0, words * 60.0 / 160.0)  # ~160 wpm reading pace
            n_frames = int(_SAMPLE_RATE * duration_s)
            with wave.open(str(out_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(_SAMPLE_RATE)
                wav_file.writeframes(b"\x00\x00" * n_frames)

        from ..audio_convert import wav_duration_ms

        duration_ms = wav_duration_ms(out_path)
        return SynthesisResult(
            audio_path=out_path,
            audio_format="wav",
            duration_ms=duration_ms,
            word_timings=evenly_spaced_timings(text, duration_ms),
        )
