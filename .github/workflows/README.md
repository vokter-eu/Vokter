# CI — what runs here, and what deliberately does not

`ci.yml` runs on every push to `main` and every PR, on a clean `ubuntu-latest`
runner. It is the **anti-regression ratchet**: the build goes red if any test
fails or if the dependency tree drifts from the hash-pinned lock.

## What runs (tests/run_ci.sh)

All of these are hermetic **script-tests** (own `main`, `sys.exit(1)` on red —
NOT pytest; pytest collects nothing from them). Invoked the way each file's
docstring documents, against a throwaway `VOKTER_DB`:

- `safety_test` — capability-gateway guarantees + the **source-level tripwires**
  (every guarded handler calls `enforce_http(...)`; `--no-sandbox` never appears
  in the browser launcher).
- `loopback_auth_test` — the C1 loopback hardening (Host gate, `require_human`,
  redirect validation).
- `cage_env_test` — A4: the browser-cage env is built by inclusion, no secrets.
- `boot_no_beacon_test` — a default boot makes ZERO outbound calls; voice is
  on-demand.
- `memory_gate_test`, `conversation_gate_test`, `memory_budget_test` — P2
  personal-memory gate + budget cap.
- `migration_test` — keyed SQLCipher fixture, Ollama stubbed.
- `secret_leakage_probe_test`, `a2a_trust_boundary_test`, `oauth_broker_test`.
- Tier-3 structural/unit guarantees (paths stay DORMANT; mocks, no network, no
  funds): `web_pay_test`, `web_pay_e2e_test`, `web_pay_nwc_test`,
  `web_pay_x402_test`, `web_login_e2e_test`, `web_login_injector_test`,
  `web_login_confirm_render_test`, `email_send_test`, `injector_test`.
- `sideload_test` — sovereign GGUF sha256 pin (local `file://` mirror).
- `hwdetect_test`, `entity_link_eval`, `entity_relation_eval`.
- `originguard_test.js` — Electron navigation-fence predicates (node).

The venv is built from `app/requirements.lock` with `--require-hashes`, so CI
also acts as the **supply-chain gate**: a substituted or drifted package aborts
the build.

## What does NOT run here — and where it lives instead

| Not in CI | Why | Real venue |
|---|---|---|
| Full containment fire-test (bwrap + pasta + nftables + REAL Chromium + hostile page + dual-stack IPv6 probes) | needs KVM/VM, kernel namespaces, pasta — not available/faithful on a shared runner | `vokter-backend --containment-selftest` on the **frozen .deb** in the clean-VM acceptance (151/0, see vokter-internal ACCEPTANCE logs). A "lite" subset would test a *different* cage than the one shipped — rejected on purpose: green here must never be read as "containment verified". |
| `memory_precision_eval`, `memory_retrieval_eval` | need a live Ollama + bge-m3 (model-quality evals, not regressions) | dev host / VM with the real engine |
| `browser_containment_fire.py` | needs bwrap/pasta on the host | clean-VM acceptance |
| GUI (Electron renderer) behaviour | needs a display + real shell | xvfb GUI smoke in the acceptance runbook |
| glibc release floor | build-time property, not a test | `desktop/freeze/build.sh` hard gate + objdump check on the final .deb |

**Rule of thumb:** CI green = "no code/dependency regression slipped in".
Shipping confidence still comes from the clean-VM acceptance against the frozen
artifact (RELEASE_BUILD_METHOD.md).
