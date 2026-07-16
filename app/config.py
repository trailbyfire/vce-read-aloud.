"""Central paths and app-wide configuration.

Everything the app writes (database, exported audio, local settings/keys)
lives under DATA_DIR, next to the app, so it is easy to see, back up, or
delete. Nothing here is ever sent anywhere.
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from threading import Lock

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VCE_DATA_DIR", BASE_DIR / "data"))
AUDIO_DIR = DATA_DIR / "audio"
DB_PATH = DATA_DIR / "library.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "engine": "local",  # "local" (OS voices) or "azure" (cloud upgrade)
    "voice_id": "",
    "rate": 0,  # -10..10, SAPI-style relative rate
    "pitch": 0,  # -10..10, relative pitch
    "volume": 100,  # 0..100
    "azure_key": "",
    "azure_region": "",
    "azure_voice": "en-AU-NatashaNeural",
}

_settings_lock = Lock()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def is_windows() -> bool:
    return platform.system() == "Windows"


def load_settings() -> dict:
    ensure_dirs()
    with _settings_lock:
        if not SETTINGS_PATH.exists():
            SETTINGS_PATH.write_text(json.dumps(DEFAULT_SETTINGS, indent=2))
            return dict(DEFAULT_SETTINGS)
        try:
            data = json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        return merged


def save_settings(settings: dict) -> dict:
    ensure_dirs()
    with _settings_lock:
        merged = dict(DEFAULT_SETTINGS)
        merged.update(settings)
        SETTINGS_PATH.write_text(json.dumps(merged, indent=2))
        return merged
