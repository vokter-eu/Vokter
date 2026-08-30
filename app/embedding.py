"""Embedding storage — packed float32 BLOBs, with a robust reader for the
JSON-text → BLOB transition (Direction A / A3).

An embedding used to be stored as a JSON array of doubles in a TEXT column
(~8 chars/dim on disk, json.loads + a pure-Python cosine per row at query
time). We now store it as a packed little-endian float32 BLOB (4 bytes/dim):
smaller, and it loads straight into a numpy matrix with zero parsing.

`unpack_embedding` reads BOTH forms so nothing breaks mid-migration: a freshly
packed BLOB, or a legacy JSON string that the background repack hasn't rewritten
yet. Callers dimension-guard the result before stacking (a row embedded with a
different model has a different length and must be skipped, not crash the batch).
"""
import json

import numpy as np

# float32: half the bytes of numpy's default float64, and nomic-embed-text (and
# every embedder we ship) is fine at f32 precision for cosine ranking.
DTYPE = np.float32


def pack_embedding(vec) -> bytes:
    """A model embedding (list[float] or ndarray) → packed float32 bytes for the DB."""
    return np.asarray(vec, dtype=DTYPE).tobytes()


def unpack_embedding(val) -> np.ndarray | None:
    """DB value → 1-D float32 ndarray, or None if unreadable.

    Accepts bytes/blob (the new form) OR str (legacy JSON, still on disk until the
    background repack rewrites it). Returns None — never raises — on anything it
    can't read, so one bad/foreign-dimension row is skipped, not fatal to the scan.
    """
    if val is None:
        return None
    try:
        if isinstance(val, (bytes, bytearray, memoryview)):
            buf = bytes(val)
            if len(buf) % DTYPE().itemsize:
                return None                      # truncated/corrupt blob
            a = np.frombuffer(buf, dtype=DTYPE)
        else:
            a = np.asarray(json.loads(val), dtype=DTYPE)
    except Exception:
        return None
    return a if a.ndim == 1 and a.size else None
