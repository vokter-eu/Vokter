"""Hardware → model tier mapping (the SSOT for first-run pull + the Settings picker).

Pure, stdlib-only, no db/net/model — mirrors how hwdetect is imported by BOTH the backend
(app/hardware.py) and the stdlib-only desktop orchestrator (first-run pick). Locks the invariants
that two code reviews flagged as untested:
  * the hardware→tier mapping (weak CPU → ultralight, normal → light, capable GPU/Apple → powerful,
    Apple needs ≥32 GB, CPU-only NEVER powerful, unknown/zero hardware degrades to a runnable tier);
  * _capable_gpu is the SINGLE gate → recommend()'s tier is ALWAYS present in visible_catalog(hw),
    so the recommendation and the curated chips can never disagree (the comment's promise, enforced);
  * lang='ca' → Salamandra regardless of hardware.

Run:  python3 tests/hwdetect_test.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import hwdetect


def _hw(cores=4, ram=8.0, gpu=None):
    return {"os": "Linux", "arch": "x86_64", "cpu_cores": cores, "ram_gb": ram, "gpu": gpu}


def _nvidia(vram):
    return {"kind": "nvidia", "vram_gb": vram, "name": "test"}


def _apple(ram):
    return {"kind": "apple", "vram_gb": ram, "name": "Apple Silicon"}  # unified: vram==ram


CASES = [
    # (name, hw, expected tier)
    ("weak i3 (2c/4t, 8GB, no GPU)",      _hw(4, 8.0, None),                 "ultralight"),
    ("low RAM (8c, 4GB, no GPU)",         _hw(8, 4.0, None),                 "ultralight"),
    ("normal CPU (6c, 12GB, no GPU)",     _hw(6, 12.0, None),                "light"),      # not strong enough for 8B
    ("strong CPU (8c, 16GB, no GPU)",     _hw(8, 16.0, None),                "balanced"),   # caps at balanced, never 14B
    ("big CPU-only (16c, 64GB, no GPU)",  _hw(16, 64.0, None),               "balanced"),   # CPU never powerful
    ("discrete GPU 24GB VRAM",            _hw(16, 32.0, _nvidia(24)),        "powerful"),
    ("discrete GPU 8GB VRAM",             _hw(8, 16.0, _nvidia(8)),          "balanced"),   # 8B fits, 14B doesn't
    ("Apple 16GB unified",                _hw(8, 16.0, _apple(16)),          "balanced"),   # 8B ok, 14B tight
    ("Apple 32GB unified",                _hw(10, 32.0, _apple(32)),         "powerful"),
    ("unknown hardware (ram=0)",          _hw(1, 0.0, None),                 "ultralight"), # degrades safe
]


def test_recommend_mapping():
    for name, hw, exp in CASES:
        got = hwdetect.recommend(hw)["tier"]
        assert got == exp, f"{name}: recommend → {got}, expected {exp}"


def test_recommend_tier_always_visible():
    # The invariant the code comments promise: recommend()'s pick is always a chip the picker shows.
    for name, hw, _exp in CASES:
        rec_tier = hwdetect.recommend(hw)["tier"]
        visible = {c["tier"] for c in hwdetect.visible_catalog(hw)}
        assert rec_tier in visible, f"{name}: recommend tier {rec_tier} not in visible {visible}"


def test_cpu_only_never_powerful():
    for cores in (4, 8, 16, 64):
        for ram in (8.0, 16.0, 64.0, 256.0):
            r = hwdetect.recommend(_hw(cores, ram, None))
            assert r["tier"] != "powerful", f"CPU-only {cores}c/{ram}GB got powerful"
            assert "powerful" not in {c["tier"] for c in hwdetect.visible_catalog(_hw(cores, ram, None))}


def test_balanced_model_and_gate():
    # Mid tier resolves to the qualified llama3.1:8b; CPU-only tops out at balanced (never 14B).
    assert hwdetect.recommend(_hw(8, 16.0, None))["model"] == "llama3.1:8b"
    assert hwdetect.recommend(_hw(16, 64.0, None))["tier"] == "balanced"
    assert hwdetect._can_run_balanced(_hw(8, 16.0, None)) is True      # strong CPU
    assert hwdetect._can_run_balanced(_hw(6, 12.0, None)) is False     # not strong enough
    assert hwdetect._can_run_balanced(_hw(8, 16.0, _nvidia(8))) is True  # any GPU
    assert hwdetect._can_run_balanced(_hw(4, 8.0, None)) is False      # weak i3
    # _capable_gpu ⟹ _can_run_balanced (14B-capable box can obviously run 8B)
    beefy = _hw(16, 32.0, _nvidia(24))
    assert hwdetect._capable_gpu(beefy) and hwdetect._can_run_balanced(beefy)


def test_catalan_overrides_hardware():
    # Even a powerful box replies Catalan with Salamandra (better ca), not the 14B.
    beefy = _hw(16, 32.0, _nvidia(24))
    assert hwdetect.recommend(beefy, "ca")["model"] == "salamandra-2b-instruct"


def test_capable_gpu_gate():
    assert hwdetect._capable_gpu(_hw(8, 16.0, _nvidia(16))) is True
    assert hwdetect._capable_gpu(_hw(8, 16.0, _nvidia(8))) is False
    assert hwdetect._capable_gpu(_hw(8, 16.0, _apple(16))) is False   # Apple needs 32GB
    assert hwdetect._capable_gpu(_hw(8, 32.0, _apple(32))) is True
    assert hwdetect._capable_gpu(_hw(64, 256.0, None)) is False       # CPU-only never


def test_every_catalog_tier_resolvable():
    # _BY_TIER must hold every tier recommend()/visible_catalog can emit (no KeyError path).
    emitted = set()
    for _n, hw, _e in CASES:
        emitted.add(hwdetect.recommend(hw)["tier"])
        emitted |= {c["tier"] for c in hwdetect.visible_catalog(hw)}
    emitted.add("catalan")
    for tier in emitted:
        assert tier in hwdetect._BY_TIER, f"tier {tier} missing from _BY_TIER"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} hwdetect tests passed.")
