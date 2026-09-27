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
    {"tier": "balanced", "model": "llama3.1:8b",      "size_gb": 4.9,  "source": "registry"},
    {"tier": "powerful", "model": "qwen2.5:14b",      "size_gb": 9.0,  "source": "registry"},
    {"tier": "catalan",  "model": "salamandra-2b-instruct", "size_gb": 1.5, "source": "mirror"},
]
# BALANCED (mid) = llama3.1:8b: NON-SWA, multilingual, and it PASSED the N=5 harness (confab 0/5,
# no-preamble 0/10, one-language clean — incl. the English→Spanish slip that qwen2.5:7b FAILED 5/5,
# 2026-09-27). qwen2.5:7b was rejected for that slip; gemma3:4b is out (SWA → ~10 s/msg on weak CPU,
# golden rule); qwen3:30b-a3b was dropped from the curated list (thinking model, never gated) — all
# three remain reachable via the free-text "any Ollama model" field. See [[project_vokter_model_tiering]].
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


def _capable_gpu(hw: dict) -> bool:
    """True when the box can run the powerful tier (qwen2.5:14b, ~9 GB) with dignity. Discrete GPU:
    ≥16 GB DEDICATED VRAM. Apple: unified memory is SHARED with the OS, so require ≥32 GB total (a
    16 GB Mac would be tight/swappy with a ~9 GB model) — don't let vram==ram trip the discrete
    threshold. Pure CPU is never capable (single-digit tok/s). This is the SINGLE definition of
    "14B fits" — recommend() and visible_catalog() both call it, so the recommendation and the
    curated chips can never disagree about what fits (the invariant holds by construction, not by
    copy-paste)."""
    gpu = hw.get("gpu")
    if gpu is None:
        return False
    ram = hw.get("ram_gb") or 0.0
    vram = gpu.get("vram_gb", 0.0)
    return (gpu.get("kind") != "apple" and vram >= 16) or (gpu.get("kind") == "apple" and ram >= 32)


def _can_run_balanced(hw: dict) -> bool:
    """True when the box can run the balanced tier (llama3.1:8b, ~4.9 GB) with dignity: ANY GPU/Apple
    (an 8B fits even a modest 8 GB card / 16 GB Mac), or a strong CPU (≥16 GB AND ≥8 cores). A weak
    CPU-only box (a 4-core i3, low RAM) stays on light/ultralight. Single definition, shared by
    recommend() and visible_catalog() so the recommendation and the curated chips can't drift."""
    if _capable_gpu(hw):
        return True                                     # capable-for-14B implies capable-for-8B
    if hw.get("gpu") is not None:
        return True                                     # any GPU/Apple runs an 8B fine
    return (hw.get("ram_gb") or 0.0) >= 16 and (hw.get("cpu_cores") or 1) >= 8


def visible_catalog(hw: dict) -> list[dict]:
    """The curated model chips a user should SEE in the picker — gated to what THEIR machine can run
    with dignity, instead of showing everyone every tier. Rationale (product): a big model on a
    normal/weak box is a bad first experience (scary download, eats/thrashes RAM). So:
      * ultralight (qwen2.5:1.5b), light (qwen2.5:3b = DEFAULT) and catalan (salamandra) → ALWAYS
        shown: small, non-SWA, run everywhere with dignity.
      * balanced (llama3.1:8b, ~4.9 GB) → any GPU/Apple or a strong CPU (see _can_run_balanced).
      * powerful (qwen2.5:14b, ~9 GB) → only where a GPU/Apple runs it well (see _capable_gpu); a
        CPU-only box (incl. a 4-core i3) never sees it.
    The free-text "any Ollama model name" field (power-user, at-own-risk) is SEPARATE and unaffected
    — a technical user who wants a huge model can still type it. Uses the SAME gates as recommend()
    (_can_run_balanced / _capable_gpu), so chips and recommendation can never disagree about fit."""
    tiers = {"ultralight", "light", "catalan"}          # always safe to offer, run everywhere
    if _can_run_balanced(hw):
        tiers.add("balanced")                           # 8 B: any GPU/Apple or a strong CPU
    if _capable_gpu(hw):
        tiers.add("powerful")                           # 14 B: only where a GPU/Apple runs it well
    return [c for c in CATALOG if c["tier"] in tiers]


def recommend(hw: dict, lang: str = "auto") -> dict:
    """Map detected hardware (and the reply language) → a curated model. Catalan gets Salamandra
    (BSC, Apache-2.0) — measurably better Catalan than qwen2.5:3b and non-SWA/CPU-fast; qwen2.5:3b
    stays the GLOBAL default for everything else. A capable GPU/Apple box is auto-recommended the
    powerful tier (qwen2.5:14b, non-SWA); CPU-only boxes NEVER get it (single-digit tok/s). A mid box
    (any GPU/Apple, or a strong CPU ≥16 GB/≥8c) gets the balanced tier (llama3.1:8b, non-SWA); a
    CPU-only box CAPS at balanced. NOTHING here blocks a user: every path returns a model the machine
    can run, and the free-text field remains for power users.

    Weak CPU-only machines get the ultra-light tier (qwen2.5:1.5b): measured on an i3 (2c/4t, no GPU)
    at ~0.65 s first token vs qwen2.5:3b's ~2.25 s and ~half the download, same family (non-SWA, so
    keep_alive/prewarm still bite) — usable-but-lighter, offered as an OPTION here, never a silent
    global downgrade (measurement 2026-09-14). qwen2.5:3b remains more careful on health/allergy
    recall, so it stays the default anywhere the machine can run it."""
    if lang == "ca":
        return _BY_TIER["catalan"]
    ram = hw.get("ram_gb") or 0.0
    cores = hw.get("cpu_cores") or 1

    if _capable_gpu(hw):                              # GPU/Apple that runs 14B with dignity
        tier = "powerful"
    elif _can_run_balanced(hw):                      # modest GPU/Apple, or strong CPU → 8B (CPU caps here)
        tier = "balanced"
    elif ram < 8 or cores <= 4:                      # weak CPU-only or low RAM → ultra-light (qwen2.5:1.5b)
        tier = "ultralight"
    else:                                            # normal CPU-only → the 3b default
        tier = "light"
    return _BY_TIER[tier]
