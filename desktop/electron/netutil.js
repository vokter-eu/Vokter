// Phase 3.3 (zombie fix, #2): pick a free loopback port PER INSTANCE so main.js
// polls/loads only ITS OWN backend — an orphaned backend squatting on the old
// port is simply on a different port, hence invisible. Chosen once at startup and
// reused across the start-fresh respawn. Pure node (no electron) → unit-testable.
'use strict';

const net = require('net');

// Ask the OS for a free port: bind :0 on loopback, read the assigned port,
// release it, hand it back. There is a tiny TOCTOU before the child binds it; if
// lost, the child fails to bind → the boot errors → we show the error screen
// (safe, not silent). Not engineered around on purpose.
function pickFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

module.exports = { pickFreePort };
