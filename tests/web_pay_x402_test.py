"""Tier-3 Phase 2 — web.pay opaque-crypto slice (x402 + direct on-chain): the LIVE handler, hermetic.

Drives the real web_pay_onchain() handler both phases (no network, no funds, no wallet) and asserts the
guards that define this slice:
  * enriched 428 carries verifiable=False + the opaque-destination caveat (backend-asserted, not renderer),
  * x402 resolves {asset, network, payTo, amount, resource_origin} from a 402 challenge and hands off an
    EIP-681 token-transfer; direct on-chain hands off BIP21 (BTC) / EIP-681 (ETH),
  * CONFIRM-INTEGRITY — the centerpiece: confirmed address A, phase-2 submits address B with A's token → 422,
  * inflated amount with the confirmed token → 422; missing token → 422,
  * non-human phase-1 → 403; the count throttle refuses past the ceiling.

Run:  uv run --python 3.12 --with fastapi --with pydantic python tests/web_pay_x402_test.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from fastapi import HTTPException

FAILS = []
def check(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)


def main():
    TOK = "x402-human-session-tok"
    os.environ["VOKTER_TIER3_SECRET_GATE"] = "passed"
    sys.modules["chat"] = types.ModuleType("chat")
    sys.modules["chat"].is_local_human_session = lambda m: m == TOK

    import web_pay as wp
    import web_pay_x402 as x4
    from web_pay import OnchainPayRequest as R

    USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"   # Base USDC contract (x402 asset)
    PAYTO = "0x1111111111111111111111111111111111111111"
    EVIL = "0x2222222222222222222222222222222222222222"
    CHAL = {"accepts": [{"scheme": "exact", "network": "base", "asset": USDC,
                         "payTo": PAYTO, "maxAmountRequired": "1000000",
                         "resource": "https://shop.example/article/42"}]}

    def phase1(**kw):
        try:
            wp.web_pay_onchain(R(**kw), x_vokter_human_session=kw.pop("_tok", TOK))
            return None, {}
        except HTTPException as e:
            return e.status_code, (e.detail if isinstance(e.detail, dict) else {})

    def phase2(**kw):
        try:
            return 200, wp.web_pay_onchain(R(confirm=True, **kw), x_vokter_human_session=TOK)
        except HTTPException as e:
            return e.status_code, (e.detail if isinstance(e.detail, dict) else {})

    # ── x402 ──
    x4._OC_LEDGER.clear()
    s, d = phase1(rail="x402", challenge=CHAL)
    check("x402 phase1 → 428", s == 428)
    check("x402 428 resolves payTo+amount from challenge", d.get("destination") == PAYTO and d.get("amount") == "1000000")
    check("x402 428 verifiable=False", d.get("verifiable") is False)
    check("x402 428 caveat present", "not verifiable" in str(d.get("caveat", "")))
    check("x402 428 shows resolvable resource origin", d.get("resource_origin") == "shop.example")
    check("x402 428 carries a confirm_token", bool(d.get("confirm_token")))
    tok = d.get("confirm_token", "")

    s, body = phase2(rail="x402", challenge=CHAL, confirm_token=tok)
    check("x402 phase2 → ready", s == 200 and body.get("status") == "ready")
    ho = (body or {}).get("handoff", {})
    check("x402 handoff is EIP-681 token transfer", ho.get("scheme") == "x402"
          and ho.get("uri", "").startswith(f"ethereum:{USDC}") and f"address={PAYTO}" in ho.get("uri", ""))
    check("x402 handoff requirement echoes payTo+amount", ho.get("requirement", {}).get("payTo") == PAYTO
          and ho.get("requirement", {}).get("maxAmountRequired") == "1000000")
    check("x402 phase2 keeps verifiable=False + caveat", body.get("verifiable") is False and "not verifiable" in str(body.get("caveat", "")))

    # CONFIRM-INTEGRITY — swap the payTo in phase-2 but reuse address-A's token → REJECTED
    x4._OC_LEDGER.clear()
    evil_chal = {"accepts": [dict(CHAL["accepts"][0], payTo=EVIL)]}
    s, _ = phase2(rail="x402", challenge=evil_chal, confirm_token=tok)
    check("x402 substituted payTo (A-token, B-address) → 422", s == 422)

    # inflated amount with the confirmed token → REJECTED
    x4._OC_LEDGER.clear()
    inflated = {"accepts": [dict(CHAL["accepts"][0], maxAmountRequired="9999999")]}
    s, _ = phase2(rail="x402", challenge=inflated, confirm_token=tok)
    check("x402 inflated amount (A-token) → 422", s == 422)

    # ── direct on-chain (native assets) ──
    BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
    ETH_TO = "0x3333333333333333333333333333333333333333"
    ETH_EVIL = "0x4444444444444444444444444444444444444444"

    x4._OC_LEDGER.clear()
    s, d = phase1(rail="onchain", asset="BTC", network="bitcoin", destination=BTC, amount="0.001")
    check("onchain BTC phase1 → 428 verifiable=False", s == 428 and d.get("verifiable") is False)
    btok = d.get("confirm_token", "")
    s, body = phase2(rail="onchain", asset="BTC", network="bitcoin", destination=BTC, amount="0.001", confirm_token=btok)
    check("onchain BTC handoff is BIP21", s == 200 and body.get("handoff", {}).get("uri") == f"bitcoin:{BTC}?amount=0.001")

    x4._OC_LEDGER.clear()
    s, d = phase1(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5")
    etok = d.get("confirm_token", "")
    s, body = phase2(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5", confirm_token=etok)
    ho = (body or {}).get("handoff", {})
    check("onchain ETH handoff is EIP-681 with chainid+wei", ho.get("scheme") == "eip681"
          and ho.get("uri") == f"ethereum:{ETH_TO}@8453?value=500000000000000000")

    # CONFIRM-INTEGRITY centerpiece — confirm address A, submit address B with A's token → REJECTED
    x4._OC_LEDGER.clear()
    s, d = phase1(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5")
    atok = d.get("confirm_token", "")
    s, _ = phase2(rail="onchain", asset="ETH", network="base", destination=ETH_EVIL, amount="0.5", confirm_token=atok)
    check("onchain substituted address (A-token, B-address) → 422", s == 422)
    s, _ = phase2(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="5.0", confirm_token=atok)
    check("onchain inflated amount (A-token) → 422", s == 422)
    s, _ = phase2(rail="onchain", asset="ETH", network="base", destination=ETH_TO, amount="0.5", confirm_token="")
    check("onchain missing confirm_token → 422", s == 422)

    # token transfers can't be built as a direct on-chain deep-link (no contract) → refused, not faked
    x4._OC_LEDGER.clear()
    s, d = phase1(rail="onchain", asset="USDC", network="base", destination=ETH_TO, amount="10")
    ttok = d.get("confirm_token", "")
    s, _ = phase2(rail="onchain", asset="USDC", network="base", destination=ETH_TO, amount="10", confirm_token=ttok)
    check("onchain token (no contract) refused, not fabricated", s == 422)

    # non-human phase-1 → 403 (blocked before anything resolves)
    try:
        wp.web_pay_onchain(R(rail="onchain", asset="BTC", network="bitcoin", destination=BTC, amount="0.001"),
                           x_vokter_human_session="nope")
        check("non-human → 403", False)
    except HTTPException as e:
        check("non-human → 403", e.status_code == 403)

    # count throttle refuses past the per-destination ceiling
    x4._OC_LEDGER.clear()
    os.environ["VOKTER_PAY_OPAQUE_MAX_DEST"] = "2"
    for _ in range(2):
        x4.record_opaque(BTC)
    s, _ = phase1(rail="onchain", asset="BTC", network="bitcoin", destination=BTC, amount="0.001")
    check("count throttle refuses past the per-destination ceiling → 403", s == 403)
    os.environ.pop("VOKTER_PAY_OPAQUE_MAX_DEST", None)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
    print("✅ web.pay opaque-crypto slice: x402 + on-chain hand-off, caveat enforced, substitution rejected")


if __name__ == "__main__":
    main()
