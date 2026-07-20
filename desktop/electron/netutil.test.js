// Plain-node test for the per-instance free-port picker. Run: `node netutil.test.js`.
'use strict';

const assert = require('assert');
const net = require('net');
const { pickFreePort } = require('./netutil');

function bindable(port) {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once('error', reject);
    s.listen(port, '127.0.0.1', () => s.close(() => resolve(true)));
  });
}

(async () => {
  const p = await pickFreePort();
  assert.ok(Number.isInteger(p) && p > 1024 && p < 65536, `valid port, got ${p}`);

  // It was released, so we can actually bind it (this is what the child does).
  assert.strictEqual(await bindable(p), true, 'picked port must be bindable');

  // A second pick also yields a usable port (not stuck on one value).
  const q = await pickFreePort();
  assert.ok(Number.isInteger(q) && q > 1024, `second pick valid, got ${q}`);

  console.log(`ALL GREEN — free-port picker (${p}, ${q})`);
})().catch((e) => { console.error('FAILED:', e); process.exit(1); });
