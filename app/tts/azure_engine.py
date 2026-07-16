"""Azure AI Speech engine (optional cloud upgrade).

Requires the azure-cognitiveservices-speech package and an API key +
region saved in Settings. Text is only sent to Azure when this engine is
explicitly selected. Word boundaries come from the SDK's word_boundary
events; because we send SSML, the reported text offsets are positions in
the SSML string, so we translate them back through the escape offset map.
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..pronunciation import escape_xml_with_map
from .base import SynthesisResult, Voice, WordTiming, evenly_spaced_timings


class AzureNotConfigured(Exception):
    pass


def _get_speech_config():
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as exc:
        raise AzureNotConfigured(
            "The Azure Speech SDK is not installed. Run: "
            "pip install azure-cognitiveservices-speech"
        ) from exc

    settings = config.load_settings()
    key = settings.get("azure_key", "").strip()
    region = settings.get("azure_region", "").strip()
    if not key or not region:
        raise AzureNotConfigured(
            "Azure Speech is not configured. Add your API key and region in Settings."
        )
    return speechsdk, speechsdk.SpeechConfig(subscription=key, region=region)


# A short, curated list of natural English neural voices (en-AU first for
# Victorian classrooms). The full Azure catalogue is hundreds of voices;
# these are sensible defaults that can be extended in settings.json.
_CURATED_VOICES = [
    ("en-AU-NatashaNeural", "Natasha (Australian, female)"),
    ("en-AU-WilliamNeural", "William (Australian, male)"),
    ("en-AU-AnnetteNeural", "Annette (Australian, female)"),
    ("en-AU-DarrenNeural", "Darren (Australian, male)"),
    ("en-GB-SoniaNeural", "Sonia (British, female)"),
    ("en-GB-RyanNeural", "Ryan (British, male)"),
    ("en-US-JennyNeural", "Jenny (American, female)"),
    ("en-US-GuyNeural", "Guy (American, male)"),
]


class AzureEngine:
    engine_id = "azure"

    def list_voices(self) -> list[Voice]:
        return [
            Voice(id=vid, name=name, language=vid[:5])
            for vid, name in _CURATED_VOICES
        ]

    def synthesize(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
        out_path: Path,
    ) -> SynthesisResult:
        speechsdk, speech_config = _get_speech_config()

        voice_id = voice_id or "en-AU-NatashaNeural"
        lang = voice_id[:5] if len(voice_id) >= 5 else "en-AU"

        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff22050Hz16BitMonoPcm
        )

        escaped, escape_map = escape_xml_with_map(text)
        rate_pct = f"{max(-10, min(10, int(rate))) * 10:+d}%"
        pitch_pct = f"{max(-10, min(10, int(pitch))) * 5:+d}%"
        volume = max(0, min(100, int(volume)))
        prefix = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
            f'<voice name="{voice_id}">'
            f'<prosody rate="{rate_pct}" pitch="{pitch_pct}" volume="{volume}">'
        )
        ssml = prefix + escaped + "</prosody></voice></speak>"
        prefix_len = len(prefix)

        audio_config = speechsdk.audio.AudioOutputConfig(filename=str(out_path))
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config, audio_config=audio_config
        )

        events: list[tuple[int, int, int]] = []  # (ssml_offset, length, offset_ms)

        def on_word_boundary(evt):
            # audio_offset is in 100-nanosecond ticks.
            events.append((evt.text_offset, evt.word_length, int(evt.audio_offset / 10_000)))

        synthesizer.synthesis_word_boundary.connect(on_word_boundary)

        result = synthesizer.speak_ssml_async(ssml).get()
        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            raise RuntimeError(
                f"Azure synthesis failed: {details.reason}. {details.error_details or ''}".strip()
            )

        from ..audio_convert import wav_duration_ms

        duration_ms = wav_duration_ms(out_path)

        timings: list[WordTiming] = []
        for ssml_offset, length, offset_ms in events:
            esc_start = ssml_offset - prefix_len
            esc_end = esc_start + length
            if esc_start < 0 or esc_end > len(escaped):
                continue
            orig_start, orig_end = escape_map.to_original(esc_start, esc_end)
            timings.append(WordTiming(char_start=orig_start, char_end=orig_end, offset_ms=offset_ms))

        if not timings:
            timings = evenly_spaced_timings(text, duration_ms)

        return SynthesisResult(
            audio_path=out_path,
            audio_format="wav",
            duration_ms=duration_ms,
            word_timings=timings,
        )
