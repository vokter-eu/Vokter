"""Beacon fix #1: a DEFAULT boot makes NO external voice fetch. The voice assets used to be pulled
from the GitHub mirror on EVERY startup (opportunistic_startup_fetch), a phone-home even on a voiceless
install — contradicting "nothing leaves your machine". They now download ONLY on demand (the first time
the user uses voice, via /api/voice/ensure). This runs the real FastAPI lifespan with the voice-mirror
download primitive recorded and asserts it is never hit on boot — yet still works when invoked on demand.

  VOKTER_DB=/tmp/beacon.db VOKTER_DB_KEY=k desktop/runtime/venv/bin/python tests/boot_no_beacon_test.py

Bites: with the old main.py (opportunistic_startup_fetch in the lifespan) the boot records a mirror
fetch → FAIL; after the fix → PASS.
"""
import asyncio
import os
import sys
import tempfile
import time

os.environ.setdefault("VOKTER_DB", os.path.join(tempfile.mkdtemp(prefix="beacon_"), "b.db"))
os.environ.setdefault("VOKTER_DB_KEY", "test-key")
os.environ.pop("VOKTER_NOSTR_RELAYS", None)        # keep the nostr listener disabled

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import voice.fetch as vf

# Record every call to the voice-mirror download primitive (the external GitHub fetch), without
# actually reaching the network.
mirror_hits = []
def _recording_download(name, sha256, dest_dir, on_progress):
    mirror_hits.append(name)
    raise RuntimeError("test: external voice fetch blocked (recorded)")
vf._download = _recording_download

fails = []
def check(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        fails.append(desc)

import main

async def _boot():
    # Enter the real app lifespan (startup), let any boot-spawned work run, then exit.
    async with main.lifespan(main.app):
        await asyncio.sleep(1.5)

asyncio.run(_boot())
boot_hits = list(mirror_hits)
check("default boot makes ZERO external voice fetch (no beacon)", boot_hits == [])

# The on-demand download machinery is intact: ensure() → _ensure_files → _download still reaches the
# mirror for an absent asset (what /api/voice/ensure drives when the user activates voice).
mirror_hits.clear()
try:
    vf._ensure_files([("kokoro-v1.0.onnx", "deadbeef", 10)],
                     lambda name: "/tmp/vkt_absent_voice_dir/" + name, lambda n: None)
except Exception:
    pass
check("on-demand download path still reaches the mirror (voice works once activated)", len(mirror_hits) > 0)

print()
if fails:
    print("❌ boot-beacon:", fails); sys.exit(1)
print("✅ boot makes no outbound voice fetch; voice downloads only on demand")
