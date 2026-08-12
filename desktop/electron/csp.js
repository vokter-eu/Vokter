// Content-Security-Policy for the Vokter renderer — CSP lote (threat-model §8.6).
//
// Delivered by the SHELL via session.webRequest.onHeadersReceived — NOT from the
// HTML <meta> and NOT from FastAPI — so it is imposed on every http(s) response the
// renderer receives and cannot be weakened by anything the page does.
//
// Scope: onHeadersReceived only fires for http(s). That covers the main UI
// (loadURL http://127.0.0.1:PORT/), which is where third-party content (documents,
// memos, browsed pages) is rendered = the real attack surface. loading.html (file://)
// and the guardrail/error screens (data:) are our own first-party HTML with no injected
// content and are deliberately NOT covered (webRequest does not fire for file:/data:).
//
// Policy notes:
//   script-src 'self'      — no 'unsafe-inline', no 'unsafe-eval'. This is WHY the two
//                            inline <script> blocks were extracted to /static/app.js +
//                            /static/app.memory.js.
//   style-src …'unsafe-inline' — the 43 inline style="" attributes stay (low risk, agreed).
//   media-src 'self' blob: — TTS plays synthesised audio via a blob: URL
//                            (speakText: URL.createObjectURL → new Audio). Without blob:
//                            the CSP would silently kill voice. Avatar images are
//                            same-origin (/api/config/avatar?t=…), covered by img-src 'self'.
'use strict';

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "media-src 'self' blob:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
].join('; ');

// Install the CSP on an Electron session: add our header to every response, stripping
// any pre-existing CSP (case-insensitive) so ours is authoritative — a backend could
// in principle set its own, and the shell's policy must win.
function installCsp(sess) {
  sess.webRequest.onHeadersReceived((details, callback) => {
    const responseHeaders = { ...details.responseHeaders };
    for (const k of Object.keys(responseHeaders)) {
      if (k.toLowerCase() === 'content-security-policy') delete responseHeaders[k];
    }
    responseHeaders['Content-Security-Policy'] = [CSP];
    callback({ responseHeaders });
  });
}

module.exports = { CSP, installCsp };
