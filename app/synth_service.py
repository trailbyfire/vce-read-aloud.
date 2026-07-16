"""Orchestrates synthesis for an essay: applies pronunciation rules, runs
the selected TTS engine, maps word timings back to the displayed text,
computes paragraph offsets, converts to MP3 when requested, and caches
results so repeat listens don't re-synthesize.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from . import config, db
from .audio_convert import wav_to_mp3
from .pronunciation import apply_substitutions_with_map
from .tts import get_engine


def split_paragraphs(content: str) -> list[dict]:
    """Split essay content into paragraphs with char offsets into content."""
    paragraphs = []
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]*)*", content):
        text = m.group().strip()
        if text:
            paragraphs.append({"text": m.group(), "start": m.start(), "end": m.end()})
    return paragraphs


def _safe_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    cleaned = re.sub(r"[^\w\- ]", "", normalized).strip().replace(" ", "_")
    return cleaned or "untitled"


def audio_filename(essay: dict, fmt: str) -> str:
    return (
        f"{_safe_filename(essay['class_name'])}--"
        f"{_safe_filename(essay['student_name'])}--"
        f"{_safe_filename(essay['title'])}--{essay['id']}.{fmt}"
    )


def synthesize_essay(essay_id: int, fmt: str = "wav", use_cache: bool = True) -> dict:
    """Synthesize an essay and return
    {audio_path, audio_url, format, duration_ms, timings, paragraphs}.
    """
    essay = db.get_essay(essay_id)
    if essay is None:
        raise LookupError(f"Essay {essay_id} not found")

    settings = config.load_settings()
    engine_name = settings.get("engine", "local")
    voice_id = settings.get("voice_id", "")
    rate = int(settings.get("rate", 0))
    pitch = int(settings.get("pitch", 0))
    volume = int(settings.get("volume", 100))
    if engine_name == "azure":
        voice_id = settings.get("azure_voice") or voice_id

    if use_cache:
        cached = db.latest_audio_export(essay_id, engine_name, voice_id, rate, pitch, volume)
        if cached and cached["audio_format"] == fmt and Path(cached["audio_path"]).exists():
            payload = json.loads(cached["timings_json"])
            # Invalidate if the essay text or pronunciation rules changed
            # since this audio was generated.
            if (
                payload.get("content_fingerprint") == _fingerprint(essay["content"])
                and payload.get("rules_fingerprint") == _rules_fingerprint()
            ):
                return _result_payload(essay, Path(cached["audio_path"]), fmt, payload)

    content = essay["content"]
    rules = db.list_rules()
    spoken_text, sub_map = apply_substitutions_with_map(content, rules)

    engine = get_engine(engine_name)
    config.ensure_dirs()
    wav_path = config.AUDIO_DIR / audio_filename(essay, "wav")
    result = engine.synthesize(spoken_text, voice_id, rate, pitch, volume, wav_path)

    # Engine timings are offsets into spoken_text; map back to content.
    timings = []
    for t in result.word_timings:
        orig_start, orig_end = sub_map.to_original(t.char_start, t.char_end)
        if orig_end > orig_start:
            timings.append({"start": orig_start, "end": orig_end, "ms": t.offset_ms})
    timings.sort(key=lambda t: t["ms"])

    paragraphs = split_paragraphs(content)
    for para in paragraphs:
        para_timings = [t for t in timings if t["start"] >= para["start"] and t["start"] < para["end"]]
        para["offset_ms"] = para_timings[0]["ms"] if para_timings else None

    audio_path = result.audio_path
    audio_format = "wav"
    if fmt == "mp3":
        mp3_path = config.AUDIO_DIR / audio_filename(essay, "mp3")
        wav_to_mp3(wav_path, mp3_path)
        audio_path = mp3_path
        audio_format = "mp3"

    payload = {
        "duration_ms": result.duration_ms,
        "timings": timings,
        "paragraphs": [
            {"start": p["start"], "end": p["end"], "offset_ms": p["offset_ms"]}
            for p in paragraphs
        ],
        "content_fingerprint": _fingerprint(content),
        "rules_fingerprint": _rules_fingerprint(),
    }
    db.create_audio_export(
        essay_id=essay_id,
        engine=engine_name,
        voice_id=voice_id,
        rate=rate,
        pitch=pitch,
        volume=volume,
        audio_path=str(audio_path),
        audio_format=audio_format,
        timings_json=json.dumps(payload),
    )
    return _result_payload(essay, audio_path, audio_format, payload)


def _result_payload(essay: dict, audio_path: Path, fmt: str, payload: dict) -> dict:
    return {
        "essay_id": essay["id"],
        "audio_path": str(audio_path),
        "audio_url": f"/audio/{audio_path.name}",
        "format": fmt,
        "duration_ms": payload.get("duration_ms", 0),
        "timings": payload.get("timings", []),
        "paragraphs": payload.get("paragraphs", []),
    }


def _fingerprint(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _rules_fingerprint() -> str:
    import hashlib

    rules = db.list_rules()
    blob = json.dumps(
        [(r["find_text"], r["replace_text"], r["whole_word"]) for r in rules],
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
