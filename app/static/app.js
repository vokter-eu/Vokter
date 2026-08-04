const chat = document.getElementById('chat');
const docsEl = document.getElementById('docs');
let conversationId = null;

function add(role, text, sources){
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  if (role === 'bot') {
    const play = document.createElement('button');
    play.className = 'btn-play';
    play.title = 'Read aloud';
    play.textContent = '▶';
    play.onclick = () => speakText(text, play);
    d.appendChild(play);
  }
  if (sources && sources.length){
    const s = document.createElement('div');
    s.className = 'src';
    s.textContent = 'Sources: ' + sources.join(', ');
    d.appendChild(s);
  }
  chat.appendChild(d);
  chat.scrollTop = chat.scrollHeight;
  return d;
}

// Phase 2c — after a reply, ask the backend if the user's message revealed a
// durable fact, and OFFER to remember it. Nothing is stored until the user
// clicks Remember. Dismissed facts are kept in-session only (dedupe b) so we
// don't re-offer the same thing this session — deliberately NOT persisted.
const _dismissedFacts = new Set();

async function maybeSuggestMemory(question, convId) {
  let suggestions;
  try {
    const {status, body} = await suggestMemoryBackend({message: question, conversation_id: convId});
    if (status < 200 || status >= 300 || !body) return;
    suggestions = body.suggestions || [];
  } catch { return; }
  // At most ONE chip per turn: a single short message often splits into two facts
  // ("nurse" + "at Ibiza hospital") — showing both reads as nagging. Offer the
  // first; the rest stay unmade. (If genuinely-distinct facts get lost too often,
  // raise this cap — it's a UI throttle, the extractor is untouched.)
  for (const fact of suggestions) {
    if (_dismissedFacts.has(fact.toLowerCase())) continue;
    renderMemoryChip(fact);
    break;
  }
}

async function saveLearned(content) {
  try {
    await fetch('/api/memory', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content, source: 'learned', confidence: 0.7})
    });
    if (typeof loadMemory === 'function') loadMemory();   // refresh the review window
  } catch {}
}

function renderMemoryChip(fact) {
  const chip = document.createElement('div');
  chip.className = 'mem-chip';
  const q = document.createElement('div');
  q.className = 'mem-chip-q';
  q.textContent = '💡 Want me to remember this?';
  const f = document.createElement('div');
  f.className = 'mem-chip-fact';
  f.textContent = '«' + fact + '»';
  const actions = document.createElement('div');
  actions.className = 'mem-chip-actions';
  const save = document.createElement('button'); save.className = 'btn-sm'; save.textContent = 'Remember';
  const edit = document.createElement('button'); edit.className = 'btn-sm'; edit.textContent = 'Edit';
  const no   = document.createElement('button'); no.className = 'btn-sm'; no.textContent = 'No';
  actions.append(save, edit, no);
  chip.append(q, f, actions);

  const done = () => { chip.textContent = '✓ Remembered'; chip.classList.add('done'); };
  save.onclick = async () => { await saveLearned(fact); done(); };
  edit.onclick = async () => {
    const v = prompt('Edit what Vokter remembers:', fact);
    if (v && v.trim()) { await saveLearned(v.trim()); done(); }
  };
  no.onclick = () => { _dismissedFacts.add(fact.toLowerCase()); chip.remove(); };

  chat.appendChild(chip);
  chat.scrollTop = chat.scrollHeight;
}

function _resetSpeakBtn(btn) {
  if (btn) { btn.disabled = false; btn.textContent = '▶'; btn.title = ''; }
}

async function speakText(text, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const r = await fetch('/api/voice/speak', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text})
    });
    if (!r.ok) {
      // C′ robustness: the voice for the chosen language isn't downloaded yet. Degrade
      // cleanly — chat keeps working — and arm ▶ as a real "download & retry" (stage 3
      // trigger 2): the click DOWNLOADS the voice (ensure_voice) and speaks when ready.
      if (r.status === 503) {
        let notReady = false;
        try { notReady = (await r.json()).error === 'voice_not_ready'; } catch {}
        if (notReady && btn) {
          _armVoiceRetry(btn, text);
          return;
        }
      }
      _resetSpeakBtn(btn);
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => { URL.revokeObjectURL(url); _resetSpeakBtn(btn); };
    audio.play();
  } catch {
    _resetSpeakBtn(btn);
  }
}

// ── Stage 3: voice availability (the three triggers' UI side) ────────────────────
// ONE UI primitive, `ensureVoice`, over the ONE backend primitive (/api/voice/ensure,
// idempotent + in-flight-coalesced). Non-blocking by construction: /ensure returns at
// once and the download runs in a backend thread; we only ever POLL /api/voice/state.
// Surfaced two ways that read the SAME state: the header pill (triggers 1 + 3) and the
// per-message ▶/⚠ button (trigger 2). Failure degrades to ⚠, never throws, never blocks.

const _voicePill = () => document.getElementById('voice-status');
let _voicePollTimer = null;

function _renderVoicePill(st) {
  const pill = _voicePill();
  if (!pill) return;
  if (st && st.status === 'downloading') {
    const pct = st.total ? Math.floor(100 * st.downloaded / st.total) : 0;
    pill.className = 'voice-pill downloading';
    pill.textContent = '⬇ Downloading voice… ' + pct + '%';
    pill.style.display = '';
    pill.onclick = null;
  } else if (st && st.status === 'error') {
    pill.className = 'voice-pill error';
    pill.textContent = '⚠ Voice unavailable — retry';
    pill.style.display = '';
    pill.onclick = () => ensureVoice();      // re-triggerable: a click re-downloads
  } else {                                    // ready | absent → nothing to show
    pill.style.display = 'none';
    pill.onclick = null;
  }
}

// Poll /state until it settles (not "downloading"), then stop — one loop at a time.
async function _pollVoiceState() {
  let st = null;
  try { st = await (await fetch('/api/voice/state')).json(); } catch { st = null; }
  _renderVoicePill(st);
  if (st && st.status === 'downloading') {
    _voicePollTimer = setTimeout(_pollVoiceState, 700);
  } else {
    _voicePollTimer = null;
  }
}

function _watchVoice() {              // (re)start the single poll loop if idle
  if (_voicePollTimer) return;
  _pollVoiceState();
}

// The one UI-side primitive. Kick the (idempotent) backend download, then watch state.
// Never throws, never blocks — callers fire and forget.
async function ensureVoice() {
  try { await fetch('/api/voice/ensure', {method: 'POST'}); } catch {}
  _watchVoice();
}

// Wait for the current voice to settle after an ensure. Resolves true on ready, false on
// error/absent/timeout. Capped so a permanently-failing download can't spin forever.
async function _awaitVoiceReady(maxMs = 180000) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    let st;
    try { st = await (await fetch('/api/voice/state')).json(); } catch { return false; }
    if (st.status === 'ready') return true;
    if (st.status === 'error' || st.status === 'absent') return false;
    await new Promise(r => setTimeout(r, 700));       // downloading → keep waiting
  }
  return false;
}

// Trigger 2: arm a not-ready ▶ as ⚠. Clicking it DOWNLOADS the voice (real fetch, not a
// bare speak-retry) and speaks when ready; if the download fails, it falls back to ⚠ so
// the user can try again — a failure never leaves the button dead.
function _armVoiceRetry(btn, text) {
  btn.disabled = false;
  btn.textContent = '⚠';
  btn.title = 'Voice not available yet — click to download & retry';
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = '…'; btn.title = 'Downloading voice…';
    await ensureVoice();                          // coalesces with any in-flight download
    if (await _awaitVoiceReady()) {
      btn.onclick = () => speakText(text, btn);   // restore normal play, then speak now
      speakText(text, btn);
    } else {
      _armVoiceRetry(btn, text);                  // failed → back to ⚠, re-triggerable
    }
  };
}

let _mediaRecorder = null;
let _audioChunks = [];

document.getElementById('btn-mic').onclick = async () => {
  const btn = document.getElementById('btn-mic');
  if (_mediaRecorder && _mediaRecorder.state === 'recording') {
    _mediaRecorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    _mediaRecorder = new MediaRecorder(stream);
    _audioChunks = [];
    _mediaRecorder.ondataavailable = e => _audioChunks.push(e.data);
    _mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      btn.classList.remove('recording');
      btn.textContent = '🎤';
      btn.disabled = true;
      const blob = new Blob(_audioChunks, {type: 'audio/webm'});
      const fd = new FormData();
      fd.append('audio', blob, 'recording.webm');
      try {
        const r = await fetch('/api/voice/transcribe', {method: 'POST', body: fd});
        const j = await r.json();
        if (r.ok && j.text) document.getElementById('q').value = j.text;
      } catch {}
      btn.disabled = false;
    };
    _mediaRecorder.start();
    btn.classList.add('recording');
    btn.textContent = '⏹';
  } catch {
    alert('Could not access microphone. Check browser permissions.');
  }
};

async function loadDocs(){
  const r = await fetch('/api/docs');
  const docs = await r.json();
  docsEl.innerHTML = docs.length ? '' :
    '<div class="tag">Vokter knows nothing yet. That\'s its most private state 😉</div>';
  for (const d of docs){
    const row = document.createElement('div');
    row.className = 'doc';
    // ${d.doc} is attacker-influenceable (a peer's browse URL `web::{url}`, an upload
    // filename) → escape it: without this it is an innerHTML XSS that fires in the
    // human session when the docs panel opens (threat-model §8.1). ${d.chunks} is a count.
    row.innerHTML = `<span>📄 ${_esc(d.doc)} <span class="tag">(${d.chunks} chunks)</span></span>`;
    const del = document.createElement('button');
    del.textContent = '✕';
    del.title = 'Real deletion: document and embeddings';
    del.onclick = async () => {
      await fetch('/api/docs/' + encodeURIComponent(d.doc), {method:'DELETE'});
      loadDocs();
    };
    row.appendChild(del);
    docsEl.appendChild(row);
  }
}

document.getElementById('file').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const note = add('bot', `Reading and memorizing "${f.name}" locally…`);
  const fd = new FormData();
  fd.append('file', f);
  const r = await fetch('/api/docs', {method:'POST', body:fd});
  const j = await r.json();
  note.textContent = r.ok
    ? `Done. I've memorized "${j.doc}" in ${j.chunks} chunks, all on your disk.`
    : `I couldn't read that file: ${j.detail || 'error'}`;
  loadDocs();
  e.target.value = '';
};

// Route /api/ask through the Electron shell (window.vokter.ask) so the human-session
// token stays in the main process, never in this page's JS. Fall back to a plain fetch
// when opened directly in a browser (dev) — that path carries no token, so the backend
// withholds memory (strict deny-by-default) and flags memory_withheld. Returns {status, body}.
async function askBackend(payload) {
  if (window.vokter && window.vokter.ask) {
    return await window.vokter.ask(payload);
  }
  const r = await fetch('/api/ask', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  let body = null;
  try { body = await r.json(); } catch {}
  return { status: r.status, body };
}

// Route /api/wallet/send through the Electron shell (window.vokter.walletSend) so the
// human-session token stays in the main process, never in this page's JS — same discipline
// as askBackend. The backend gates wallet_send on that token (deny-by-default): a plain
// browser (dev) carries no token, so the payment is refused (403) rather than sent
// unauthorised. Returns {status, body}.
async function sendPayment(payload) {
  if (window.vokter && window.vokter.walletSend) {
    return await window.vokter.walletSend(payload);
  }
  const r = await fetch('/api/wallet/send', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  let body = null;
  try { body = await r.json(); } catch {}
  return { status: r.status, body };
}

// Route /api/memory/suggest through the Electron shell (window.vokter.memorySuggest) so the
// human-session token stays in main — same discipline as askBackend. The backend gates this
// human-only read on that token (C2a): a plain browser (dev) carries no token, so it returns
// no suggestions rather than reading the thread. Returns {status, body}.
async function suggestMemoryBackend(payload) {
  if (window.vokter && window.vokter.memorySuggest) {
    return await window.vokter.memorySuggest(payload);
  }
  const r = await fetch('/api/memory/suggest', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  let body = null;
  try { body = await r.json(); } catch {}
  return { status: r.status, body };
}

// Fail-closed VISIBLE: when the backend withheld personal memory from this session,
// say so plainly rather than letting Vokter act as if it doesn't know you.
function noteMemoryWithheld(botEl) {
  const n = document.createElement('div');
  n.className = 'src';
  n.textContent = '⚠ Memory not available in this session.';
  botEl.appendChild(n);
}

document.getElementById('ask').onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById('q');
  const q = input.value.trim();
  if (!q) return;
  add('user', q);
  input.value = '';
  const thinking = add('bot', 'Thinking locally…');
  try {
    const res = await askBackend({question: q, conversation_id: conversationId});
    if (!res.body) {
      thinking.textContent = `Server error ${res.status || ''} — unexpected response format.`;
      return;
    }
    thinking.remove();
    if (res.status >= 200 && res.status < 300) {
      const j = res.body;
      conversationId = j.conversation_id;
      const botEl = add('bot', j.answer, j.sources);
      if (j.memory_withheld) noteMemoryWithheld(botEl);
      maybeSuggestMemory(q, j.conversation_id);   // Phase 2c — propose, never store
    } else {
      add('bot', (res.body && res.body.detail) || 'Error');
    }
  } catch (err){
    thinking.textContent = 'Can\'t reach Vokter. Is it still running?';
  }
};

document.getElementById('new-chat').onclick = () => {
  conversationId = null;
  chat.innerHTML = '';
  add('bot', 'New conversation started. Ask me anything about your documents.');
};

async function loadEmailStatus() {
  const r = await fetch('/api/email/status');
  const j = await r.json();
  const el = document.getElementById('email-status');
  const delBtn = document.getElementById('btn-del-emails');
  if (!j.configured) {
    el.textContent = 'Email not configured.';
  } else {
    el.textContent = j.synced_emails > 0
      ? `${j.synced_emails} emails in memory`
      : 'No emails synced yet';
    delBtn.style.display = j.synced_emails > 0 ? '' : 'none';
  }
}

document.getElementById('btn-sync').onclick = async () => {
  const btn = document.getElementById('btn-sync');
  const el  = document.getElementById('email-status');
  btn.disabled = true;
  el.textContent = 'Syncing… (this may take a while)';
  try {
    const r = await fetch('/api/email/sync', {method: 'POST'});
    const j = await r.json();
    if (r.ok) {
      el.textContent = `Done — ${j.synced} new emails added (${j.errors} errors)`;
      loadEmailStatus();
    } else {
      el.textContent = `Error: ${j.detail}`;
    }
  } catch {
    el.textContent = 'Could not reach server.';
  }
  btn.disabled = false;
};

document.getElementById('btn-del-emails').onclick = async () => {
  if (!confirm('Delete all synced emails from Vokter\'s memory?')) return;
  const r = await fetch('/api/email/all', {method: 'DELETE'});
  const j = await r.json();
  add('bot', `Deleted ${j.emails_removed} emails (${j.chunks_removed} chunks removed).`);
  loadEmailStatus();
};

async function loadPerms() {
  const r = await fetch('/api/browse/permissions');
  const perms = await r.json();
  document.getElementById('perms-count').textContent = perms.length;
  const list = document.getElementById('perms-list');
  list.innerHTML = perms.length
    ? ''
    : '<div class="tag" style="font-size:.82rem">No patterns allowed yet.</div>';
  for (const p of perms) {
    const row = document.createElement('div');
    row.className = 'perm-row';
    const span = document.createElement('span');
    span.textContent = p.pattern;
    row.appendChild(span);
    const del = document.createElement('button');
    del.textContent = '✕';
    del.onclick = async () => {
      await fetch('/api/browse/permissions/' + encodeURIComponent(p.pattern), {method:'DELETE'});
      loadPerms();
    };
    row.appendChild(del);
    list.appendChild(row);
  }
}

document.getElementById('btn-perms-toggle').onclick = () => {
  const el = document.getElementById('web-perms');
  el.style.display = el.style.display === 'none' ? '' : 'none';
};

document.getElementById('btn-add-perm').onclick = async () => {
  const input = document.getElementById('perm-input');
  const pattern = input.value.trim();
  if (!pattern) return;
  const r = await fetch('/api/browse/permissions', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({pattern}),
  });
  if (r.ok) { input.value = ''; loadPerms(); }
  else { const j = await r.json(); alert(j.detail); }
};

document.getElementById('btn-browse').onclick = async () => {
  const url = document.getElementById('web-url').value.trim();
  const status = document.getElementById('web-status');
  if (!url) return;
  const btn = document.getElementById('btn-browse');
  btn.disabled = true;
  status.textContent = 'Fetching and memorizing…';
  try {
    const r = await fetch('/api/browse', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url}),
    });
    const j = await r.json();
    if (r.ok) {
      status.textContent = `Done — ${j.chunks} chunks stored. Ask me about it.`;
      add('bot', `I've read and memorized ${url} (${j.chunks} chunks). Ask me anything about it.`);
      loadDocs();
    } else {
      status.textContent = `Error: ${j.detail}`;
    }
  } catch {
    status.textContent = 'Could not reach server.';
  }
  btn.disabled = false;
};

document.getElementById('btn-plan').onclick = async () => {
  const goal = document.getElementById('task-goal').value.trim();
  if (!goal) return;
  const btn = document.getElementById('btn-plan');
  const log  = document.getElementById('task-log');
  btn.disabled = true;
  log.innerHTML = '';

  const stepEls = [];

  function logLine(text, cls) {
    const d = document.createElement('div');
    d.className = 'tlog-step' + (cls ? ' ' + cls : '');
    d.textContent = text;
    log.appendChild(d);
    return d;
  }

  try {
    const r = await fetch('/api/plan', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({goal}),
    });
    if (!r.ok) { logLine('Server error: ' + r.status, 'tlog-error'); btn.disabled = false; return; }

    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }

        if (ev.type === 'status') {
          logLine('⋯ ' + ev.text);
        } else if (ev.type === 'plan') {
          ev.steps.forEach((s, i) => {
            stepEls[i] = logLine(`${i + 1}. [${s.tool}] ${s.reason}`);
          });
        } else if (ev.type === 'step_start') {
          if (stepEls[ev.index]) stepEls[ev.index].textContent += ' …';
        } else if (ev.type === 'step_done') {
          if (stepEls[ev.index]) {
            stepEls[ev.index].textContent = `✓ ${ev.text}`;
            stepEls[ev.index].className = 'tlog-step tlog-done';
          }
        } else if (ev.type === 'step_error') {
          if (stepEls[ev.index]) {
            stepEls[ev.index].textContent = `✗ ${ev.text}`;
            stepEls[ev.index].className = 'tlog-step tlog-error';
          }
        } else if (ev.type === 'error') {
          logLine('✗ ' + ev.text, 'tlog-error');
        } else if (ev.type === 'done') {
          const ans = document.createElement('div');
          ans.className = 'tlog-answer';
          ans.textContent = ev.answer;
          log.appendChild(ans);
          add('bot', ev.answer);
          loadDocs();
        }
      }
    }
  } catch (err) {
    logLine('Connection error: ' + err, 'tlog-error');
  }
  btn.disabled = false;
};

// ── Wallet ────────────────────────────────────────────────────────────────────

async function loadWalletBalance() {
  try {
    const r = await fetch('/api/wallet/balance');
    if (!r.ok) return;
    const d = await r.json();
    document.getElementById('wallet-balance').innerHTML =
      `Balance: <strong>${d.balance.toLocaleString()} ${_esc(d.unit)}</strong>`;
    document.getElementById('wallet-badge').textContent = d.adapter;
  } catch {}
}

document.getElementById('btn-wallet-refresh').onclick = loadWalletBalance;

document.getElementById('btn-wallet-receive').onclick = async () => {
  const token = document.getElementById('wallet-token').value.trim();
  if (!token) return;
  const btn = document.getElementById('btn-wallet-receive');
  btn.disabled = true;
  try {
    const r = await fetch('/api/wallet/receive', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token}),
    });
    const d = await r.json();
    if (!r.ok) { alert(d.detail || 'Receive failed'); return; }
    document.getElementById('wallet-token').value = '';
    alert(`Received ${d.received.toLocaleString()} ${d.unit}`);
    loadWalletBalance();
  } catch (e) { alert('Error: ' + e); }
  btn.disabled = false;
};

let _pendingSend = null;

document.getElementById('btn-wallet-send').onclick = () => {
  const amount = parseInt(document.getElementById('wallet-send-amount').value, 10);
  const memo   = document.getElementById('wallet-send-memo').value.trim();
  if (!amount || amount <= 0) { alert('Enter a valid amount'); return; }
  _pendingSend = {amount, memo};
  document.getElementById('wallet-modal-text').textContent =
    `Send ${amount.toLocaleString()} tokens${memo ? ' — ' + memo : ''}. This cannot be undone.`;
  document.getElementById('wallet-modal').classList.add('open');
};

document.getElementById('btn-wallet-cancel').onclick = () => {
  document.getElementById('wallet-modal').classList.remove('open');
  _pendingSend = null;
};

document.getElementById('btn-wallet-confirm').onclick = async () => {
  if (!_pendingSend) return;
  document.getElementById('wallet-modal').classList.remove('open');
  const {amount, memo} = _pendingSend;
  _pendingSend = null;
  const btn = document.getElementById('btn-wallet-send');
  btn.disabled = true;
  try {
    const {status, body: d} = await sendPayment({amount, memo, confirmed: true});
    if (status < 200 || status >= 300) {
      alert((d && d.detail) || 'Send failed'); btn.disabled = false; return;
    }
    document.getElementById('wallet-send-amount').value = '';
    document.getElementById('wallet-send-memo').value   = '';
    if (d.output) {
      const out = document.getElementById('wallet-output');
      document.getElementById('wallet-output-token').textContent = d.output;
      out.style.display = '';
    }
    loadWalletBalance();
  } catch (e) { alert('Error: ' + e); }
  btn.disabled = false;
};

function _copyText(text, btn) {
  const ok = () => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  };
  const fail = () => {
    const orig = btn.textContent;
    btn.textContent = 'Copy failed — select manually';
    setTimeout(() => { btn.textContent = orig; }, 2500);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(ok, fail);
  } else {
    // Fallback for non-HTTPS / IP access
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0;top:0;left:0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand('copy') ? ok() : fail(); }
    catch { fail(); }
    finally { document.body.removeChild(ta); }
  }
}

document.getElementById('btn-wallet-copy').onclick = () => {
  const text = document.getElementById('wallet-output-token').textContent;
  _copyText(text, document.getElementById('btn-wallet-copy'));
};

document.getElementById('btn-wallet-history-toggle').onclick = async () => {
  const el = document.getElementById('wallet-history');
  if (el.style.display !== 'none') { el.style.display = 'none'; return; }
  el.style.display = '';
  try {
    const r = await fetch('/api/wallet/history');
    const txs = await r.json();
    if (!txs.length) { el.textContent = 'No transactions yet.'; return; }
    el.innerHTML = '';
    txs.forEach(t => {
      const d = document.createElement('div');
      d.className = 'wtx';
      const sign = t.direction === 'in' ? '+' : '−';
      const cls  = t.direction === 'in' ? 'wtx-in' : 'wtx-out';
      // ${t.memo} rides inside the Cashu token minted by the counterparty (cashu.py:45)
      // → untrusted, escape it (threat-model §8.4). amount is a number; unit escaped too.
      d.innerHTML = `<span class="${cls}">${sign}${t.amount.toLocaleString()} ${_esc(t.unit)}</span>` +
                    `<span style="color:var(--muted);font-size:.75rem">${_esc(t.memo || '')}</span>` +
                    `<span style="color:var(--muted);font-size:.72rem">${new Date(t.ts*1000).toLocaleDateString()}</span>`;
      el.appendChild(d);
    });
  } catch {}
};

document.getElementById('btn-wallet-adapters-toggle').onclick = async () => {
  const el = document.getElementById('wallet-adapters-panel');
  if (el.style.display !== 'none') { el.style.display = 'none'; return; }
  el.style.display = '';
  try {
    const r = await fetch('/api/wallet/adapters');
    const d = await r.json();
    el.innerHTML = '';
    d.adapters.forEach(a => {
      const div = document.createElement('div');
      div.className = 'wadapter' + (a.name === d.active ? ' active' : '');
      div.innerHTML =
        `<div><span class="wadapter-name">${_esc(a.label)}</span>` +
        `<span class="wadapter-tier">[${_esc(a.tier)}]</span></div>` +
        `<div class="wadapter-status">${_esc(a.status)}</div>`;
      el.appendChild(div);
    });
  } catch {}
};

loadWalletBalance();
loadDocs();
loadEmailStatus();
loadPerms();
loadSchedule();

// ── Scheduled tasks ───────────────────────────────────────────────────────────

const SCHED_SEEN_KEY = 'vokter_sched_seen';

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _fmtInterval(s) {
  if (s >= 86400 && s % 86400 === 0) return (s / 86400) + 'd';
  if (s >= 3600  && s % 3600  === 0) return (s / 3600)  + 'h';
  return Math.round(s / 60) + 'm';
}

function _fmtTs(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString(undefined, {dateStyle:'short', timeStyle:'short'});
}

function renderTask(task) {
  const wrap = document.createElement('div');
  wrap.className = 'stask';
  wrap.dataset.id = task.id;

  const hdr = document.createElement('div');
  hdr.className = 'stask-header';
  hdr.innerHTML =
    `<span class="stask-name">${_esc(task.name)}</span>` +
    `<span class="stask-interval">${_fmtInterval(task.interval_seconds)}</span>` +
    `<span class="stask-status ${task.enabled ? 'on' : 'off'}">${task.enabled ? 'on' : 'paused'}</span>` +
    `<button class="btn-sm" style="font-size:.72rem;padding:3px 7px" data-toggle>` +
      `${task.enabled ? 'Pause' : 'Resume'}` +
    `</button>` +
    `<button class="btn-sm danger" style="font-size:.72rem;padding:3px 7px" data-del>✕</button>`;
  wrap.appendChild(hdr);

  const runs = document.createElement('div');
  runs.className = 'stask-runs';
  runs.innerHTML = '<div class="tag" style="font-size:.78rem">Click to load runs…</div>';
  wrap.appendChild(runs);

  let runsLoaded = false;
  hdr.addEventListener('click', async (e) => {
    if (e.target.dataset.toggle !== undefined || e.target.dataset.del !== undefined) return;
    const open = runs.style.display === '';
    runs.style.display = open ? 'none' : '';
    if (!open && !runsLoaded) {
      try {
        const r = await fetch(`/api/schedule/${task.id}/runs`);
        const list = await r.json();
        runsLoaded = true;
        if (!list.length) { runs.innerHTML = '<div class="tag" style="font-size:.78rem">No runs yet.</div>'; return; }
        runs.innerHTML = '';
        list.forEach(run => {
          const d = document.createElement('div');
          d.className = 'srun';
          d.innerHTML =
            `<div>` +
              `<span class="srun-status ${_esc(run.status)}">${_esc(run.status)}</span>` +
              `<span style="color:var(--muted)">${_fmtTs(run.finished_at || run.started_at)}</span>` +
            `</div>` +
            (run.output ? `<div class="srun-output">${_esc(run.output.slice(0, 300))}</div>` : '');
          runs.appendChild(d);
        });
      } catch {}
    }
  });

  hdr.querySelector('[data-toggle]').onclick = async (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    btn.disabled = true;
    await fetch(`/api/schedule/${task.id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled: !task.enabled}),
    });
    loadSchedule();
  };

  hdr.querySelector('[data-del]').onclick = async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete task "${task.name}"?`)) return;
    await fetch(`/api/schedule/${task.id}`, {method: 'DELETE'});
    loadSchedule();
  };

  return wrap;
}

async function loadSchedule() {
  try {
    const r = await fetch('/api/schedule');
    const tasks = await r.json();
    const el = document.getElementById('schedule-list');
    if (!tasks.length) {
      el.innerHTML = '<div class="tag" style="font-size:.82rem">No scheduled tasks yet.</div>';
    } else {
      el.innerHTML = '';
      tasks.forEach(t => el.appendChild(renderTask(t)));
    }
    await checkNewRuns();
  } catch {}
}

async function checkNewRuns() {
  const seenRaw = localStorage.getItem(SCHED_SEEN_KEY);
  const seen = seenRaw ? parseFloat(seenRaw) : null;
  try {
    const url = seen !== null
      ? `/api/schedule/runs/recent?since=${seen}&limit=20`
      : '/api/schedule/runs/recent?limit=20';
    const r = await fetch(url);
    const runs = await r.json();
    const badge = document.getElementById('schedule-badge-el');
    const newDone = runs.filter(r => r.finished_at && (seen === null || r.finished_at > seen));
    if (newDone.length) {
      badge.textContent = newDone.length + ' new';
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
  } catch {}
}

document.getElementById('btn-sched-new').onclick = () => {
  const form = document.getElementById('schedule-form');
  form.style.display = form.style.display === 'none' ? '' : 'none';
};

document.getElementById('btn-sched-cancel').onclick = () => {
  document.getElementById('schedule-form').style.display = 'none';
};

document.getElementById('btn-sched-refresh').onclick = () => {
  // Mark badge as seen when user refreshes
  localStorage.setItem(SCHED_SEEN_KEY, Date.now() / 1000);
  document.getElementById('schedule-badge-el').style.display = 'none';
  loadSchedule();
};

document.getElementById('btn-sched-create').onclick = async () => {
  const name     = document.getElementById('sched-name').value.trim();
  const goal     = document.getElementById('sched-goal').value.trim();
  const interval = document.getElementById('sched-interval').value.trim();
  if (!name || !goal || !interval) { alert('Fill in name, goal, and interval (e.g. 1h)'); return; }
  const btn = document.getElementById('btn-sched-create');
  btn.disabled = true;
  try {
    const r = await fetch('/api/schedule', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, goal, interval}),
    });
    const d = await r.json();
    if (!r.ok) { alert(d.detail || 'Error creating task'); btn.disabled = false; return; }
    document.getElementById('sched-name').value = '';
    document.getElementById('sched-goal').value = '';
    document.getElementById('sched-interval').value = '';
    document.getElementById('schedule-form').style.display = 'none';
    loadSchedule();
  } catch (e) { alert('Error: ' + e); }
  btn.disabled = false;
};

// Poll for new completed runs every 5 minutes (passive — badge only)
setInterval(checkNewRuns, 300_000);

// ── Settings ────────────────────────────────────────────────────────────────
function applyConfig(cfg) {
  if (!cfg) return;
  document.getElementById('cfg-agent-name').value  = cfg.agent_name  || 'Vokter';
  document.getElementById('cfg-tone').value        = cfg.tone        || 'neutral';
  document.getElementById('cfg-mode').value        = cfg.mode        || 'conversational';
  document.getElementById('cfg-language').value    = cfg.language    || 'auto';
  document.getElementById('cfg-chat-model').value  = cfg.chat_model  || '';
  document.getElementById('cfg-embed-model').value = cfg.embed_model || '';
  document.getElementById('cfg-max-history').value = cfg.max_history || 20;
  document.getElementById('cfg-rag-chunks').value  = cfg.rag_chunks  || 4;
  document.getElementById('hdr-agent-name').textContent = cfg.agent_name || 'Vokter';
}

async function loadConfig() {
  try { applyConfig(await (await fetch('/api/config')).json()); } catch {}
}

async function loadAvatar() {
  const hdr     = document.getElementById('hdr-avatar');
  const preview = document.getElementById('cfg-avatar-preview');
  const placeholder = document.getElementById('cfg-avatar-placeholder');
  const removeBtn   = document.getElementById('btn-cfg-avatar-remove');
  try {
    const r = await fetch('/api/config/avatar', {method: 'HEAD'});
    if (r.ok) {
      const url = '/api/config/avatar?t=' + Date.now();
      hdr.src     = url;  hdr.style.display = '';
      preview.src = url;  preview.style.display = '';
      placeholder.style.display = 'none';
      removeBtn.style.display   = '';
    } else {
      hdr.style.display     = 'none';
      preview.style.display = 'none';
      placeholder.style.display = '';
      removeBtn.style.display   = 'none';
    }
  } catch {
    hdr.style.display = 'none';
  }
}

document.getElementById('cfg-avatar-file').onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/config/avatar', {method: 'POST', body: fd});
    if (r.ok) { await loadAvatar(); }
    else { const j = await r.json(); alert(j.detail || 'Upload failed'); }
  } catch (err) { alert('Error: ' + err); }
  e.target.value = '';
};

document.getElementById('btn-cfg-avatar-remove').onclick = async () => {
  if (!confirm('Remove avatar?')) return;
  await fetch('/api/config/avatar', {method: 'DELETE'});
  loadAvatar();
};

document.getElementById('btn-settings').onclick = () => {
  const p = document.getElementById('settings');
  const open = p.style.display !== 'none';
  p.style.display = open ? 'none' : '';
  if (!open) { loadConfig(); loadAvatar(); }
};

document.getElementById('btn-cfg-save').onclick = async () => {
  const btn    = document.getElementById('btn-cfg-save');
  const status = document.getElementById('cfg-status');
  btn.disabled = true;
  status.textContent = 'Saving…';
  const maxH = parseInt(document.getElementById('cfg-max-history').value);
  const ragC = parseInt(document.getElementById('cfg-rag-chunks').value);
  try {
    const r = await fetch('/api/config', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        agent_name:  document.getElementById('cfg-agent-name').value.trim() || null,
        tone:        document.getElementById('cfg-tone').value,
        mode:        document.getElementById('cfg-mode').value,
        language:    document.getElementById('cfg-language').value,
        chat_model:  document.getElementById('cfg-chat-model').value.trim()  || null,
        embed_model: document.getElementById('cfg-embed-model').value.trim() || null,
        max_history: isNaN(maxH) ? null : maxH,
        rag_chunks:  isNaN(ragC) ? null : ragC,
      }),
    });
    const d = await r.json();
    if (r.ok) {
      document.getElementById('hdr-agent-name').textContent = d.agent_name || 'Vokter';
      status.textContent = 'Saved ✓';
      setTimeout(() => { status.textContent = ''; }, 2000);
    } else {
      status.textContent = d.detail || 'Error';
    }
  } catch (e) { status.textContent = 'Error: ' + e; }
  btn.disabled = false;
};

// ── First-run welcome wizard ──────────────────────────────────────────────────
// TWO INDEPENDENT LANGUAGE AXES — do NOT fuse them back into one value:
//   • AGENT_LANGS      = the language Vokter chats/speaks/listens in (the 7 of v1).
//        The user picks this at onboarding; it governs the three capas (chat + TTS +
//        STT) and is stored as agent_config.language.
//   • ONBOARDING_LANGS = the languages the wizard's OWN text (chrome) is translated
//        into (only those with a full I18N dict). Running the agent in French while the
//        wizard chrome shows English is fine and expected — the chrome catches up on its
//        own schedule and must NEVER gate which agent language a user can choose.
// (The wizard used to fuse these into ONE value, capping the agent language to the
// translated set — that was a bug; this split fixes it. Keep them separate.)
// Adding an AGENT language: mirror app/languages.py (voice + STT + chat are wired there).
// Adding a CHROME translation: add its I18N dict + an ONBOARDING_LANGS entry. Priority
// (by AFFINITY, not market size): Norwegian, Swedish, Dutch, Finnish, Polish, Hungarian,
// Catalan, Portuguese. Only ship a chrome language once fully translated.
const AGENT_LANGS = [
  {code: 'en', label: 'English'},
  {code: 'es', label: 'Español'},
  {code: 'fr', label: 'Français'},
  {code: 'de', label: 'Deutsch'},
  {code: 'it', label: 'Italiano'},
  {code: 'pt', label: 'Português'},
  {code: 'nl', label: 'Nederlands'},
];
const ONBOARDING_LANGS = [
  {code: 'en', label: 'English'},
  {code: 'es', label: 'Español'},
];

// Chrome language for a given agent language: use its own chrome if translated, else
// English — the agent stays in its language either way (the two axes never block).
function _chromeFor(agentLang) { return I18N[agentLang] ? agentLang : 'en'; }
// Deny-closed: only ever return one of the 7 agent languages, else English. A locale
// outside the list (Greek, Russian, Swedish, the parked ca/pl…) pre-selects English —
// never an option absent from the list. Same fail-closed rule the backend uses.
function _agentLangFor(code) { return AGENT_LANGS.some(l => l.code === code) ? code : 'en'; }

const I18N = {
  en: {
    welcome_title: "Welcome to Vokter",
    welcome_sub: "Your private AI guardian. Everything runs on your machine — not a single byte leaves here.",
    choose_lang: "Choose your language",
    lang_note: "This sets the language Vokter speaks to you. You can change it later in Settings.",
    name_title: "Name your guardian",
    name_sub: "What should it be called? You can change this any time.",
    avatar_label: "Picture (optional)",
    avatar_hint: "Upload an image — jpg, png or webp.",
    char_title: "How should it talk to you?",
    tone_label: "Tone",
    tone_formal: "Formal", tone_formal_d: "Professional and precise.",
    tone_neutral: "Neutral", tone_neutral_d: "Plain and balanced.",
    tone_friendly: "Friendly", tone_friendly_d: "Warm and encouraging.",
    mode_label: "Style",
    mode_productive: "To the point", mode_productive_d: "Short, direct answers.",
    mode_conversational: "Conversational", mode_conversational_d: "Explains a little more.",
    done_title: "You're all set, {name}",
    done_sub: "Upload your first document and ask anything about it. What it learns stays on your disk.",
    back: "Back", next: "Next", skip: "Skip", start: "Start using Vokter",
  },
  es: {
    welcome_title: "Bienvenido a Vokter",
    welcome_sub: "Tu guardián de IA privado. Todo se ejecuta en tu máquina — no sale ni un byte de aquí.",
    choose_lang: "Elige tu idioma",
    lang_note: "Esto fija el idioma en el que Vokter te habla. Puedes cambiarlo luego en Ajustes.",
    name_title: "Ponle nombre a tu guardián",
    name_sub: "¿Cómo quieres llamarlo? Puedes cambiarlo cuando quieras.",
    avatar_label: "Imagen (opcional)",
    avatar_hint: "Sube una imagen — jpg, png o webp.",
    char_title: "¿Cómo quieres que te hable?",
    tone_label: "Tono",
    tone_formal: "Formal", tone_formal_d: "Profesional y preciso.",
    tone_neutral: "Neutral", tone_neutral_d: "Claro y equilibrado.",
    tone_friendly: "Cercano", tone_friendly_d: "Cálido y alentador.",
    mode_label: "Estilo",
    mode_productive: "Al grano", mode_productive_d: "Respuestas cortas y directas.",
    mode_conversational: "Conversacional", mode_conversational_d: "Explica un poco más.",
    done_title: "Todo listo, {name}",
    done_sub: "Sube tu primer documento y pregúntale lo que quieras. Lo que aprende se queda en tu disco.",
    back: "Atrás", next: "Siguiente", skip: "Saltar", start: "Empezar a usar Vokter",
  },
};

const ONB_STEPS = 4;
const onbState = { step: 0, agentLang: 'en', chromeLang: 'en', agent_name: '', tone: 'neutral', mode: 'conversational' };

// chrome axis, not agent axis: the wizard text follows chromeLang (en/es), the agent follows agentLang (7).
function _onbT() { return I18N[onbState.chromeLang] || I18N.en; }

function _onbEl(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

function _onbOption(container, label, desc, selected, onPick) {
  const b = _onbEl('button', 'onb-opt' + (selected ? ' sel' : ''));
  b.appendChild(_onbEl('span', null, label));
  if (desc) b.appendChild(_onbEl('span', 'onb-opt-d', desc));
  b.onclick = onPick;
  container.appendChild(b);
  return b;
}

function renderOnb() {
  const t = _onbT();
  const body = document.getElementById('onb-body');
  body.innerHTML = '';
  document.getElementById('onb-skip').textContent = t.skip;

  // step dots
  const dots = document.getElementById('onb-dots');
  dots.innerHTML = '';
  for (let i = 0; i < ONB_STEPS; i++) {
    dots.appendChild(_onbEl('div', 'onb-dot' + (i === onbState.step ? ' on' : '')));
  }

  if (onbState.step === 0) {
    body.appendChild(_onbEl('div', 'onb-h', t.welcome_title));
    body.appendChild(_onbEl('div', 'onb-sub', t.welcome_sub));
    body.appendChild(_onbEl('div', 'onb-field-label', t.choose_lang));
    // The 7 AGENT languages (not the chrome set): picking one sets the agent language and
    // pulls the chrome along ONLY if that language is translated, else chrome stays English.
    AGENT_LANGS.forEach(l => {
      _onbOption(body, l.label, null, onbState.agentLang === l.code, () => {
        onbState.agentLang = l.code;
        onbState.chromeLang = _chromeFor(l.code);
        renderOnb();
      });
    });
    body.appendChild(_onbEl('div', 'onb-note', t.lang_note));

  } else if (onbState.step === 1) {
    body.appendChild(_onbEl('div', 'onb-h', t.name_title));
    body.appendChild(_onbEl('div', 'onb-sub', t.name_sub));
    const input = _onbEl('input');
    input.id = 'onb-name';
    input.type = 'text';
    input.placeholder = 'Vokter';
    input.value = onbState.agent_name;
    input.oninput = () => { onbState.agent_name = input.value; };
    body.appendChild(input);

    body.appendChild(_onbEl('div', 'onb-field-label', t.avatar_label));
    const row = _onbEl('div', 'onb-avatar-row');
    const preview = _onbEl('img'); preview.id = 'onb-avatar-preview'; preview.alt = 'avatar';
    const placeholder = _onbEl('div', null, '🛡️'); placeholder.id = 'onb-avatar-placeholder';
    const upWrap = _onbEl('label', 'onb-avatar-upload', t.avatar_hint);
    const upInput = _onbEl('input'); upInput.type = 'file';
    upInput.accept = '.jpg,.jpeg,.png,.webp,.gif';
    upInput.onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const fd = new FormData(); fd.append('file', file);
      try {
        const r = await fetch('/api/config/avatar', {method: 'POST', body: fd});
        if (r.ok) {
          const url = '/api/config/avatar?t=' + Date.now();
          preview.src = url; preview.style.display = '';
          placeholder.style.display = 'none';
        }
      } catch {}
      e.target.value = '';
    };
    upWrap.appendChild(upInput);
    row.appendChild(preview); row.appendChild(placeholder); row.appendChild(upWrap);
    body.appendChild(row);

  } else if (onbState.step === 2) {
    body.appendChild(_onbEl('div', 'onb-h', t.char_title));
    body.appendChild(_onbEl('div', 'onb-field-label', t.tone_label));
    [['formal', t.tone_formal, t.tone_formal_d],
     ['neutral', t.tone_neutral, t.tone_neutral_d],
     ['friendly', t.tone_friendly, t.tone_friendly_d]].forEach(([val, label, desc]) => {
      _onbOption(body, label, desc, onbState.tone === val, () => {
        onbState.tone = val; renderOnb();
      });
    });
    body.appendChild(_onbEl('div', 'onb-field-label', t.mode_label));
    [['productive', t.mode_productive, t.mode_productive_d],
     ['conversational', t.mode_conversational, t.mode_conversational_d]].forEach(([val, label, desc]) => {
      _onbOption(body, label, desc, onbState.mode === val, () => {
        onbState.mode = val; renderOnb();
      });
    });

  } else {
    const name = onbState.agent_name.trim() || 'Vokter';
    // function replacement: a name containing $-sequences must not be read as a
    // String.replace special pattern ($&, $', …).
    body.appendChild(_onbEl('div', 'onb-h', t.done_title.replace('{name}', () => name)));
    body.appendChild(_onbEl('div', 'onb-sub', t.done_sub));
  }

  // nav buttons
  const nav = document.getElementById('onb-nav');
  nav.innerHTML = '';
  if (onbState.step > 0) {
    const back = _onbEl('button', 'btn-sm', t.back);
    back.id = 'onb-back';
    back.onclick = () => { onbState.step--; renderOnb(); };
    nav.appendChild(back);
  }
  const primary = _onbEl('button', 'btn', onbState.step === ONB_STEPS - 1 ? t.start : t.next);
  primary.onclick = () => {
    if (onbState.step < ONB_STEPS - 1) { onbState.step++; renderOnb(); }
    else finishOnboarding();
  };
  nav.appendChild(primary);
}

function startOnboarding(cfg) {
  // Seed from the current config so an existing user who clicks THROUGH the
  // wizard (rather than Skip) keeps their name/tone/mode instead of having them
  // reset to blank defaults. Skip preserves everything regardless.
  if (cfg) {
    onbState.agent_name = cfg.agent_name || '';
    if (cfg.tone) onbState.tone = cfg.tone;
    if (cfg.mode) onbState.mode = cfg.mode;
  }
  // Step 0 is an explicit AGENT-language choice, pre-selected from the saved config (a
  // returning user clicking through) or else the system locale, mapped DENY-CLOSED to the
  // 7 (anything outside → English). The user still confirms; we never pre-pick a language
  // that isn't in the list. Chrome then follows only if that language is translated.
  const cur  = cfg && cfg.language;
  const nav2 = (navigator.language || 'en').slice(0, 2).toLowerCase();
  const seed = (cur && AGENT_LANGS.some(l => l.code === cur)) ? cur : nav2;
  onbState.agentLang  = _agentLangFor(seed);
  onbState.chromeLang = _chromeFor(onbState.agentLang);
  onbState.step = 0;
  document.getElementById('onboarding').classList.add('open');
  renderOnb();
}

function closeOnboarding() {
  document.getElementById('onboarding').classList.remove('open');
}

// Skip and Finish both close OPTIMISTICALLY — a failed config write must never
// trap the user behind the overlay. Worst case the wizard reappears next load.
function finishOnboarding() {
  const patch = {
    agent_name: onbState.agent_name.trim() || null,
    tone: onbState.tone,
    mode: onbState.mode,
    language: onbState.agentLang,   // AGENT axis — governs chat + voice + STT
    onboarded: true,
  };
  closeOnboarding();
  fetch('/api/config', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(patch),
  })
    // Trigger 1: the language is now saved → download ITS voice, with in-app progress
    // (the header pill). Chained on SAVE SUCCESS (not finally) because ensure reads the
    // stored language; on a failed save we must not fetch the stale language's voice.
    // Fire-and-forget: onboarding has already closed, so a download failure can NEVER
    // trap the user — chat + STT work, the voice just shows ⚠ until a retry succeeds.
    .then(r => { if (r && r.ok) ensureVoice(); })
    .catch(() => {})
    .finally(() => { loadConfig(); loadAvatar(); });
}

function skipOnboarding() {
  closeOnboarding();
  fetch('/api/config', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({onboarded: true}),
  }).catch(() => {}).finally(() => { loadConfig(); loadAvatar(); });
}

document.getElementById('onb-skip').onclick = skipOnboarding;

// ── Boot ──────────────────────────────────────────────────────────────────────
async function initApp() {
  let cfg = null;
  try { cfg = await (await fetch('/api/config')).json(); }
  catch { loadAvatar(); return; }   // API unreachable → show the normal UI, never block
  applyConfig(cfg);
  loadAvatar();
  // Trigger 3 (surface): the backend's opportunistic startup fetch may already be
  // downloading the current language's voice — reflect it in the pill. If the voice is
  // ready, the pill stays hidden; if that fetch failed (e.g. offline boot), it shows ⚠.
  _watchVoice();
  if (cfg.onboarded !== '1') startOnboarding(cfg);
}

initApp();
