// Phase 3.3-B: the keychain-guardrail recovery screen. PURE (ev -> HTML string)
// so the copy is unit-tested headlessly (guardrail_screen.test.js), composed from
// FACTS only — no "guardrail"/"keychain-unreachable" jargon reaches the user.
//
// Honesty rule (Bilal): never promise a recovery the app can't perform. The only
// clickable action is [2] "Start fresh" (create-only, never deletes). For a locked
// keychain we give the REAL fix in words — unlock it and reopen — instead of a
// button that can't decrypt without the key. For data detected elsewhere we
// reassure it's safe, WITHOUT implying an in-app "recover" button (there isn't one).
'use strict';

function guardrailHtml(ev) {
  const kc = ev && ev.keychain;
  let reason = '';
  if (kc === 'unreachable') reason = ' — and it couldn’t reach your keychain (it may be locked)';
  else if (kc === 'has_key') reason = ' — though it found a sign of a previous Vokter';

  // The honest fix for the false positive (locked keychain + real data): words, not
  // a button — the app cannot decrypt without the key, which is in that keychain.
  const unlockNote = kc === 'unreachable'
    ? '<p>It looks like your keychain is locked. If you’ve used Vokter before, unlock it '
      + '(log back in) and reopen Vokter before starting fresh.</p>'
    : '';

  // Data found at a known prior location — reassure, do NOT promise a recover action.
  const candidateNote = ev && ev.has_candidates
    ? '<p class="safe">It also found what looks like earlier data on this computer — '
      + 'that’s safe too, and starting fresh won’t delete it.</p>'
    : '';

  return `<!doctype html><meta charset="utf-8">
<style>
  html,body{height:100%;margin:0}
  body{background:#0f1115;color:#e6e6e6;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
       display:flex;flex-direction:column;align-items:center;justify-content:center;
       gap:1.05rem;padding:2.5rem;text-align:center;line-height:1.5}
  .shield{font-size:3rem;line-height:1}
  h1{font-size:1.2rem;font-weight:600;margin:0}
  p{color:#b8bcc4;max-width:34rem;margin:0}
  .safe{color:#8b909a;font-size:.85rem}
  button{margin-top:.5rem;background:#6ea8fe;color:#0f1115;border:0;border-radius:8px;
         font-size:.95rem;font-weight:600;padding:.7rem 1.4rem;cursor:pointer}
  button:disabled{opacity:.55;cursor:default}
</style>
<div class="shield">🛡️</div>
<h1>Vokter didn’t start</h1>
<p>Vokter looked for your data but couldn’t find it where it expected${reason}. To be safe it stopped rather than starting empty — it never assumes your data is gone.</p>
<p class="safe">Nothing has been deleted — if you’ve used Vokter before, your data and your key are safe and untouched.</p>
${unlockNote}
${candidateNote}
<button id="fresh">Start fresh</button>
<p class="safe">Creates a new, empty Vokter. Nothing you already have is deleted. Only choose this if you’re new here.</p>
<script>
  const b = document.getElementById('fresh');
  b.addEventListener('click', () => {
    b.disabled = true; b.textContent = 'Starting…';
    if (window.vokter) window.vokter.startFresh();
  });
</script>`;
}

module.exports = { guardrailHtml };
