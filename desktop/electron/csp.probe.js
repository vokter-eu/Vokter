// Headless causal probe for the renderer Content-Security-Policy — CSP lote (§8.6).
//
// This does NOT hand-copy the policy: it `require('./csp')` and installs the SHIPPED
// installCsp on the window's session, so it exercises the exact same header-injection
// the app ships. If csp.js changes, this probe tests the change.
//
// It proves the renderer ENFORCES the policy (not just that we emit it):
//   - a SAME-ORIGIN external <script src="/probe.js"> runs      → script-src 'self' allows it
//   - an INLINE <script> is BLOCKED and fires securitypolicyviolation in the script-src family
//
// Run (real display, no DevTools):  electron csp.probe.js
'use strict';
const { app, BrowserWindow, session } = require('electron');
const http = require('http');
const { installCsp, CSP } = require('./csp');   // <-- the shipped module, NOT a copy

const PROBE_JS =
  'window.__externalRan = true;' +
  'window.__violations = [];' +
  'document.addEventListener("securitypolicyviolation", function (e) {' +
  '  var d = e.effectiveDirective || e.violatedDirective || "";' +
  '  window.__violations.push(d);' +
  '  console.log("CSP_VIOLATION dir=" + d + " blocked=" + e.blockedURI);' +
  '});' +
  'window.addEventListener("load", function () {' +
  '  setTimeout(function () {' +
  '    console.log("PROBE_SUMMARY externalRan=" + (window.__externalRan === true) +' +
  '      " inlineRan=" + (window.__inlineRan === true) +' +
  '      " violations=" + JSON.stringify(window.__violations));' +
  '  }, 250);' +
  '});';

const HTML =
  '<!doctype html><html><head><meta charset="utf-8"></head><body>' +
  '<script src="/probe.js"></script>' +            // external, same-origin  → ALLOWED by script-src 'self'
  '<script>window.__inlineRan = true;</script>' +  // inline                 → MUST be blocked
  '</body></html>';

const server = http.createServer((req, res) => {
  if (req.url === '/probe.js') {
    res.setHeader('Content-Type', 'application/javascript');
    res.end(PROBE_JS);
  } else {
    res.setHeader('Content-Type', 'text/html');
    res.end(HTML);
  }
});

let finished = false;
function done(green, lines) {
  if (finished) return;
  finished = true;
  console.log('----');
  console.log('PROBE using CSP from ./csp.js: ' + CSP);
  for (const l of lines) console.log(l);
  console.log(green ? 'PROBE-RESULT: GREEN' : 'PROBE-RESULT: RED');
  app.exit(green ? 0 : 1);
}

// Robust across Electron console-message signatures:
//   old: (event, level, message, line, sourceId)   new (>=36): (event) with event.message
function consoleText(args) {
  if (args.length >= 3 && typeof args[2] === 'string') return args[2];
  if (args[0] && typeof args[0].message === 'string') return args[0].message;
  return '';
}

app.disableHardwareAcceleration();
app.whenReady().then(() => {
  installCsp(session.defaultSession);   // the REAL shipped installer, on the window's session

  const seen = { summary: null, violations: [] };
  server.listen(0, '127.0.0.1', () => {
    const port = server.address().port;
    const win = new BrowserWindow({ show: false, webPreferences: { contextIsolation: true, nodeIntegration: false } });

    win.webContents.on('console-message', (...args) => {
      const msg = consoleText(args);
      if (msg.startsWith('CSP_VIOLATION')) seen.violations.push(msg);
      if (msg.startsWith('PROBE_SUMMARY')) seen.summary = msg;
    });

    win.webContents.on('did-finish-load', () => {
      setTimeout(() => {
        const inlineRan = /inlineRan=true/.test(seen.summary || '');
        const externalRan = /externalRan=true/.test(seen.summary || '');
        const scriptViol = seen.violations.find((m) => /dir=script-src/.test(m));  // matches script-src and script-src-elem
        const lines = [
          "external same-origin script ran (script-src 'self'): " + externalRan,
          'inline script executed (must be false):            ' + inlineRan,
          'securitypolicyviolation events:                    ' + JSON.stringify(seen.violations),
          'renderer summary line:                             ' + (seen.summary || '(none)'),
        ];
        done(externalRan && !inlineRan && !!scriptViol, lines);
      }, 600);
    });

    win.loadURL('http://127.0.0.1:' + port + '/');
  });
});

setTimeout(() => done(false, ['TIMEOUT — no result within 15s']), 15000);
