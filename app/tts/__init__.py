from __future__ import annotations

from .. import config
from .base import SynthesisResult, TTSEngine, Voice


def get_engine(name: str) -> TTSEngine:
    if name == "azure":
        from .azure_engine import AzureEngine

        return AzureEngine()

    if config.is_windows():
        try:
            from .sapi_engine import SapiEngine

            return SapiEngine()
        except ImportError:
            pass

    from .dev_fallback_engine import DevFallbackEngine

    return DevFallbackEngine()


__all__ = ["get_engine", "TTSEngine", "Voice", "SynthesisResult"]
