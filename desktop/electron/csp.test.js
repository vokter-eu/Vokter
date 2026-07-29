// Plain-node test for the renderer CSP handler. Run: `node csp.test.js`.
// Proves the SHELL emits the exact agreed policy on every response and that our CSP
// is authoritative (a pre-existing one is stripped). This is the "afirmar⇒testear"
// rule from docs/SECURITY_REVIEW.md applied to the CSP delivery mechanism.
'use strict';

const assert = require('assert');
const { CSP, installCsp } = require('./csp');

// Capture the handler installCsp registers on a fake Electron session.
let handler = null;
const fakeSession = { webRequest: { onHeadersReceived: (fn) => { handler = fn; } } };
installCsp(fakeSession);
assert.ok(typeof handler === 'function', 'installCsp must register an onHeadersReceived handler');

// Case 1 — a response with no CSP gets ours added; other headers survive.
let out1 = null;
handler({ responseHeaders: { 'Content-Type': ['text/html'] } }, (r) => { out1 = r.responseHeaders; });
assert.deepStrictEqual(out1['Content-Security-Policy'], [CSP], 'emits the exact CSP (as a header-value array)');
assert.deepStrictEqual(out1['Content-Type'], ['text/html'], 'preserves unrelated headers');

// Case 2 — a pre-existing CSP (any key casing) is stripped; exactly one remains, ours.
let out2 = null;
handler({ responseHeaders: { 'content-security-policy': ['default-src *'], 'X-Test': ['y'] } },
        (r) => { out2 = r.responseHeaders; });
const cspKeys = Object.keys(out2).filter((k) => k.toLowerCase() === 'content-security-policy');
assert.strictEqual(cspKeys.length, 1, 'exactly one CSP header after strip (no duplicate/weaker one)');
assert.deepStrictEqual(out2[cspKeys[0]], [CSP], 'our CSP replaces the pre-existing one');
assert.deepStrictEqual(out2['X-Test'], ['y'], 'preserves unrelated headers on strip path');

// Case 3 — the agreed policy shape (the invariants the lote promises).
for (const d of [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "media-src 'self' blob:",   // TTS audio (blob:) — must be allowed or voice breaks
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "frame-ancestors 'none'",
]) {
  assert.ok(CSP.includes(d), `CSP must contain directive: ${d}`);
}
assert.ok(!CSP.includes("'unsafe-eval'"), "CSP must NOT allow 'unsafe-eval'");
assert.ok(!/script-src[^;]*'unsafe-inline'/.test(CSP), "script-src must NOT allow 'unsafe-inline'");

console.log('ALL GREEN — CSP handler emits the exact policy, strips a pre-existing one, '
          + "and script-src carries no unsafe-inline/unsafe-eval.");
