"""Windows SAPI5 engine via pywin32.

Synthesizes to a WAV file and captures Word boundary events so the UI can
highlight words in sync. Word events report the character position within
the input string and the byte position within the output stream; byte
position divided by bytes-per-second gives the audio offset.

If events fail for any reason (some third-party SAPI voices don't emit
them), we fall back to proportional timing estimates so highlighting still
works approximately.
"""

from __future__ import annotations

from pathlib import Path

from .base import SynthesisResult, Voice, WordTiming, evenly_spaced_timings

# SpeechVoiceEvents flags
_SVE_WORD_BOUNDARY = 2
_SVE_END_INPUT_STREAM = 4
# SpeechVoiceSpeakFlags
_SVSF_ASYNC = 1
_SVSF_PURGE_BEFORE_SPEAK = 2
# SpeechAudioFormatType: SAFT22kHz16BitMono
_SAFT_22K_16BIT_MONO = 22
_BYTES_PER_SEC = 22050 * 2
# SpeechStreamFileMode
_SSFM_CREATE_FOR_WRITE = 3


class SapiEngine:
    engine_id = "local"

    def list_voices(self) -> list[Voice]:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            sp_voice = win32com.client.Dispatch("SAPI.SpVoice")
            voices = []
            for token in sp_voice.GetVoices():
                try:
                    language = token.GetAttribute("Language")
                except Exception:  # noqa: BLE001 - attribute optional per voice
                    language = ""
                try:
                    gender = token.GetAttribute("Gender")
                except Exception:  # noqa: BLE001
                    gender = ""
                voices.append(
                    Voice(
                        id=token.Id,
                        name=token.GetDescription(),
                        language=language,
                        gender=gender,
                    )
                )
            return voices
        finally:
            pythoncom.CoUninitialize()

    def synthesize(
        self,
        text: str,
        voice_id: str,
        rate: int,
        pitch: int,
        volume: int,
        out_path: Path,
    ) -> SynthesisResult:
        import pythoncom
        import win32com.client

        from ..audio_convert import wav_duration_ms
        from ..pronunciation import escape_xml_with_map

        pythoncom.CoInitialize()
        try:
            events: list[tuple[int, int, int]] = []  # (char_pos, length, stream_bytes)
            done_flag = {"done": False}

            class _Sink:
                def OnWord(self, stream_number, stream_position, character_position, length):
                    events.append((character_position, length, stream_position))

                def OnEndStream(self, stream_number, stream_position):
                    done_flag["done"] = True

            sp_voice = win32com.client.DispatchWithEvents("SAPI.SpVoice", _Sink)
            sp_voice.EventInterests = _SVE_WORD_BOUNDARY | _SVE_END_INPUT_STREAM

            if voice_id:
                for token in sp_voice.GetVoices():
                    if token.Id == voice_id:
                        sp_voice.Voice = token
                        break

            sp_voice.Rate = max(-10, min(10, int(rate)))
            sp_voice.Volume = max(0, min(100, int(volume)))

            file_stream = win32com.client.Dispatch("SAPI.SpFileStream")
            file_stream.Format.Type = _SAFT_22K_16BIT_MONO
            file_stream.Open(str(out_path), _SSFM_CREATE_FOR_WRITE)
            sp_voice.AudioOutputStream = file_stream

            # Pitch has no direct SAPI property; it needs XML markup. The
            # markup (and XML escaping) shifts character positions, so we
            # keep an offset map to translate word events back.
            escaped, escape_map = escape_xml_with_map(text)
            pitch = max(-10, min(10, int(pitch)))
            prefix = f'<pitch absmiddle="{pitch}"/>'
            spoken = prefix + escaped
            prefix_len = len(prefix)

            flags = _SVSF_ASYNC | _SVSF_PURGE_BEFORE_SPEAK | 8  # 8 = SVSFIsXML
            sp_voice.Speak(spoken, flags)

            # Pump COM messages so events are delivered while we wait.
            while not done_flag["done"]:
                if sp_voice.WaitUntilDone(50):
                    break
                pythoncom.PumpWaitingMessages()
            pythoncom.PumpWaitingMessages()

            file_stream.Close()

            duration_ms = wav_duration_ms(out_path)

            timings: list[WordTiming] = []
            for char_pos, length, stream_bytes in events:
                esc_start = char_pos - prefix_len
                esc_end = esc_start + length
                if esc_start < 0:
                    continue
                orig_start, orig_end = escape_map.to_original(esc_start, esc_end)
                offset_ms = int(stream_bytes / _BYTES_PER_SEC * 1000)
                timings.append(
                    WordTiming(char_start=orig_start, char_end=orig_end, offset_ms=offset_ms)
                )

            if not timings:
                timings = evenly_spaced_timings(text, duration_ms)

            return SynthesisResult(
                audio_path=out_path,
                audio_format="wav",
                duration_ms=duration_ms,
                word_timings=timings,
            )
        finally:
            pythoncom.CoUninitialize()
