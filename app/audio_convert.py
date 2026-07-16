"""WAV -> MP3 conversion using lameenc (pure pip wheel, no system ffmpeg
needed). Used when the user asks to export MP3 instead of WAV.
"""

from __future__ import annotations

import wave
from pathlib import Path


class ConversionError(Exception):
    pass


def wav_to_mp3(wav_path: Path, mp3_path: Path, bitrate: int = 128) -> Path:
    try:
        import lameenc
    except ImportError as exc:
        raise ConversionError(
            "lameenc is not installed. Run: pip install -r requirements.txt "
            "(or export as WAV instead)."
        ) from exc

    with wave.open(str(wav_path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise ConversionError("Only 16-bit PCM WAV audio is supported for MP3 export.")

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bitrate)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)  # 2 = high quality
    mp3_data = encoder.encode(frames)
    mp3_data += encoder.flush()

    mp3_path.write_bytes(mp3_data)
    return mp3_path


def wav_duration_ms(wav_path: Path) -> int:
    with wave.open(str(wav_path), "rb") as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
    return int((frames / float(rate)) * 1000) if rate else 0
