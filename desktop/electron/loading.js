// Phase 3.3-D (step 2c): render download progress on the loading screen.
//
// applyProgress() is PURE — it takes an event and an `els` adapter (setTitle,
// setStatus, setFill, mode) — so the phase→UI mapping unit-tests in node with a
// fake adapter (loading.js.test.js). The browser bootstrap at the bottom wires
// that adapter to the real DOM and to window.vokter.onDownloadProgress (the
// one-way channel from preload.js).
//
// Copy rules (Bilal): reassuring, human, NO Ollama jargon on screen — the user
// never sees "pulling", "blob", a sha256, or a model id. A determinate bar while
// bytes flow; a spinner (never a bar stuck at 100%) for the non-download phases.
// English, to match the rest of the UI (app/static/index.html). All on-screen
// wording lives HERE — the orchestrator emits structured index/count, not text —
// so future i18n has a single place to touch.

'use strict';

const PHASE_TEXT = {
  manifest: 'Preparing the download…',
  verifying: 'Verifying the download…',
  writing: 'Almost there…',
};

function applyProgress(p, els) {
  if (p.phase === 'downloading') {
    const pct = Math.max(0, Math.min(100, p.percent));
    els.mode('bar');
    els.setFill(pct);
    els.setTitle("Downloading Vokter's model");
    const ofN = (p.index && p.count) ? `${p.index} of ${p.count} · ` : '';
    els.setStatus(ofN + Math.round(pct) + '%');
    return;
  }
  if (p.phase === 'done') {
    // done fires PER model. Only the last one means "downloads finished, backend
    // is coming up". A non-last done is transient — the next model's events land
    // in milliseconds — so leave the screen as-is and avoid a flicker.
    const last = (p.index && p.count) ? p.index === p.count : true;
    if (last) {
      els.mode('spinner');
      els.setTitle('Starting Vokter…');
      els.setStatus('Almost ready.');
    }
    return;
  }
  // manifest / verifying / writing / anything else → indeterminate spinner,
  // never a filled bar.
  els.mode('spinner');
  els.setTitle("Downloading Vokter's model");
  els.setStatus(PHASE_TEXT[p.phase] || 'Preparing…');
}

// --- browser bootstrap (skipped under node's unit test) ---------------------
if (typeof document !== 'undefined') {
  const $ = (s) => document.querySelector(s);
  const spinner = $('.spinner');
  const progress = $('.progress');
  const fill = $('.fill');
  const els = {
    setTitle: (t) => { $('.title').textContent = t; },
    setStatus: (t) => { $('.status').textContent = t; },
    setFill: (pct) => { fill.style.width = pct + '%'; },
    mode: (m) => {
      const bar = m === 'bar';
      progress.style.display = bar ? '' : 'none';
      spinner.style.display = bar ? 'none' : '';
    },
  };
  if (typeof window !== 'undefined' && window.vokter) {
    window.vokter.onDownloadProgress((p) => applyProgress(p, els));
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { applyProgress };
}
