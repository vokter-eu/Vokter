"""No-network unit test for model_pull.PullParser.

The main fixture (testdata/ollama_pull_allminilm-0.31.1.jsonl) is a REAL stream
captured from the ollama binary we ship (0.31.1) pulling `all-minilm` — same
schema as the ~2 GB models, just small. Run: `python3 model_pull_test.py`.
"""

import json
import pathlib

from model_pull import (
    PullParser,
    PHASE_MANIFEST, PHASE_DOWNLOADING, PHASE_VERIFYING,
    PHASE_WRITING, PHASE_DONE, PHASE_ERROR,
)

FIXTURE = pathlib.Path(__file__).parent / "testdata" / "ollama_pull_allminilm-0.31.1.jsonl"


def _feed(objs):
    p = PullParser()
    return [p.update(o) for o in objs]


def test_real_stream_phases_and_monotonic():
    objs = [json.loads(l) for l in FIXTURE.read_text().splitlines() if l.strip()]
    snaps = _feed(objs)

    # (1) The phases Ollama walks appear, in order, as a subsequence.
    seq = [s.phase for s in snaps]
    expected = [PHASE_MANIFEST, PHASE_DOWNLOADING, PHASE_VERIFYING, PHASE_WRITING, PHASE_DONE]
    it = iter(seq)
    assert all(ph in it for ph in expected), f"phases out of order: {seq}"

    # (2) THE guarantee: the displayed percent NEVER moves backward. The naive
    # aggregation regresses once (100.00 -> 99.98 when the 11 KB layer appears);
    # the monotonic clamp must erase it.
    pcts = [s.percent for s in snaps]
    for a, b in zip(pcts, pcts[1:]):
        assert b >= a - 1e-9, f"percent went backward: {a} -> {b}"

    # (3) The download reaches a full 100% (dominant layer completes).
    assert max(s.percent for s in snaps if s.phase == PHASE_DOWNLOADING) == 100.0

    # (4) Terminal state is a determinate 100% done.
    assert snaps[-1].phase == PHASE_DONE
    assert snaps[-1].percent == 100.0 and snaps[-1].indeterminate is False

    # (5) Indeterminate flag tracks the phase (spinner text vs filled bar).
    for s in snaps:
        want = s.phase in (PHASE_MANIFEST, PHASE_VERIFYING, PHASE_WRITING)
        assert s.indeterminate is want, f"{s.phase} indeterminate={s.indeterminate}"


def test_backward_jump_guard_extreme():
    # The regression at its worst: a tiny layer completes to 100%, THEN a big
    # layer is announced. Naive percent would crash 100 -> 10; the clamp holds.
    p = PullParser()
    p.update({"status": "pulling manifest"})
    a = p.update({"status": "pulling aaa", "digest": "aaa", "total": 100, "completed": 100})
    assert a.percent == 100.0
    b = p.update({"status": "pulling bbb", "digest": "bbb", "total": 900, "completed": 0})
    assert b.percent == 100.0, f"clamp failed, bar jumped back to {b.percent}"
    # As the big layer fills, the aggregate climbs past the clamp again.
    c = p.update({"status": "pulling bbb", "digest": "bbb", "total": 900, "completed": 900})
    assert c.percent == 100.0 and c.completed == 1000 and c.total == 1000


def test_error_line_surfaces():
    p = PullParser()
    p.update({"status": "pulling manifest"})
    s = p.update({"error": "pull model manifest: file does not exist"})
    assert s.phase == PHASE_ERROR
    assert s.error and "does not exist" in s.error


def test_first_manifest_is_indeterminate_zero():
    s = PullParser().update({"status": "pulling manifest"})
    assert s.phase == PHASE_MANIFEST and s.indeterminate is True and s.percent == 0.0


def test_as_event_shape():
    # The transport dict main.js will receive (caller adds `label` on top).
    s = PullParser().update({"status": "pulling x", "digest": "x", "total": 200, "completed": 50})
    ev = s.as_event()
    assert ev == {"phase": PHASE_DOWNLOADING, "completed": 50, "total": 200,
                  "percent": 25.0, "indeterminate": False, "error": None}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nALL GREEN — {len(tests)} tests")
