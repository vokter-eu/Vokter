// Unit test for the navigation-fence / IPC-sender origin predicates (ALTO-3). Pure logic, no Electron.
//   node tests/originguard_test.js
const { makeGuards } = require('../desktop/electron/originguard');

let fails = 0;
const check = (d, c) => { console.log((c ? 'PASS ' : 'FAIL ') + d); if (!c) fails++; };

const G = makeGuards(() => 8097);                       // backend on port 8097

// isOwn — only our exact loopback origin (defeats look-alike / rebinding-style hosts)
check('own origin exact → own', G.isOwn('http://127.0.0.1:8097'));
check('own origin + path → own', G.isOwn('http://127.0.0.1:8097/static/app.js'));
check('own origin + query/hash → own', G.isOwn('http://127.0.0.1:8097/?x#y'));
check('external http → NOT own', !G.isOwn('http://evil.com'));
check('https external → NOT own', !G.isOwn('https://evil.com/'));
check('different port → NOT own', !G.isOwn('http://127.0.0.1:9999/'));
check('suffix look-alike (…:8097.evil.com) → NOT own', !G.isOwn('http://127.0.0.1:8097.evil.com/'));
check('userinfo trick (…:8097@evil.com) → NOT own', !G.isOwn('http://127.0.0.1:8097@evil.com/'));

// senderIsApp — our app origin + our own main-loaded screens; everything remote rejected
check('senderIsApp: own origin → allowed', G.senderIsApp('http://127.0.0.1:8097/'));
check('senderIsApp: loading.html (file://) → allowed', G.senderIsApp('file:///opt/Vokter/resources/desktop/electron/loading.html'));
check('senderIsApp: guardrail (data:) → allowed', G.senderIsApp('data:text/html;charset=utf-8,%3Chtml%3E'));
check('senderIsApp: about:blank → allowed', G.senderIsApp('about:blank'));
check('senderIsApp: external http → REJECTED', !G.senderIsApp('http://evil.com/'));
check('senderIsApp: external https → REJECTED', !G.senderIsApp('https://evil.example/pay'));
check('senderIsApp: empty/undefined → REJECTED', !G.senderIsApp('') && !G.senderIsApp(undefined));

// before the backend port is known, nothing is "own" (fail-closed)
const G0 = makeGuards(() => null);
check('no backend port yet → own origin unknown (fail-closed)', !G0.isOwn('http://127.0.0.1:8097/'));

console.log('');
if (fails) { console.log('❌ originguard: ' + fails + ' FAILED'); process.exit(1); }
console.log('✅ originguard: fence/sender origin predicates hold');
