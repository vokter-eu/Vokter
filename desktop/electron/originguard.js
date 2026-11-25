// Origin predicates for the navigation fence + IPC sender check (ALTO-3). Pure and dependency-free so
// the security logic is unit-testable without spinning up Electron. `getPort` returns the current
// backend port (the app is served from http://127.0.0.1:<port>/).
'use strict';

function makeGuards(getPort) {
  const ownOrigin = () => {
    const p = getPort();
    return (p != null) ? `http://127.0.0.1:${p}` : null;
  };

  // A URL that belongs to our own loopback app origin (exact origin, or origin + path/query/hash).
  const isOwn = (url) => {
    const o = ownOrigin();
    if (!o || typeof url !== 'string') return false;
    return url === o || url.startsWith(o + '/') || url.startsWith(o + '?') || url.startsWith(o + '#');
  };

  // May this frame reach the IPC bridges (which attach the human-session token)? Our own app origin,
  // plus the main-process-loaded screens (loading.html = file://, the guardrail/error pages = data:,
  // and the initial about:blank). Everything else — any remote http(s) origin or exotic scheme — is
  // rejected. The navigation fence already makes it impossible to point the main webContents at a
  // foreign origin; this is the defence-in-depth behind it, so a stray frame still can't use window.vokter.
  const senderIsApp = (url) => {
    if (!url || typeof url !== 'string') return false;
    if (isOwn(url)) return true;
    if (url.startsWith('file://') || url.startsWith('data:') || url === 'about:blank') return true;
    return false;
  };

  return { ownOrigin, isOwn, senderIsApp };
}

module.exports = { makeGuards };
