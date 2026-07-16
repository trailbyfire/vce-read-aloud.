"""VCE Read Aloud — local web app entry point.

Everything is served from 127.0.0.1 only: the app, the API, and the audio
files. Essay content never leaves this machine unless the Azure engine is
explicitly selected in Settings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, synth_service, text_extract
from .tts import get_engine
from .tts.azure_engine import AzureNotConfigured

app = FastAPI(title="VCE Read Aloud", docs_url=None, redoc_url=None)

BASE = Path(__file__).resolve().parent.parent


@app.on_event("startup")
def startup() -> None:
    config.ensure_dirs()
    db.init_db()


# --- classes ---------------------------------------------------------------

@app.get("/api/classes")
def api_list_classes():
    return db.list_classes()


@app.post("/api/classes")
def api_create_class(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Class name is required")
    try:
        return db.create_class(name)
    except Exception:  # noqa: BLE001 - UNIQUE constraint
        raise HTTPException(409, f"A class named '{name}' already exists")


@app.delete("/api/classes/{class_id}")
def api_delete_class(class_id: int):
    db.delete_class(class_id)
    return {"ok": True}


# --- students ----------------------------------------------------------------

@app.get("/api/students")
def api_list_students(class_id: Optional[int] = None):
    return db.list_students(class_id)


@app.post("/api/students")
def api_create_student(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    class_id = payload.get("class_id")
    if not name or not class_id:
        raise HTTPException(400, "Student name and class_id are required")
    return db.get_or_create_student(int(class_id), name)


@app.delete("/api/students/{student_id}")
def api_delete_student(student_id: int):
    db.delete_student(student_id)
    return {"ok": True}


# --- essays -------------------------------------------------------------------

@app.get("/api/essays")
def api_list_essays(
    class_id: Optional[int] = None,
    student_id: Optional[int] = None,
    q: Optional[str] = None,
):
    essays = db.list_essays(class_id, student_id, q)
    # Don't ship full essay bodies in list responses; previews are enough.
    for essay in essays:
        essay["preview"] = essay["content"][:180]
        del essay["content"]
    return essays


@app.get("/api/essays/{essay_id}")
def api_get_essay(essay_id: int):
    essay = db.get_essay(essay_id)
    if essay is None:
        raise HTTPException(404, "Essay not found")
    essay["paragraphs"] = synth_service.split_paragraphs(essay["content"])
    return essay


@app.post("/api/essays")
def api_create_essay(payload: dict = Body(...)):
    title = (payload.get("title") or "").strip() or "Untitled"
    content = (payload.get("content") or "").strip()
    student_id = payload.get("student_id")
    if not content or not student_id:
        raise HTTPException(400, "content and student_id are required")
    return db.create_essay(int(student_id), title, content)


@app.put("/api/essays/{essay_id}")
def api_update_essay(essay_id: int, payload: dict = Body(...)):
    essay = db.update_essay(essay_id, payload.get("title"), payload.get("content"))
    if essay is None:
        raise HTTPException(404, "Essay not found")
    return essay


@app.delete("/api/essays/{essay_id}")
def api_delete_essay(essay_id: int):
    db.delete_essay(essay_id)
    return {"ok": True}


@app.post("/api/essays/import")
async def api_import_essay(
    file: UploadFile = File(...),
    student_id: int = Form(...),
    title: Optional[str] = Form(None),
):
    data = await file.read()
    try:
        content = text_extract.extract_text(file.filename or "upload.txt", data)
    except text_extract.ExtractionError as exc:
        raise HTTPException(422, str(exc))
    essay_title = (title or "").strip() or Path(file.filename or "Untitled").stem
    return db.create_essay(student_id, essay_title, content, source_filename=file.filename)


# --- pronunciation rules --------------------------------------------------------

@app.get("/api/rules")
def api_list_rules():
    return db.list_rules()


@app.post("/api/rules")
def api_create_rule(payload: dict = Body(...)):
    find_text = (payload.get("find_text") or "").strip()
    replace_text = (payload.get("replace_text") or "").strip()
    if not find_text or not replace_text:
        raise HTTPException(400, "Both the word and its replacement are required")
    return db.create_rule(find_text, replace_text, bool(payload.get("whole_word", True)))


@app.delete("/api/rules/{rule_id}")
def api_delete_rule(rule_id: int):
    db.delete_rule(rule_id)
    return {"ok": True}


# --- voices and settings ----------------------------------------------------------

@app.get("/api/voices")
def api_list_voices(engine: Optional[str] = None):
    settings = config.load_settings()
    engine_name = engine or settings.get("engine", "local")
    try:
        engine_obj = get_engine(engine_name)
        voices = engine_obj.list_voices()
    except AzureNotConfigured as exc:
        return JSONResponse({"error": str(exc), "voices": []}, status_code=200)
    return {"voices": [vars(v) for v in voices], "engine": engine_obj.engine_id}


@app.get("/api/settings")
def api_get_settings():
    settings = config.load_settings()
    # Never send the raw key back to the page; just whether one is set.
    settings["azure_key_set"] = bool(settings.get("azure_key"))
    settings["azure_key"] = ""
    return settings


@app.put("/api/settings")
def api_update_settings(payload: dict = Body(...)):
    settings = config.load_settings()
    for key in ("engine", "voice_id", "rate", "pitch", "volume", "azure_region", "azure_voice"):
        if key in payload:
            settings[key] = payload[key]
    # Only overwrite the stored key when a new non-empty one is provided.
    if payload.get("azure_key"):
        settings["azure_key"] = payload["azure_key"].strip()
    if payload.get("clear_azure_key"):
        settings["azure_key"] = ""
    saved = config.save_settings(settings)
    saved["azure_key_set"] = bool(saved.get("azure_key"))
    saved["azure_key"] = ""
    return saved


# --- synthesis and export -----------------------------------------------------------

@app.post("/api/essays/{essay_id}/synthesize")
def api_synthesize(essay_id: int, payload: dict = Body(default={})):
    fmt = payload.get("format", "wav")
    if fmt not in ("wav", "mp3"):
        raise HTTPException(400, "format must be wav or mp3")
    try:
        return synth_service.synthesize_essay(essay_id, fmt=fmt)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except AzureNotConfigured as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001 - surface engine errors to the UI
        raise HTTPException(500, f"Synthesis failed: {exc}")


@app.post("/api/export/batch")
def api_batch_export(payload: dict = Body(...)):
    """Generate audio files for every essay in a class (or an explicit list
    of essay ids). Returns per-essay success/failure so one bad essay
    doesn't sink the batch.
    """
    fmt = payload.get("format", "mp3")
    if fmt not in ("wav", "mp3"):
        raise HTTPException(400, "format must be wav or mp3")
    essay_ids = payload.get("essay_ids")
    if not essay_ids:
        class_id = payload.get("class_id")
        if not class_id:
            raise HTTPException(400, "Provide essay_ids or class_id")
        essay_ids = [e["id"] for e in db.list_essays(class_id=int(class_id))]

    results = []
    for essay_id in essay_ids:
        try:
            result = synth_service.synthesize_essay(int(essay_id), fmt=fmt)
            results.append(
                {
                    "essay_id": essay_id,
                    "ok": True,
                    "file": Path(result["audio_path"]).name,
                    "audio_url": result["audio_url"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"essay_id": essay_id, "ok": False, "error": str(exc)})
    return {
        "results": results,
        "output_dir": str(config.AUDIO_DIR),
        "ok_count": sum(1 for r in results if r["ok"]),
        "fail_count": sum(1 for r in results if not r["ok"]),
    }


_PREVIEW_TEXT = (
    "This is a preview of the selected voice. "
    "The gum trees swayed gently as the class settled in for silent reading."
)


@app.post("/api/preview")
def api_preview_voice():
    """Speak a fixed sample sentence with the currently saved settings."""
    settings = config.load_settings()
    engine_name = settings.get("engine", "local")
    voice_id = settings.get("azure_voice") if engine_name == "azure" else settings.get("voice_id", "")
    try:
        engine = get_engine(engine_name)
        config.ensure_dirs()
        out_path = config.AUDIO_DIR / "_voice_preview.wav"
        engine.synthesize(
            _PREVIEW_TEXT,
            voice_id or "",
            int(settings.get("rate", 0)),
            int(settings.get("pitch", 0)),
            int(settings.get("volume", 100)),
            out_path,
        )
    except AzureNotConfigured as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Preview failed: {exc}")
    import time as _time

    return {"audio_url": f"/audio/_voice_preview.wav?t={int(_time.time())}"}


@app.get("/audio/{filename}")
def api_get_audio(filename: str):
    # Basic traversal guard; filenames are generated by us.
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = config.AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Audio file not found")
    media_type = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=filename)


@app.get("/api/health")
def api_health():
    return {"ok": True, "windows": config.is_windows(), "data_dir": str(config.DATA_DIR)}


# Static frontend, mounted last so /api and /audio win.
app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")
