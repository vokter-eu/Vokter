// Plain-node unit test for the loading-screen render mapping. No browser.
// Run: `node loading.js.test.js`.
'use strict';

const assert = require('assert');
const { applyProgress } = require('./loading.js');

// A fake DOM adapter that records what applyProgress did.
function spyEls() {
  const state = { title: null, status: null, fill: null, mode: null, note: null, calls: 0 };
  return {
    state,
    setTitle: (t) => { state.title = t; state.calls++; },
    setStatus: (t) => { state.status = t; state.calls++; },
    setNote: (t) => { state.note = t; state.calls++; },
    setFill: (p) => { state.fill = p; state.calls++; },
    mode: (m) => { state.mode = m; state.calls++; },
  };
}

// 1. Downloading → determinate bar, the "1 of 2 · 43%" copy shape (English).
{
  const e = spyEls();
  applyProgress({ phase: 'downloading', index: 1, count: 2, model: 'all-minilm', percent: 42.5 }, e);
  assert.strictEqual(e.state.mode, 'bar');
  assert.strictEqual(e.state.fill, 42.5);
  assert.strictEqual(e.state.title, "Downloading Vokter's model");
  assert.strictEqual(e.state.status, '1 of 2 · 43%'); // 42.5 rounds to 43
}

// 2. NO Ollama jargon ever reaches the screen — not the model id, not "pulling".
{
  const e = spyEls();
  applyProgress({ phase: 'downloading', index: 2, count: 2, model: 'all-minilm', percent: 10 }, e);
  const shown = (e.state.title + ' ' + e.state.status).toLowerCase();
  for (const jargon of ['all-minilm', 'pulling', 'sha256', 'blob', 'ollama', 'manifest']) {
    assert.ok(!shown.includes(jargon), `jargon leaked to UI: ${jargon}`);
  }
}

// 3. Non-download phases → spinner, human text, never a bar.
{
  for (const [phase, text] of [
    ['manifest', 'Preparing the download…'],
    ['verifying', 'Verifying the download…'],
    ['writing', 'Almost there…'],
  ]) {
    const e = spyEls();
    applyProgress({ phase, index: 1, count: 2, percent: 100 }, e);
    assert.strictEqual(e.state.mode, 'spinner', `${phase} should be spinner`);
    assert.strictEqual(e.state.status, text);
  }
}

// 4. done on the LAST model → "Starting Vokter…" spinner (never a 100% bar).
{
  const e = spyEls();
  applyProgress({ phase: 'done', index: 2, count: 2, percent: 100 }, e);
  assert.strictEqual(e.state.mode, 'spinner');
  assert.strictEqual(e.state.title, 'Starting Vokter…');
}

// 5. done on a NON-last model → transient, screen untouched (no flicker).
{
  const e = spyEls();
  applyProgress({ phase: 'done', index: 1, count: 2, percent: 100 }, e);
  assert.strictEqual(e.state.calls, 0, 'non-last done must not touch the screen');
}

// 6. Percent is clamped to [0,100] before it drives the bar width.
{
  const hi = spyEls(); applyProgress({ phase: 'downloading', index: 1, count: 2, percent: 150 }, hi);
  assert.strictEqual(hi.state.fill, 100);
  const lo = spyEls(); applyProgress({ phase: 'downloading', index: 1, count: 2, percent: -5 }, lo);
  assert.strictEqual(lo.state.fill, 0);
}

// 7. DISCLOSURE (beacon fix #2): while the base model downloads, the note states the size and that
//    this is the ONLY thing fetched — so the user proceeds informed. 986048512 B → "~1.0 GB".
{
  const e = spyEls();
  applyProgress({ phase: 'downloading', index: 1, count: 2, model: 'qwen', percent: 30, total: 986048512 }, e);
  assert.ok(/~\d+(\.\d+)?\s(MB|GB)/.test(e.state.note), `note must disclose the size, got: ${e.state.note}`);
  assert.ok(/986 MB/.test(e.state.note), `986048512 B should read ~986 MB, got: ${e.state.note}`);
  assert.ok(/nothing else leaves your machine/i.test(e.state.note), 'note must state nothing else leaves');
  assert.ok(/runs entirely on your device/i.test(e.state.note), 'note must state it runs locally');
}

// 8. Disclosure still shows (without a size) when total is unknown; no jargon leaks into the note.
{
  const e = spyEls();
  applyProgress({ phase: 'downloading', index: 1, count: 2, model: 'qwen2.5:1.5b', percent: 5 }, e);
  assert.ok(/nothing else leaves/i.test(e.state.note), 'disclosure present even without a size');
  assert.ok(!/GB|MB/.test(e.state.note), 'no fabricated size when total is unknown');
  for (const jargon of ['qwen', 'pulling', 'sha256', 'ollama']) {
    assert.ok(!e.state.note.toLowerCase().includes(jargon), `jargon in note: ${jargon}`);
  }
}

// 9. After the LAST model's done, the disclosure note is cleared (download finished).
{
  const e = spyEls();
  applyProgress({ phase: 'done', index: 2, count: 2, percent: 100 }, e);
  assert.strictEqual(e.state.note, '', 'note cleared when downloads finish');
}

console.log('ALL GREEN — 9 loading-render cases');
