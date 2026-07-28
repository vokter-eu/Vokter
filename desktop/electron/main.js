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

const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const crypto = require('crypto');
const path = require('path');
const fs = require('fs');
const { LineBuffer, parseProgressLine, parseGuardrailLine } = require('./progress_pipe');
const { pickFreePort } = require('./netutil');
const { guardrailHtml } = require('./guardrail_screen');

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
// Per-instance backend port (#2): chosen once at startup — a free port unless the
// env pins one (dev/tests). main.js polls/loads ONLY this port, so an orphaned
// backend squatting on a different port can never be adopted (the zombie bug).
let backendPort = null;
const healthUrl = () => `http://127.0.0.1:${backendPort}/`;

// Human-session token (P2): minted ONCE per Electron launch and handed to the backend
// on EVERY spawn (see startOrchestrator) so it survives a Start-fresh respawn without
// desyncing — the backend re-reads the same value, the reloaded renderer re-reads it
// via the IPC proxy. It authorises personal memory to reach ONLY this local human
// session; internal callers (A2A/Nostr/MCP) never hold it. It lives ONLY in the main
// process — never in page JS — so a renderer XSS cannot steal it. See
// docs/threat-model-prompt-injection.md §7-8.
const HUMAN_SESSION_TOKEN = crypto.randomBytes(32).toString('hex');

let win = null;
let child = null;
let childExited = false;   // orchestrator process has terminated
let ready = false;         // backend answered → real UI is loaded
let lastProgress = null;   // most recent download-progress event, for replay
let guardrailEvent = null; // parsed [guardrail] facts if the boot halted at the guardrail
let halted = false;        // stopped at the guardrail screen, awaiting the user's choice
let restarting = false;    // a start-fresh respawn is in flight (anti double-click)
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

function startOrchestrator(opts = {}) {
  const { cmd, args } = orchestratorCommand();
  // When packaged, tell the orchestrator where its resources live (see
  // DESKTOP_HOME above) and give it a real cwd — REPO_ROOT points inside the
  // asar and is not a usable directory. In dev nothing changes.
  const env = { ...process.env };
  if (PACKAGED) env.VOKTER_DESKTOP_HOME = DESKTOP_HOME;
  // Bind the backend to OUR per-instance port (chosen at startup, reused across a
  // start-fresh respawn) so we only ever adopt our own child's backend.
  env.VOKTER_DESKTOP_BACKEND_PORT = String(backendPort);
  // Hand the backend THIS launch's human-session token on every spawn (initial and
  // Start-fresh respawn), exactly like the port above — so the token the backend
  // compares against and the token the renderer presents can never drift apart.
  env.VOKTER_HUMAN_SESSION_TOKEN = HUMAN_SESSION_TOKEN;
  // Start-fresh respawn (the user clicked [2]): the orchestrator, seeing this
  // flag, proceeds past the keychain guardrail create-only (see orchestrator.py).
  // Transient to THIS launch only.
  if (opts.startFresh) env.VOKTER_START_FRESH = '1';

  // Reset per-launch state; on a respawn put the loading spinner back up. `ready`
  // is reset with the rest so a respawn re-polls (pollUntilReady early-returns if
  // `ready` is still true from a prior/foreign backend).
  childExited = false;
  guardrailEvent = null;
  ready = false;
  if (opts.startFresh && win && !win.isDestroyed()) {
    win.loadFile(path.join(__dirname, 'loading.html'));
  }

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
      const prog = parseProgressLine(line);
      if (prog) { onProgress(prog); continue; }
      // The guardrail emits its structured facts just before die(); remember them
      // and render the choice screen when the child exits (below).
      const guard = parseGuardrailLine(line);
      if (guard) guardrailEvent = guard;
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

  // 'exit' fires as soon as the process dies — use it only to stop polling and to
  // finish a pending quit. The SCREEN decision waits for 'close' (below).
  child.on('exit', () => {
    childExited = true;
    if (app.isQuitting) app.quit();
  });

  // 'close' fires AFTER the stdio streams have drained, so every [guardrail] line
  // is already parsed — this closes the exit-beats-stdout race (#4). We also flush
  // any final unterminated line as a belt. With the per-instance port, `ready` is
  // true only if OUR backend answered, so a halted boot can't be masked (#3).
  child.on('close', (code, signal) => {
    for (const line of outBuf.flush()) {
      const g = parseGuardrailLine(line);
      if (g) guardrailEvent = g;
    }
    if (!app.isQuitting) {
      // A halted boot is AUTHORITATIVE: the guardrail screen wins regardless of a
      // foreign `ready` — guardrailEvent and a genuine own-backend `ready` are
      // mutually exclusive (the guardrail halts before any backend binds our port).
      if (guardrailEvent) {
        console.log('Vokter: the keychain guardrail halted the boot — showing the recovery screen.');
        showGuardrail(guardrailEvent);
      } else if (!ready) {
        console.log('Vokter: the background service exited before it was ready — showing the error screen.');
        showError(code, signal);
      }
    }
  });

  pollUntilReady();
}

function pollUntilReady() {
  const attempt = () => {
    if (childExited || ready) return;
    const req = http.get(healthUrl(), (res) => {
      res.resume();
      if (res.statusCode && res.statusCode < 500) {
        ready = true;
        if (win && !win.isDestroyed()) win.loadURL(healthUrl());
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

// The guardrail halted a blank boot. Show a reassuring, plain-language screen
// with a clickable [2] "Start fresh". Copy is composed from the structured facts
// — no "guardrail"/"keychain" jargon reaches the user.
function showGuardrail(ev) {
  if (!win || win.isDestroyed()) return;
  halted = true;
  win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(guardrailHtml(ev)));
}

// The renderer's ONE privileged request: proxy /api/ask through MAIN so the
// human-session token never touches page JS. The page sends only {question,
// conversation_id}; main attaches X-Vokter-Human-Session. Returns {status, body} so
// the renderer keeps its ok/error handling. A renderer XSS could invoke this (and it
// already sees the answer), but cannot read the token to reuse it from another process.
ipcMain.handle('vokter:ask', (_event, body) => new Promise((resolve) => {
  if (!ready || backendPort == null) { resolve({ status: 0, body: null }); return; }
  const payload = JSON.stringify(body && typeof body === 'object' ? body : {});
  const req = http.request({
    host: '127.0.0.1', port: backendPort, path: '/api/ask', method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload),
      'X-Vokter-Human-Session': HUMAN_SESSION_TOKEN,
    },
  }, (res) => {
    let data = '';
    res.setEncoding('utf8');
    res.on('data', (c) => { data += c; });
    res.on('end', () => {
      let parsed = null;
      try { parsed = JSON.parse(data); } catch { /* leave null → renderer shows error */ }
      resolve({ status: res.statusCode || 0, body: parsed });
    });
  });
  req.on('error', () => resolve({ status: 0, body: null }));
  // Local CPU inference can take minutes (backend's own chat timeout is 300s); allow past it.
  // Resolve explicitly on timeout: req.destroy() with no error argument does NOT emit
  // 'error', so without this the renderer's `await ask()` would hang forever.
  req.setTimeout(310000, () => { req.destroy(); resolve({ status: 0, body: null }); });
  req.write(payload);
  req.end();
}));

// The window's ONE outbound action: the user clicked [2] "Start fresh". Honour it
// ONLY while halted at the guardrail, and ONLY once — a nervous double click must
// not spawn two orchestrators contending for the ports and Ollama.
ipcMain.on('vokter:start-fresh', () => {
  if (!halted || restarting) return;
  restarting = true;
  halted = false;
  startOrchestrator({ startFresh: true });
});

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

// Single-instance lock (#1): only one Vokter may run. A second launch fails to
// get the lock and quits silently; the FIRST instance is notified and brings its
// window to the front. This prevents concurrent Vokters — the usual way an
// orphaned backend is born. (The start-fresh respawn is a CHILD process, not a
// new Electron instance, so the lock never interferes with it.)
if (!app.requestSingleInstanceLock()) {
  console.log('Vokter is already running — focusing the existing window and exiting.');
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore();
      win.show();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    // Honour a pinned port (dev/tests); otherwise grab a free one for this run.
    const pinned = parseInt(process.env.VOKTER_DESKTOP_BACKEND_PORT || '', 10);
    try {
      backendPort = Number.isInteger(pinned) && pinned > 0 ? pinned : await pickFreePort();
    } catch (e) {
      // Falling back to the fixed port reopens the shared-port risk — worth a shout.
      console.error('Vokter: could not obtain a free port, falling back to 8081:', e && e.message);
      backendPort = 8081;
    }
    createWindow();
    startOrchestrator();  // spawns + polls; re-invoked with {startFresh} on [2]
  });
}
