"""Parse Ollama's streaming /api/pull progress into a normalised, UI-friendly
snapshot. PURE: feed it the JSON objects from the stream, read the snapshot.
No network, no I/O — so it unit-tests against a captured fixture (see
model_pull_test.py) taken from the exact ollama version we ship (0.31.1).

Why this exists (3.3-D): the first-run ~2 GB model download must show a real
progress bar in the Electron window, not a spinner that looks hung. This module
turns the per-LAYER stream into a per-MODEL bar ("modelo 1 de 2 · 43%"):

  * Ollama reports progress per digest (layer); a model has several layers.
    We aggregate: percent = sum(completed) / sum(total) over the layers SEEN.
  * The displayed percent is clamped MONOTONIC (never moves backward) so a new
    layer appearing — which grows the denominator — cannot make the bar jump
    back. Bilal's rule: a backward bar breeds more distrust than a spinner.
  * The non-download phases (pulling manifest / verifying / writing manifest)
    carry no meaningful %, so they surface as `indeterminate` — the UI shows a
    moving "Verificando…" / "Arrancando Vokter…" state, never a bar at 100%.

The per-model LABEL ("modelo 1 de 2") is added by the caller (orchestrator),
which knows the index/count; this parser stays generic — {phase, completed,
total, percent, indeterminate} — so the future optional GPU-runner download
reuses it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Phases, in the order Ollama walks them. Only DOWNLOADING has a real percent.
PHASE_MANIFEST = "manifest"      # "pulling manifest"          — indeterminate
PHASE_DOWNLOADING = "downloading"  # "pulling <digest>"        — has %/bytes
PHASE_VERIFYING = "verifying"    # "verifying sha256 digest"   — indeterminate
PHASE_WRITING = "writing"        # "writing manifest"          — indeterminate
PHASE_DONE = "done"              # "success"                   — 100 %
PHASE_ERROR = "error"            # {"error": "..."}            — failed

_INDETERMINATE = {PHASE_MANIFEST, PHASE_VERIFYING, PHASE_WRITING}


@dataclass
class Snapshot:
    """Normalised progress for ONE model pull. The caller wraps this with a
    label (e.g. 'modelo 1 de 2') before sending it to the UI."""
    phase: str
    completed: int = 0        # bytes downloaded so far, summed across layers
    total: int = 0            # bytes known so far, summed across layers seen
    percent: float = 0.0      # 0..100, MONOTONIC within a model
    indeterminate: bool = True  # True → spinner text, not a filled bar
    error: str | None = None

    def as_event(self) -> dict:
        """Plain dict for JSON transport to main.js (the caller adds `label`)."""
        return {
            "phase": self.phase,
            "completed": self.completed,
            "total": self.total,
            "percent": round(self.percent, 1),
            "indeterminate": self.indeterminate,
            "error": self.error,
        }


@dataclass
class PullParser:
    """Stateful across a single model's stream. Feed it each decoded JSON object
    with update(); read the returned Snapshot. One instance per model."""

    _layers: dict[str, dict] = field(default_factory=dict)  # digest -> {completed,total}
    _percent_hwm: float = 0.0                               # monotonic high-water mark
    _phase: str = PHASE_MANIFEST

    def update(self, obj: dict) -> Snapshot:
        # An error object aborts the pull; surface it as its own phase.
        if obj.get("error"):
            self._phase = PHASE_ERROR
            return Snapshot(phase=PHASE_ERROR, percent=self._percent_hwm,
                            indeterminate=True, error=str(obj["error"]))

        status = obj.get("status", "")

        if status == "success":
            self._phase = PHASE_DONE
            return Snapshot(phase=PHASE_DONE, completed=self._sum_completed(),
                            total=self._sum_total(), percent=100.0,
                            indeterminate=False)

        if status == "pulling manifest":
            self._phase = PHASE_MANIFEST
            return self._indeterminate(PHASE_MANIFEST)

        if status == "verifying sha256 digest":
            self._phase = PHASE_VERIFYING
            return self._indeterminate(PHASE_VERIFYING)

        if status == "writing manifest":
            self._phase = PHASE_WRITING
            return self._indeterminate(PHASE_WRITING)

        # Everything else that carries a digest is a download layer. We key on
        # the digest (not the truncated status text) so re-emitted lines for the
        # same layer just overwrite that layer's tally.
        digest = obj.get("digest")
        if digest is not None and "total" in obj:
            self._phase = PHASE_DOWNLOADING
            layer = self._layers.setdefault(digest, {"completed": 0, "total": 0})
            layer["total"] = obj["total"]
            # `completed` is absent until the first byte; keep the last known.
            if "completed" in obj:
                layer["completed"] = obj["completed"]
            return self._downloading()

        # Unknown status with no digest (forward-compat): treat as indeterminate
        # without losing the monotonic percent we already showed.
        return self._indeterminate(self._phase if self._phase in _INDETERMINATE
                                   else PHASE_MANIFEST)

    # --- helpers -------------------------------------------------------------
    def _sum_total(self) -> int:
        return sum(l["total"] for l in self._layers.values())

    def _sum_completed(self) -> int:
        return sum(l["completed"] for l in self._layers.values())

    def _downloading(self) -> Snapshot:
        total = self._sum_total()
        completed = self._sum_completed()
        pct = (100.0 * completed / total) if total > 0 else 0.0
        # Monotonic clamp: a new layer growing the denominator must not pull the
        # bar backward.
        self._percent_hwm = max(self._percent_hwm, pct)
        return Snapshot(phase=PHASE_DOWNLOADING, completed=completed, total=total,
                        percent=self._percent_hwm, indeterminate=False)

    def _indeterminate(self, phase: str) -> Snapshot:
        return Snapshot(phase=phase, completed=self._sum_completed(),
                        total=self._sum_total(), percent=self._percent_hwm,
                        indeterminate=True)
