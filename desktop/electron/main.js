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
// or move the key to the OS keychain (that is 3.2). The Node side reimplements
// ZERO orchestration — orchestrator.py remains the single source of truth for
// booting and supervising Ollama + the backend.
// (Phase 3.3-D later adds ONE thing: a one-way, receive-only preload channel
// that relays the orchestrator's model-download progress to the loading screen.)

const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');
const { LineBuffer, parseProgressLine } = require('./progress_pipe');

// …/Vokter/desktop/electron -> …/Vokter (dev layout only; not valid when packaged)
const REPO_ROOT = path.resolve(__dirname, '..', '..');

// Phase 3.3-C (C0): resolve the desktop "home" — where the frozen binary and its
// runtime resources (Ollama, etc.) live — from WHERE WE ACTUALLY ARE:
//   * dev (electron .)         -> the repo's desktop/ tree, exactly as before.
//   * packaged (.deb/AppImage) -> the extraResources copy under resourcesPath,
//       laid out to MIRROR desktop/ so the orchestrator finds everything by its
//       usual relative paths. We hand that home to the child via
//       VOKTER_DESKTOP_HOME (which orchestrator._here() already honours), because
//       the orchestrator's own parents[3] guess assumes the dev layout, which a
//       package does not reproduce.
// In dev, DESKTOP_HOME === REPO_ROOT/desktop, so every path below is byte-for-byte
// what it was before — dev behaviour is unchanged.
const PACKAGED = app.isPackaged;
const DESKTOP_HOME = PACKAGED
  ? path.join(process.resourcesPath, 'desktop')
  : path.join(REPO_ROOT, 'desktop');

const ORCHESTRATOR = path.join(DESKTOP_HOME, 'orchestrator.py');
const PYTHON = process.env.VOKTER_PYTHON || 'python3';
// The frozen binary's --orchestrate mode (3.3-A): boots the whole stack from
// inside the bundle, so a user machine needs no system python3.
const FROZEN_BIN = path.join(DESKTOP_HOME, 'freeze', 'dist', 'vokter-backend', 'vokter-backend');
const VENV_DIR = path.join(DESKTOP_HOME, 'runtime', 'venv');

// Mirror orchestrator.backend_flavour(): a dev box (has the venv) runs the
// Python source so freshly edited code is never shadowed by a stale freeze; a
// user machine (no venv) runs the frozen binary. VOKTER_DESKTOP_ORCHESTRATOR
// forces 'python' or 'frozen'.
function orchestratorCommand() {
  const forced = (process.env.VOKTER_DESKTOP_ORCHESTRATOR || '').trim().toLowerCase();
  // A packaged app has neither a venv nor a system python3 to rely on — always
  // the frozen binary. In dev, keep the venv-vs-frozen choice unchanged.
  const useFrozen = PACKAGED || forced === 'frozen' || (forced !== 'python' && !fs.existsSync(VENV_DIR));
  return useFrozen
    ? { cmd: FROZEN_BIN, args: ['--orchestrate'] }
    : { cmd: PYTHON, args: [ORCHESTRATOR] };
}

// Must match orchestrator.py's BACKEND_PORT (same env var, same default).
const PORT = parseInt(process.env.VOKTER_DESKTOP_BACKEND_PORT || '8081', 10);
const HEALTH_URL = `http://127.0.0.1:${PORT}/`;

let win = null;
let child = null;
let childExited = false;   // orchestrator process has terminated
let ready = false;         // backend answered → real UI is loaded
let lastProgress = null;   // most recent download-progress event, for replay
const stderrTail = [];     // last lines of orchestrator stderr, for the error screen

function createWindow() {
  win = new BrowserWindow({
    width: 1100,
    height: 780,
    title: 'Vokter',
    backgroundColor: '#0f1115',
    webPreferences: {
      // We only ever load trusted localhost, and keep the renderer locked down:
      // contextIsolation ON, no nodeIntegration, sandboxed. The preload is a
      // single ONE-WAY receive channel (download progress) — see preload.js
      // (Phase 3.3-D). No send/invoke is exposed to the page.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  win.loadFile(path.join(__dirname, 'loading.html'));

  // The loading page may finish loading AFTER the first progress events arrive
  // (Ollama can start pulling within a second). Replay the latest event once the
  // page is ready so the bar never starts blank. Guard on !ready so this never
  // fires for the real UI (which loads only after ready flips true).
  win.webContents.on('did-finish-load', () => {
    if (!ready && lastProgress && win && !win.isDestroyed()) {
      win.webContents.send('download-progress', lastProgress);
    }
  });
}

// A parsed [progress] event from the orchestrator → the loading screen. Only
// while we're still on that screen (!ready); once the backend is up the real UI
// has replaced it and there is nothing more to download.
function onProgress(ev) {
  lastProgress = ev;
  if (win && !win.isDestroyed() && !ready) {
    win.webContents.send('download-progress', ev);
  }
}

function startOrchestrator() {
  const { cmd, args } = orchestratorCommand();
  // When packaged, tell the orchestrator where its resources live (see
  // DESKTOP_HOME above) and give it a real cwd — REPO_ROOT points inside the
  // asar and is not a usable directory. In dev nothing changes.
  const env = { ...process.env };
  if (PACKAGED) env.VOKTER_DESKTOP_HOME = DESKTOP_HOME;
  child = spawn(cmd, args, {
    cwd: PACKAGED ? DESKTOP_HOME : REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env,
  });

  // Surface the orchestrator's own logs in our stdout so `[orchestrator] …`
  // and, critically, `[orchestrator] FATAL: …` lines are visible — AND pick the
  // machine-readable `[progress] …` lines out of the same stream to drive the
  // download bar. stdout arrives as byte chunks, not lines, so we buffer and
  // split on '\n' (see progress_pipe) before parsing — otherwise a JSON line
  // split across two chunks would parse-fail intermittently.
  const outBuf = new LineBuffer();
  child.stdout.on('data', (d) => {
    process.stdout.write(d);
    for (const line of outBuf.push(d)) {
      const ev = parseProgressLine(line);
      if (ev) onProgress(ev);
    }
  });
  child.stderr.on('data', (d) => {
    process.stderr.write(d);
    stderrTail.push(d.toString());
    while (stderrTail.length > 40) stderrTail.shift();
  });

  // Spawn itself failing (ENOENT on a wrong FROZEN_BIN path or an unusable cwd —
  // exactly the failure mode C0's packaged paths could hit) emits 'error', NOT
  // 'exit'. Without this handler that error is unhandled and the user sits on the
  // loading spinner forever. Route it to the same diagnostic error screen.
  child.on('error', (err) => {
    childExited = true;
    stderrTail.push(`failed to launch the background service: ${err.message}\n`);
    if (!ready) showError(null, err.code || 'spawn-error');
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
