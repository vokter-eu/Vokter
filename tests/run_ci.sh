#!/usr/bin/env bash
# CI suite runner — the anti-regression ratchet.
#
# Every test here is a SCRIPT-test (its own main + check()/FAILS + sys.exit(1) on red;
# they are NOT pytest tests — pytest collects nothing from them). Each is invoked the
# way its docstring documents: `python tests/<name>.py` with throwaway VOKTER_DB /
# VOKTER_DB_KEY env. The interpreter comes from $PYTHON (CI: a venv built from
# app/requirements.lock with --require-hashes; dev: desktop/runtime/venv).
#
# HERMETIC ONLY. What does NOT run here, and where its real venue is:
#   * the full containment fire-test (bwrap + pasta + nftables + real Chromium +
#     dual-stack IPv6) — needs a VM; venue = `vokter-backend --containment-selftest`
#     on the frozen .deb in the clean-VM acceptance (151/0 as of 2026-11-25).
#   * memory_precision_eval / memory_retrieval_eval — need a live Ollama with bge-m3.
#   * browser_containment_fire.py — needs bwrap/pasta on the host.
# See .github/workflows/README.md for the full honesty map.
set -u
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-desktop/runtime/venv/bin/python}"
command -v "$PYTHON" >/dev/null || PYTHON="$(pwd)/${PYTHON}"

SCRATCH="$(mktemp -d /tmp/vokter-ci.XXXXXX)"
trap 'rm -rf "$SCRATCH"' EXIT

PASS=(); FAIL=()
run () { # run <name> [extra env K=V ...]
  local name="$1"; shift
  local dir="$SCRATCH/$name"; mkdir -p "$dir"
  echo "──── $name"
  if env VOKTER_DB="$dir/x.db" VOKTER_DB_KEY=ci-throwaway "$@" \
       "$PYTHON" "tests/$name.py" > "$dir/out.log" 2>&1; then
    echo "PASS $name"; PASS+=("$name")
  else
    echo "FAIL $name (exit $?) — last lines:"; tail -15 "$dir/out.log" | sed 's/^/    /'
    FAIL+=("$name")
  fi
}

# ── capability gateway + its source-level tripwires (enforce_http wiring, no --no-sandbox) ──
run safety_test
# ── loopback auth hardening (C1: host-gate, require_human, redirect validation) ──
run loopback_auth_test
# ── browser cage env by inclusion (A4) ──
run cage_env_test
# ── zero outbound on default boot; voice is on-demand (beacon) ──
run boot_no_beacon_test
# ── P2 personal-memory gate + conversation gate + budget cap ──
run memory_gate_test
run conversation_gate_test
run memory_budget_test
# ── DB migration on a keyed SQLCipher fixture (Ollama stubbed) ──
run migration_test
# ── secret non-leakage probe ──
run secret_leakage_probe_test
# ── A2A trust boundary ──
run a2a_trust_boundary_test
# ── OAuth broker (PKCE, loopback) ──
run oauth_broker_test
# ── Tier-3 structural/unit guarantees (all DORMANT paths; mocks, no network, no funds) ──
run web_pay_test
run web_pay_e2e_test
run web_pay_nwc_test
run web_pay_x402_test
run web_login_e2e_test
run web_login_injector_test
run web_login_confirm_render_test
run email_send_test
run injector_test
# ── sovereign sideload sha256 (file:// mirror, Ollama API stubbed) ──
run sideload_test
# ── hardware tier picker ──
run hwdetect_test
# ── entity layer heuristics (stdlib-only evals, adversarial fixtures) ──
run entity_link_eval
run entity_relation_eval

# ── Electron origin guard (pure predicates; node) ──
echo "──── originguard_test.js"
if node tests/originguard_test.js > "$SCRATCH/originguard.log" 2>&1; then
  echo "PASS originguard_test.js"; PASS+=("originguard_test.js")
else
  echo "FAIL originguard_test.js — last lines:"; tail -15 "$SCRATCH/originguard.log" | sed 's/^/    /'
  FAIL+=("originguard_test.js")
fi

echo
echo "==== CI suite: ${#PASS[@]} passed / ${#FAIL[@]} failed ===="
if [ "${#FAIL[@]}" -gt 0 ]; then
  printf 'RED: %s\n' "${FAIL[@]}"
  exit 1
fi
echo "ALL GREEN"
