"""Tier-3 Phase 1 — renderer regression guard for the web.login human-confirm sheet (step 6).

The confirm sheet (`loginConfirm` in app/static/app.js) is the LAST gate a human reads before a
credential-authorizing sign-in. It renders backend-asserted fields — the RESOLVED origin, the
identity, and for OAuth the provider + requested scopes — none of which the model supplies. This test
pins the security-critical rendering property so a future edit can't silently make that surface
HTML-injectable:

  POSITIVE — the sheet shows the backend-asserted fields (resolved origin, identity, provider, scopes;
             the password note on the password path), read straight from the (stubbed) phase-1 428.
  NEGATIVE — a field value containing HTML/script markup renders as INERT TEXT, not DOM: no element is
             injected into the sheet, the field nodes have no element children, and an onerror/inline
             <script> payload NEVER executes. This is exactly what breaks if someone swaps the
             field's `textContent` for `innerHTML`.

It drives the REAL app.js (not a copy): it loads the actual file under headless Chrome and renders
through the real code path — the exposed `window.__vokterStartWebLogin` seam + a stubbed bridge whose
phase-1 428 carries the crafted field values. So the guard protects the shipping code, not a replica.

Pure UI, no secret, no activation: the bridge is stubbed, the backend never runs, web.login stays
dormant/gated. Requires google-chrome + xvfb-run; SKIPs cleanly where a browser isn't available.

Run:  python tests/web_login_confirm_render_test.py
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

APP_JS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "static", "app.js"))

# Crafted field values carrying active HTML markup. If any field is ever rendered via innerHTML, the
# <img onerror>/<script> parses into DOM and (for onerror) executes — the negative assertions catch it.
# (Neutral example text only — no real handles/sites in the public repo.)
XSS_ID = '<img src=x onerror="window.__xss_id=1">you@example.com'
XSS_PROVIDER = '<b>Git<i>Hub</i></b>'
XSS_SCOPE = '<script>window.__xss_scope=1</script>drop-all'
# The resolved origin is the sheet's anti-phishing anchor — defense-in-depth: assert it too renders
# inert even though normalize_origin already guarantees an origin can't carry markup.
XSS_ORIGIN = '<img src=x onerror="window.__xss_origin=1">https://evil.example'

_HARNESS = r"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<textarea id="composer"></textarea>
<script src="file://__APP_JS__"></script>
<script>
(async () => {
  const out = {};
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  out.seam_exposed = (typeof window.__vokterStartWebLogin === 'function');
  if (!out.seam_exposed) { done(out); return; }

  // ---- OAuth path: provider + scopes + identity, all with HTML markup in the values ----
  window.vokter = { webLogin: async (req) => req.confirm
    ? { status: 200, body: { status: 'succeeded' } }
    : { status: 428, body: { detail: {
        resolved_origin: 'https://github.com',
        identity: __XSS_ID__, mode: 'oauth', provider: __XSS_PROVIDER__,
        scopes: ['read:user', __XSS_SCOPE__] } } } };
  window.__vokterStartWebLogin('github', 'me');       // fire; sheet stays open awaiting confirm
  await sleep(250);
  const ov = document.querySelector('.lgn-ov');
  out.oauth_rendered = !!ov;
  if (ov) {
    const u = ov.querySelector('.lgn-site .u');
    const idv = ov.querySelector('.lgn-row .v.mono');
    const pv = [...ov.querySelectorAll('.lgn-row .v')].find(e => !e.classList.contains('mono'));
    const scopeLis = [...ov.querySelectorAll('.lgn-scopes li')];
    out.oauth_origin_text = u && u.textContent;
    out.oauth_identity_text = idv && idv.textContent;
    out.oauth_identity_child_elems = idv ? idv.childElementCount : -1;
    out.oauth_provider_text = pv && pv.textContent;
    out.oauth_provider_child_elems = pv ? pv.childElementCount : -1;
    out.oauth_scope_texts = scopeLis.map(li => li.textContent);
    out.oauth_scope_child_elems = scopeLis.reduce((n, li) => n + li.childElementCount, 0);
    // markup must NOT have parsed into the sheet (the static header/lock SVGs are <svg>, not these)
    out.oauth_injected_elems = ov.querySelectorAll('img, script, b, i').length;
    ov.querySelector('.lgn-cancel').click();          // close before the next render
    await sleep(50);
  }

  // ---- Password path: the password note, identity with markup ----
  window.vokter = { webLogin: async (req) => req.confirm
    ? { status: 200, body: { status: 'succeeded' } }
    : { status: 428, body: { detail: {
        resolved_origin: 'https://login.example.com',
        identity: __XSS_ID__, mode: 'password', provider: null, scopes: null } } } };
  window.__vokterStartWebLogin('example', 'me');
  await sleep(250);
  const ov2 = document.querySelector('.lgn-ov');
  out.pw_rendered = !!ov2;
  if (ov2) {
    const idv2 = ov2.querySelector('.lgn-row .v.mono');
    const note = ov2.querySelector('.lgn-reassure');
    out.pw_origin_text = (ov2.querySelector('.lgn-site .u') || {}).textContent;
    out.pw_identity_text = idv2 && idv2.textContent;
    out.pw_identity_child_elems = idv2 ? idv2.childElementCount : -1;
    out.pw_note_text = note && note.textContent;
    out.pw_has_scopes = !!ov2.querySelector('.lgn-scopes');
    out.pw_injected_elems = ov2.querySelectorAll('img, script, b, i').length;
    ov2.querySelector('.lgn-cancel').click();
    await sleep(50);
  }

  // ---- Resolved-origin field (the anti-phishing anchor): markup must render inert text too ----
  window.vokter = { webLogin: async (req) => req.confirm
    ? { status: 200, body: { status: 'succeeded' } }
    : { status: 428, body: { detail: {
        resolved_origin: __XSS_ORIGIN__, identity: 'you@example.com',
        mode: 'password', provider: null, scopes: null } } } };
  window.__vokterStartWebLogin('origincheck', 'me');
  await sleep(250);
  const ov3 = document.querySelector('.lgn-ov');
  out.origin_rendered = !!ov3;
  if (ov3) {
    const u3 = ov3.querySelector('.lgn-site .u');
    out.origin_markup_text = u3 && u3.textContent;
    out.origin_child_elems = u3 ? u3.childElementCount : -1;
    out.origin_injected_elems = ov3.querySelectorAll('img, script, b, i').length;
    ov3.querySelector('.lgn-cancel').click();
  }

  // did any onerror/inline-script payload execute? (identity, scope, AND the origin anchor)
  out.xss_executed = (window.__xss_id === 1) || (window.__xss_scope === 1) || (window.__xss_origin === 1);
  done(out);

  function done(o) {
    const d = document.createElement('div'); d.id = 'testresult';
    d.textContent = JSON.stringify(o); document.body.appendChild(d);
  }
})();
</script></body></html>
"""


def _skip(msg):
    print(f"SKIP web_login_confirm_render: {msg}")
    sys.exit(0)


def _run_headless(html_path):
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        _skip("no google-chrome/chromium on PATH (renderer guard runs where a browser is available)")
    xvfb = shutil.which("xvfb-run")
    argv = ([xvfb, "-a"] if xvfb else []) + [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--virtual-time-budget=6000", "--dump-dom", f"file://{html_path}"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        print("FAIL headless render timed out"); sys.exit(1)
    m = re.search(r'<div id="testresult">(.*?)</div>', p.stdout, re.S)
    if not m:
        print("FAIL no testresult in rendered DOM")
        print("--- chrome stdout tail ---"); print(p.stdout[-1500:])
        print("--- chrome stderr tail ---"); print(p.stderr[-800:])
        sys.exit(1)
    # --dump-dom serializes the result div's text as HTML, so the captured field values come back
    # entity-encoded (`<`→`&lt;`). Restore them before parsing — the only &-sequences present are the
    # entities Chrome introduced, so this round-trips the JSON exactly.
    return json.loads(html.unescape(m.group(1)))


def _js_lit(s):
    # A JS string literal safe to embed inside the harness's inline <script>: escape `</` to `<\/` so a
    # `</script>` inside a tested value can't prematurely close the tag. `<\/` evaluates back to `</` at
    # runtime, so the value the sheet actually renders is unchanged — the inertness assertions stay real.
    return json.dumps(s).replace("</", "<\\/")


def main():
    html = (_HARNESS
            .replace("__APP_JS__", APP_JS)
            .replace("__XSS_ID__", _js_lit(XSS_ID))
            .replace("__XSS_PROVIDER__", _js_lit(XSS_PROVIDER))
            .replace("__XSS_SCOPE__", _js_lit(XSS_SCOPE))
            .replace("__XSS_ORIGIN__", _js_lit(XSS_ORIGIN)))
    d = tempfile.mkdtemp(prefix="vkt_lgn_render_")
    hp = os.path.join(d, "harness.html")
    with open(hp, "w") as f:
        f.write(html)
    try:
        r = _run_headless(hp)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    checks = {
        # the real seam + both sheets actually rendered through the shipping code path
        "seam_exposed": r.get("seam_exposed") is True,
        "oauth_rendered": r.get("oauth_rendered") is True,
        "pw_rendered": r.get("pw_rendered") is True,
        "origin_rendered": r.get("origin_rendered") is True,
        # POSITIVE — backend-asserted fields are shown
        "oauth_shows_resolved_origin": r.get("oauth_origin_text") == "https://github.com",
        "oauth_shows_identity": r.get("oauth_identity_text") == XSS_ID,
        "oauth_shows_provider": r.get("oauth_provider_text") == XSS_PROVIDER,
        "oauth_shows_scopes": r.get("oauth_scope_texts") == ["read:user", XSS_SCOPE],
        "pw_shows_resolved_origin": r.get("pw_origin_text") == "https://login.example.com",
        "pw_shows_identity": r.get("pw_identity_text") == XSS_ID,
        "pw_shows_password_note": bool(r.get("pw_note_text")) and "password" in (r.get("pw_note_text") or "").lower(),
        "pw_has_no_scopes": r.get("pw_has_scopes") is False,
        # NEGATIVE — field markup is inert text, never DOM; nothing executed
        "oauth_identity_is_text_node": r.get("oauth_identity_child_elems") == 0,
        "oauth_provider_is_text_node": r.get("oauth_provider_child_elems") == 0,
        "oauth_scope_is_text_node": r.get("oauth_scope_child_elems") == 0,
        "oauth_no_injected_elements": r.get("oauth_injected_elems") == 0,
        "pw_identity_is_text_node": r.get("pw_identity_child_elems") == 0,
        "pw_no_injected_elements": r.get("pw_injected_elems") == 0,
        # NEGATIVE — the resolved-origin anchor (most security-critical field) renders inert too
        "origin_shows_as_inert_text": r.get("origin_markup_text") == XSS_ORIGIN,
        "origin_is_text_node": r.get("origin_child_elems") == 0,
        "origin_no_injected_elements": r.get("origin_injected_elems") == 0,
        "no_xss_executed": r.get("xss_executed") is False,
    }
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    fails = [k for k, v in checks.items() if not v]
    if fails:
        print("\n❌ FAILED:", fails)
        print("raw:", json.dumps(r)[:1200])
        sys.exit(1)
    print("\n✅ web.login confirm sheet renders backend-asserted fields via textContent; HTML field "
          "values are inert (no innerHTML injection). Regression guard green.")


if __name__ == "__main__":
    main()
