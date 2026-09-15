"""
Local hardware detection → model recommendation. STDLIB-ONLY on purpose: this is the SINGLE
SOURCE OF TRUTH for the curated model tiers, imported by BOTH the FastAPI backend (hardware.py,
which wraps it in GET /api/hardware) AND the desktop orchestrator (first-run model choice, before
any web framework is up). Keeping it free of fastapi/app deps is what lets the stdlib-only
orchestrator reuse it instead of duplicating the thresholds — so the picker, the recommendation,
and the first-run pull can never drift.

Everything is read on-device (no network, no phone-home). Recommendation bakes in the CPU/SWA
lesson: gemma3:4b uses sliding-window attention → ~10 s first token on a weak CPU (the prompt cache
can't help), while qwen2.5:3b is non-SWA → ~1 s. A bigger model is only suggested with a GPU or a
capable CPU; a weak CPU-only machine gets the ultra-light tier (qwen2.5:1.5b).
"""
import os
import platform
import subprocess

# Tier → curated model. size_gb is the first-run download size (what the user actually feels).
# `source`: "registry" → pulled from the Ollama registry (ollama.com); "mirror" → GGUF fetched
# from OUR host and sideloaded into Ollama (sovereign — see MIRROR_MODELS in hardware.py).
CATALOG = [
    {"tier": "ultralight", "model": "qwen2.5:1.5b",   "size_gb": 1.0,  "source": "registry"},
    {"tier": "light",    "model": "qwen2.5:3b",       "size_gb": 2.0,  "source": "registry"},
    {"tier": "balanced", "model": "gemma3:4b",        "size_gb": 3.0,  "source": "registry"},
    {"tier": "powerful", "model": "qwen3:30b-a3b",    "size_gb": 18.0, "source": "registry"},
    {"tier": "catalan",  "model": "salamandra-2b-instruct", "size_gb": 1.5, "source": "mirror"},
]
_BY_TIER = {c["tier"]: c for c in CATALOG}


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
    stays the GLOBAL default for everything else. SWA lesson baked in: never suggest gemma3:4b (SWA)
    without a GPU or a capable CPU — it would be ~10 s/message on a weak machine.

    Weak CPU-only machines get the ultra-light tier (qwen2.5:1.5b): measured on an i3 (2c/4t, no GPU)
    at ~0.65 s first token vs qwen2.5:3b's ~2.25 s and ~half the download, same family (non-SWA, so
    keep_alive/prewarm still bite) — usable-but-lighter, offered as an OPTION here, never a silent
    global downgrade (measurement 2026-09-14). qwen2.5:3b remains more careful on health/allergy
    recall, so it stays the default anywhere the machine can run it."""
    if lang == "ca":
        return _BY_TIER["catalan"]
    ram = hw.get("ram_gb") or 0.0
    cores = hw.get("cpu_cores") or 1
    gpu = hw.get("gpu")
    vram = (gpu or {}).get("vram_gb", 0.0)

    if gpu is not None:                              # GPU or Apple Silicon (unified) — 3b runs easily
        if ram >= 32 and vram >= 16:
            tier = "powerful"
        elif ram >= 16:
            tier = "balanced"
        else:
            tier = "light"
    else:                                            # CPU-only — SWA + first-token latency bite hardest
        if ram < 8 or cores <= 4:                    # weak CPU or low RAM → ultra-light (qwen2.5:1.5b)
            tier = "ultralight"
        elif ram >= 16 and cores >= 8:
            tier = "balanced"
        else:
            tier = "light"
    return _BY_TIER[tier]
