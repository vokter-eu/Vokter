// Plain-node unit test for the stdout line pipeline. Run: `node progress_pipe.test.js`.
// Covers the chunk-boundary cases that break a naive per-chunk parser.
'use strict';

const assert = require('assert');
const { LineBuffer, parseProgressLine, PROGRESS_PREFIX } = require('./progress_pipe');

const P = (obj) => PROGRESS_PREFIX + JSON.stringify(obj) + '\n';

function feed(chunks) {
  const buf = new LineBuffer();
  const events = [];
  const rawLines = [];
  for (const c of chunks) {
    for (const line of buf.push(Buffer.from(c))) {
      rawLines.push(line);
      const ev = parseProgressLine(line);
      if (ev) events.push(ev);
    }
  }
  return { events, rawLines };
}

// 1. A whole line in one chunk.
{
  const { events } = feed([P({ percent: 10 })]);
  assert.deepStrictEqual(events, [{ percent: 10 }]);
}

// 2. THE case: one JSON line split across two chunks — nothing emitted until
//    the newline arrives, then it parses cleanly.
{
  const full = P({ index: 1, count: 2, percent: 42.5 });
  const cut = Math.floor(full.length / 2);
  const { events } = feed([full.slice(0, cut), full.slice(cut)]);
  assert.deepStrictEqual(events, [{ index: 1, count: 2, percent: 42.5 }]);
}

// 3. Several lines bundled in one chunk → all emitted, in order.
{
  const { events } = feed([P({ percent: 1 }) + P({ percent: 2 }) + P({ percent: 3 })]);
  assert.deepStrictEqual(events.map((e) => e.percent), [1, 2, 3]);
}

// 4. Human `[orchestrator] …` logs interleaved with progress → logs ignored,
//    progress extracted, trailing partial held for the next chunk.
{
  const chunk = '[orchestrator] starting native Ollama\n' + P({ percent: 5 }) + '[progress] {"per';
  const { events, rawLines } = feed([chunk]);
  assert.deepStrictEqual(events, [{ percent: 5 }]);
  // the "[orchestrator] …" line surfaced as a raw line but parsed to null
  assert.ok(rawLines.some((l) => l.startsWith('[orchestrator]')));
  // the dangling "[progress] {\"per" is NOT emitted yet
  assert.strictEqual(events.length, 1);
}

// 5. Malformed JSON after the prefix → null, no throw, stream keeps going.
{
  const { events } = feed(['[progress] {not json}\n' + P({ percent: 7 })]);
  assert.deepStrictEqual(events, [{ percent: 7 }]);
}

// 6. CRLF line endings tolerated.
{
  const { events } = feed([PROGRESS_PREFIX + JSON.stringify({ percent: 8 }) + '\r\n']);
  assert.deepStrictEqual(events, [{ percent: 8 }]);
}

// 7. Byte-by-byte dribble (worst-case fragmentation) still reconstructs the line.
{
  const full = P({ phase: 'downloading', percent: 99.9 });
  const { events } = feed([...full].map((ch) => ch));
  assert.deepStrictEqual(events, [{ phase: 'downloading', percent: 99.9 }]);
}

console.log('ALL GREEN — 7 line-pipeline cases');
