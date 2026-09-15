"""
HTTP surface for hardware detection → model recommendation. The detection + tier logic + CATALOG
live in the stdlib-only `hwdetect` module (SINGLE SOURCE OF TRUTH, also imported by the desktop
orchestrator for the first-run model choice). This file only wraps them in GET /api/hardware and
holds the sovereign-mirror model metadata used by config_routes' sideload.

The picker chips are built from GET /api/hardware, so the recommendation and the picker can never
disagree — and because first-run reuses the same `hwdetect.recommend`, the first-run pull can't
drift from what the picker shows either.
"""
import os

from fastapi import APIRouter

from hwdetect import CATALOG, detect, recommend

router = APIRouter()

# Sovereign mirror for GGUF chat models: our own release. Overridable (tests / a future CDN).
MODEL_ASSETS_BASE = os.getenv(
    "VOKTER_MODEL_ASSETS_BASE",
    "https://github.com/vokter-eu/Vokter/releases/download/models-v1",
).rstrip("/")

# Models we host ourselves and sideload into Ollama (GGUF + the Ollama import recipe). Salamandra
# (BSC, Apache-2.0) is the Catalan pick — see the ChatML template from its tokenizer_config.
MIRROR_MODELS = {
    "salamandra-2b-instruct": {
        "gguf": "salamandra-2b-instruct-Q4_K_M.gguf",
        "sha256": "3984c6f0204a981379aa02ddbe67a7c7ebe6f26c9bc0543832cf77bd2b665a33",
        "size": 1506089312,
        "template": "{{- range .Messages }}<|im_start|>{{ .Role }}\n{{ .Content }}<|im_end|>\n{{ end }}<|im_start|>assistant\n",
        "stop": ["<|im_end|>", "</s>"],
        "num_ctx": 8192,
    },
}


@router.get("/api/hardware")
def hardware(lang: str | None = None):
    # `lang` lets the picker PREVIEW the recommendation for the currently-selected reply language
    # (before Save); without it we fall back to the saved config language.
    from agent_config import get_config
    hw = detect()
    lang = (lang or get_config().get("language") or "auto").strip()
    return {"hardware": hw, "recommended": recommend(hw, lang), "catalog": CATALOG}
