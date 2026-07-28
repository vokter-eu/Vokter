// Phase 3.3-D (step 2b): the ONE-WAY, receive-only bridge to the loading screen.
//
// Minimum privilege, same discipline as the sandbox work: the renderer gets a
// single ability — LISTEN for download-progress events — and nothing else. No
// ipcRenderer.send/invoke is exposed, so the page cannot call back into the main
// process or reach Node. contextIsolation stays ON; this runs in the isolated
// world and only hands the page a plain callback registrar via contextBridge.

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vokter', {
  // onDownloadProgress(cb): cb(event) is called with each {label, model, phase,
  // completed, total, percent, indeterminate, error}. Returns an unsubscribe fn.
  onDownloadProgress: (cb) => {
    const listener = (_event, data) => cb(data);
    ipcRenderer.on('download-progress', listener);
    return () => ipcRenderer.removeListener('download-progress', listener);
  },

  // startFresh(): the ONE thing the window may send TO the system. A single
  // fixed verb, NO arguments, NO channel choice — the page cannot ask for
  // anything else. Only honoured by main when halted at the guardrail, and only
  // once (main gates against a double click). See main.js.
  startFresh: () => ipcRenderer.send('vokter:start-fresh'),

  // ask(body): proxy /api/ask through main so the human-session token never lives in
  // page JS (main attaches it — see main.js 'vokter:ask'). Resolves to {status, body}.
  // The page passes only {question, conversation_id}; it never sees or holds the token.
  ask: (body) => ipcRenderer.invoke('vokter:ask', body),
});
