// Phase 3.3-D (step 2b): turn the orchestrator child's stdout — a byte STREAM,
// not a line stream — into whole `[progress] {json}` events for the loading UI.
//
// The subtlety the whole feature hinges on: child.stdout 'data' events are
// arbitrary byte chunks. Under the high-frequency progress stream a single JSON
// line can be split across two chunks, or several lines can arrive in one chunk.
// Parsing a raw chunk as a line gives intermittent JSON.parse failures — a ghost
// bug that shows up only under load. So we buffer and split on '\n', parsing
// only COMPLETE lines and holding the remainder for the next chunk.
//
// Pure (no electron, no I/O) → unit-tested with plain node (progress_pipe.test.js).

'use strict';

// Must match orchestrator.py's PROGRESS_PREFIX exactly.
const PROGRESS_PREFIX = '[progress] ';

// Accumulates byte chunks; hands back only the complete lines seen so far.
class LineBuffer {
  constructor() {
    this._rem = ''; // bytes after the last '\n' — an incomplete line, held over
  }

  // push(chunk: Buffer|string) -> string[] of complete lines (newline stripped).
  push(chunk) {
    this._rem += chunk.toString('utf8');
    const parts = this._rem.split('\n');
    this._rem = parts.pop(); // last piece has no trailing '\n' yet → keep it
    return parts;
  }
}

// A complete line -> the parsed progress object, or null if it isn't one of ours
// (a human `[orchestrator] …` log) or the JSON is malformed. Never throws.
function parseProgressLine(line) {
  line = line.replace(/\r$/, ''); // tolerate CRLF, just in case
  if (!line.startsWith(PROGRESS_PREFIX)) return null;
  try {
    return JSON.parse(line.slice(PROGRESS_PREFIX.length));
  } catch {
    return null;
  }
}

module.exports = { LineBuffer, parseProgressLine, PROGRESS_PREFIX };
