"""
Local hardware detection → model recommendation (sovereign: everything is read on-device, no
network, no phone-home). Powers Settings' "Best for your computer" badge and the onboarding
one-tap suggestion, built on the existing Option B model management.

The model CATALOG here is the SINGLE SOURCE OF TRUTH for the curated tiers — the picker chips are
built from GET /api/hardware, so the recommendation and the picker can never disagree.

Recommendation bakes in the CPU/SWA lesson: gemma3:4b uses sliding-window attention → ~10 s first
token on a weak CPU (the prompt cache can't help), while qwen2.5:3b is non-SWA → ~1 s. So a bigger
model is only suggested when there's a GPU or a genuinely capable CPU; otherwise Light (qwen2.5:3b).
"""
import os
import platform
import subprocess

from fastapi import APIRouter

router = APIRouter()

# Tier → curated model. size_gb is the first-run download size (what the user actually feels).
# `source`: "registry" → pulled from the Ollama registry (ollama.com); "mirror" → GGUF fetched
# from OUR host and sideloaded into Ollama (sovereign — see MIRROR_MODELS + config_routes).
CATALOG = [
    {"tier": "light",    "model": "qwen2.5:3b",       "size_gb": 2.0,  "source": "registry"},
    {"tier": "balanced", "model": "gemma3:4b",        "size_gb": 3.0,  "source": "registry"},
    {"tier": "powerful", "model": "qwen3:30b-a3b",    "size_gb": 18.0, "source": "registry"},
    {"tier": "catalan",  "model": "salamandra-2b-instruct", "size_gb": 1.5, "source": "mirror"},
]
_BY_TIER = {c["tier"]: c for c in CATALOG}

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


def _ram_gb() -> float:
    """Total physical RAM in GB, best-effort per OS. 0.0 if unknown (caller degrades gracefully)."""
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / (1024 * 1024), 1)  # kB → GB
        if system == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, timeout=2)
            if out.returncode == 0:
                return round(int(out.stdout.strip()) / (1024 ** 3), 1)          # bytes → GB
        if system == "Windows":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MS(); ms.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return round(ms.ullTotalPhys / (1024 ** 3), 1)
        # POSIX fallback (also covers Darwin if sysctl failed)
        return round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _gpu(arch: str, system: str) -> dict | None:
    """Best-effort discrete-GPU/VRAM probe. NVIDIA via nvidia-smi (absent = no NVIDIA, not an
    error); Apple Silicon = unified memory (RAM doubles as VRAM). Everything else → None
    (treated as CPU-only, which is the safe default for a CPU-first app)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,name", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            mib, _, name = out.stdout.strip().splitlines()[0].partition(",")
            return {"kind": "nvidia", "vram_gb": round(int(mib.strip()) / 1024, 1),
                    "name": name.strip()}
    except Exception:
        pass
    if system == "Darwin" and arch in ("arm64", "aarch64"):
        return {"kind": "apple", "vram_gb": _ram_gb(), "name": "Apple Silicon"}
    return None


def detect() -> dict:
    system = platform.system()
    arch = platform.machine()
    return {
        "os": system,
        "arch": arch,
        "cpu_cores": os.cpu_count() or 1,
        "ram_gb": _ram_gb(),
        "gpu": _gpu(arch, system),
    }


def recommend(hw: dict, lang: str = "auto") -> dict:
    """Map detected hardware (and the reply language) → a curated model. Catalan gets Salamandra
    (BSC, Apache-2.0) — measurably better Catalan than qwen2.5:3b and non-SWA/CPU-fast; qwen2.5:3b
    stays the default for everything else. SWA lesson baked in: never suggest gemma3:4b (SWA)
    without a GPU or a capable CPU — it would be ~10 s/message on a weak machine."""
    if lang == "ca":
        return _BY_TIER["catalan"]
    ram = hw.get("ram_gb") or 0.0
    cores = hw.get("cpu_cores") or 1
    gpu = hw.get("gpu")
    vram = (gpu or {}).get("vram_gb", 0.0)

    if gpu is not None:                              # GPU or Apple Silicon (unified)
        if ram >= 32 and vram >= 16:
            tier = "powerful"
        elif ram >= 16:
            tier = "balanced"
        else:
            tier = "light"
    else:                                            # CPU-only — SWA latency matters
        tier = "balanced" if (ram >= 16 and cores >= 8) else "light"
    return _BY_TIER[tier]


@router.get("/api/hardware")
def hardware(lang: str | None = None):
    # `lang` lets the picker PREVIEW the recommendation for the currently-selected reply language
    # (before Save); without it we fall back to the saved config language.
    from agent_config import get_config
    hw = detect()
    lang = (lang or get_config().get("language") or "auto").strip()
    return {"hardware": hw, "recommended": recommend(hw, lang), "catalog": CATALOG}
