// Plain-node test for the recovery-screen copy. Run: `node guardrail_screen.test.js`.
'use strict';

const assert = require('assert');
const { guardrailHtml } = require('./guardrail_screen');

const CASES = [
  { keychain: 'unreachable', has_candidates: false },
  { keychain: 'unreachable', has_candidates: true },
  { keychain: 'has_key', has_candidates: false },
  { keychain: 'has_key', has_candidates: true },
  { keychain: 'no_key', has_candidates: true },
];

// Universal honesty: no case may promise a recovery the app can't do, and every
// case must reassure + offer the one clickable action.
for (const ev of CASES) {
  const html = guardrailHtml(ev);
  assert.ok(!/recover it first/i.test(html), `dead-end "recover it first" leaked: ${JSON.stringify(ev)}`);
  assert.ok(!/recover it/i.test(html), `must not promise recovery: ${JSON.stringify(ev)}`);
  assert.ok(html.includes('Nothing has been deleted'), 'reassurance present');
  assert.ok(html.includes('Start fresh'), 'the clickable action present');
}

// unreachable → the REAL fix (unlock + reopen) is spelled out.
{
  const html = guardrailHtml({ keychain: 'unreachable', has_candidates: false });
  assert.ok(/unlock it/i.test(html) && /reopen Vokter/i.test(html),
    'locked-keychain case must guide the user to unlock + reopen');
}

// has_key without candidates → keychain is fine, so NO unlock line, NO candidate line.
{
  const html = guardrailHtml({ keychain: 'has_key', has_candidates: false });
  assert.ok(!/unlock/i.test(html), 'no unlock guidance when the keychain is fine');
  assert.ok(!/earlier data on this computer/i.test(html), 'no found-data line without candidates');
}

// candidates → honest "it's safe, fresh won't delete it", not a recover promise.
{
  const html = guardrailHtml({ keychain: 'has_key', has_candidates: true });
  assert.ok(/earlier data on this computer/i.test(html) && /safe too/i.test(html),
    'found-data reassurance present and honest');
}

console.log('ALL GREEN — guardrail recovery-screen copy (5 cases coherent, no false promises)');
