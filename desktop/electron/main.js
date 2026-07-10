// Vokter desktop shell — Phase 3.1: the "hollow" Electron window.
//
// Scope of this phase, deliberately narrow:
//   * launch the EXISTING Python orchestrator (desktop/orchestrator.py) as a child,
//   * show a loading screen until the backend answers on 127.0.0.1:8081,
//   * then load that URL into the window,
//   * and — the part that actually matters — shut the orchestrator down cleanly
//     on quit so we never orphan Ollama or the backend holding the port.
//
// What this phase does NOT do (on purpose): package anything, touch the DB key,
// move the key to the OS keychain (that is 3.2), or add IPC/preload. The Node
// side reimplements ZERO orchestration — orchestrator.py remains the single
// source of truth for booting and supervising Ollama + the backend.

const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');

// …/Vokter/desktop/electron -> …/Vokter
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const ORCHESTRATOR = path.join(REPO_ROOT, 'desktop', 'orchestrator.py');
const PYTHON = process.env.VOKTER_PYTHON || 'python3';

// Must match orchestrator.py's BACKEND_PORT (same env var, same default).
const PORT = parseInt(process.env.VOKTER_DESKTOP_BACKEND_PORT || '8081', 10);
const HEALTH_URL = `http://127.0.0.1:${PORT}/`;

let win = null;
let child = null;
let childExited = false;   // orchestrator process has terminated
let ready = false;         // backend answered → real UI is loaded
const stderrTail = [];     // last lines of orchestrator stderr, for the error screen

function createWindow() {
  win = new BrowserWindow({
    width: 1100,
    height: 780,
    title: 'Vokter',
    backgroundColor: '#0f1115',
    webPreferences: {
      // Free hygiene: we only ever load trusted localhost, but keep the
      // renderer sandboxed anyway. No preload/IPC needed for a hollow window.
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, 'loading.html'));
}

function startOrchestrator() {
  child = spawn(PYTHON, [ORCHESTRATOR], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: process.env,
  });

  // Surface the orchestrator's own logs in our stdout so `[orchestrator] …`
  // and, critically, `[orchestrator] FATAL: …` lines are visible.
  child.stdout.on('data', (d) => process.stdout.write(d));
  child.stderr.on('data', (d) => {
    process.stderr.write(d);
    stderrTail.push(d.toString());
    while (stderrTail.length > 40) stderrTail.shift();
  });

  child.on('exit', (code, signal) => {
    childExited = true;
    if (!ready) {
      // Orchestrator died before the backend ever came up (die() exits non-zero,
      // or Ollama/models failed). Stop the user staring at a spinner forever.
      showError(code, signal);
    }
    // If we were mid-quit and only waiting on the child, finish quitting now.
    if (app.isQuitting) app.quit();
  });
}

function pollUntilReady() {
  const attempt = () => {
    if (childExited || ready) return;
    const req = http.get(HEALTH_URL, (res) => {
      res.resume();
      if (res.statusCode && res.statusCode < 500) {
        ready = true;
        if (win && !win.isDestroyed()) win.loadURL(HEALTH_URL);
      } else {
        setTimeout(attempt, 500);
      }
    });
    req.on('error', () => setTimeout(attempt, 500));
    req.setTimeout(2000, () => req.destroy());
  };
  // No overall deadline here on purpose: first run pulls ~2 GB of models, which
  // can take minutes. The abort condition is the child dying (childExited),
  // handled above — not an arbitrary timer.
  attempt();
}

function showError(code, signal) {
  if (!win || win.isDestroyed()) return;
  const esc = (s) => s.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]));
  const detail = esc(stderrTail.join('').slice(-2000)) || 'No output captured.';
  const html = `<!doctype html><meta charset="utf-8">
<style>
  body { background:#0f1115; color:#e6e6e6; font-family:system-ui,-apple-system,sans-serif;
         padding:2.5rem; line-height:1.5; }
  h1 { color:#ff6b6b; font-size:1.25rem; margin:0 0 .5rem; }
  p  { color:#b8bcc4; }
  pre{ white-space:pre-wrap; background:#181b21; padding:1rem; border-radius:8px;
       color:#c9c9c9; font-size:.8rem; max-height:52vh; overflow:auto; }
</style>
<h1>Vokter could not start</h1>
<p>The background service exited (code ${code}, signal ${signal || 'none'}) before it was ready.</p>
<pre>${detail}</pre>`;
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html));
}

// --- Lifecycle: the clean-shutdown contract ---------------------------------
// On quit we SIGTERM the orchestrator and WAIT for it to exit before the app
// actually dies. orchestrator.py's own handler then terminate()s Ollama + the
// backend. If we skipped this (or SIGKILLed the parent), we would orphan a
// backend still holding :8081 and an Ollama eating RAM/GPU.
app.on('before-quit', (e) => {
  app.isQuitting = true;
  if (child && !childExited) {
    e.preventDefault();
    child.kill('SIGTERM');
    // Safety net: if the graceful stop hangs, force it. orchestrator.py already
    // gives its own children 10s before SIGKILL, so 15s here is comfortably past that.
    setTimeout(() => {
      if (!childExited && child) child.kill('SIGKILL');
    }, 15000);
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.whenReady().then(() => {
  createWindow();
  startOrchestrator();
  pollUntilReady();
});
