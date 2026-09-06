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

  // askStream(body): the streaming twin of ask(). main opens /api/ask with stream=true
  // (attaching the human-session token — the page still never holds it) and pushes tokens
  // over the 'vokter:ask-token' channel; this promise resolves with the final
  // {status, body:{answer, sources, conversation_id, memory_withheld}} — same shape as ask.
  askStream: (body) => ipcRenderer.invoke('vokter:ask-stream', body),

  // askAbort(): interrupt the in-flight askStream (the Stop button). main destroys the request;
  // the streamed tokens already shown stay on screen, the backend discards the partial turn.
  askAbort: () => ipcRenderer.send('vokter:ask-abort'),

  // onAskToken(cb): cb({text}) for each streamed delta. Receive-only, like
  // onDownloadProgress. Returns an unsubscribe fn — the caller MUST call it when the
  // stream ends so listeners don't pile up across turns.
  onAskToken: (cb) => {
    const listener = (_event, data) => cb(data);
    ipcRenderer.on('vokter:ask-token', listener);
    return () => ipcRenderer.removeListener('vokter:ask-token', listener);
  },

  // memorySuggest(body): proxy /api/memory/suggest through main so the human-session token
  // never lives in page JS (main attaches it — see main.js 'vokter:memory-suggest'). The
  // backend gates this human-only read on that token. Resolves to {status, body}. The page
  // passes only {message, conversation_id}; it never holds the token.
  memorySuggest: (body) => ipcRenderer.invoke('vokter:memory-suggest', body),

  // memory(req): proxy the /api/memory CRUD (list/add/edit/pin/unpin/delete/forgetAll) through
  // main so the human-session token never lives in page JS — same discipline as vokter:ask. main
  // WHITELISTS req.op → the real path (the page can't choose an arbitrary path). Resolves to
  // {status, body}. Used only by the Memory settings view.
  memory: (req) => ipcRenderer.invoke('vokter:memory', req),
});
