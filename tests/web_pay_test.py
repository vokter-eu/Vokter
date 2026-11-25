"""Tier-3 Phase 2 — web.pay LNURL slice: unit guarantees (NO network, NO real funds).

Validates the pieces confirm-integrity rests on:
  * the BOLT11 decoder (amount + description_hash) against bech32-encoded test invoices (round-trip),
  * Lightning Address parsing, LNURL-pay resolution with a MOCK http seam,
  * CONFIRM-INTEGRITY: a tampered amount or a tampered metadata-hash is REJECTED (ValueError),
  * the gateway ceilings (per-tx / per-window / per-payee).

Run:  uv run --python 3.12 --with fastapi --with pydantic python tests/web_pay_test.py
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import web_pay as wp
from web_pay import _B32

FAILS = []
def check(desc, cond):
    print(("PASS " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)


# ── minimal bech32 ENCODER (test-only, to build invoices the real decoder reads) ──
def _polymod(values):
    GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25; chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk
def _hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]
def _checksum(hrp, data):
    pm = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ 1
    return [(pm >> 5 * (5 - i)) & 31 for i in range(6)]
def _bech32_encode(hrp, data):
    return hrp + "1" + "".join(_B32[d] for d in data + _checksum(hrp, data))
def _bytes_to_5bit(b):
    acc = bits = 0; out = []
    for x in b:
        acc = (acc << 8) | x; bits += 8
        while bits >= 5:
            bits -= 5; out.append((acc >> bits) & 31)
    if bits:
        out.append((acc << (5 - bits)) & 31)
    return out
def _field(tag, value5):
    ln = len(value5)
    return [tag, ln // 32, ln % 32] + value5
def mk_invoice(hrp, desc_hash: bytes, pay_hash: bytes = b"\x11" * 32):
    """Build a bech32 BOLT11-shaped string. Real BOLT11 invoices always end in a 520-bit (104-group)
    signature, and decode_bolt11 strips those trailing groups before walking the tagged fields — so the
    mock must carry the padding too (same as the selftest's builder) or the strip eats the fields."""
    data = [0] * 7 + _field(1, _bytes_to_5bit(pay_hash)) + _field(23, _bytes_to_5bit(desc_hash)) + [0] * 104
    return _bech32_encode(hrp, data)


def main():
    # 1) HRP amount decode
    check("lnbc2500u → 250_000_000 msat", wp._hrp_amount_msat("lnbc2500u") == 250_000_000)
    check("lnbc10u → 1_000_000 msat",     wp._hrp_amount_msat("lnbc10u") == 1_000_000)
    check("lnbc1m → 100_000_000 msat",    wp._hrp_amount_msat("lnbc1m") == 100_000_000)
    check("lnbc (amountless) → None",     wp._hrp_amount_msat("lnbc") is None)

    # 2) full decode round-trip (amount + description_hash)
    meta = '[["text/plain","Pay alice"]]'
    dh = hashlib.sha256(meta.encode()).digest()
    inv = mk_invoice("lnbc10u", dh)
    dec = wp.decode_bolt11(inv)
    check("decode amount_msat", dec["amount_msat"] == 1_000_000)
    check("decode description_hash matches sha256(metadata)", dec["description_hash"] == dh.hex())

    # 3) Lightning Address parsing
    check("parse alice@getalby.com", wp.parse_lightning_address("alice@getalby.com") == ("alice", "getalby.com"))
    check("reject bare domain",      wp.parse_lightning_address("getalby.com") is None)
    check("reject empty",            wp.parse_lightning_address("") is None)

    # 4) resolve_and_invoice + CONFIRM-INTEGRITY (mock http seam)
    CALLBACK = "https://getalby.com/lnurlp/alice/callback"
    def mock_http(amount_msat=1_000_000, desc_bytes=dh, metadata=meta):
        def _get(url):
            if url.endswith("/.well-known/lnurlp/alice"):
                return {"tag": "payRequest", "callback": CALLBACK, "metadata": metadata,
                        "minSendable": 1000, "maxSendable": 100_000_000}
            if url.startswith(CALLBACK):
                return {"pr": mk_invoice("lnbc10u" if amount_msat == 1_000_000 else "lnbc2500u", desc_bytes)}
            raise AssertionError("unexpected url " + url)
        return _get

    wp._http_get_json = mock_http()
    res = wp.resolve_and_invoice("alice@getalby.com", 1000)      # 1000 sats = 1_000_000 msat
    check("happy path returns bolt11", bool(res.get("bolt11")) and res["domain"] == "getalby.com")
    check("happy path amount", res["amount_sats"] == 1000)

    # tampered AMOUNT: callback returns an invoice for 2500u (250_000_000 msat) ≠ confirmed 1_000_000
    wp._http_get_json = mock_http(amount_msat=250_000_000)
    try:
        wp.resolve_and_invoice("alice@getalby.com", 1000)
        check("inflated amount REJECTED", False)
    except ValueError as e:
        check("inflated amount REJECTED", "amount" in str(e).lower())

    # tampered METADATA binding: invoice desc_hash does not match sha256(metadata) (LUD-06)
    wp._http_get_json = mock_http(desc_bytes=hashlib.sha256(b"attacker").digest())
    try:
        wp.resolve_and_invoice("alice@getalby.com", 1000)
        check("substituted invoice (metadata mismatch) REJECTED", False)
    except ValueError as e:
        check("substituted invoice (metadata mismatch) REJECTED", "'h' mismatch" in str(e).lower())

    # 5) gateway ceilings
    wp._LEDGER.clear()
    check("per-tx ceiling blocks a huge amount", wp.check_ceilings("getalby.com", wp._max_tx() + 1) is not None)
    check("normal amount allowed", wp.check_ceilings("getalby.com", 1000) is None)
    # fill the per-payee ceiling and confirm the next is refused
    wp._LEDGER.clear()
    wp._ledger_record("getalby.com", wp._max_payee())
    check("per-payee ceiling blocks the next", wp.check_ceilings("getalby.com", 1) is not None)
    check("a different payee still allowed", wp.check_ceilings("other.com", 1000) is None)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAILED: {FAILS}"); sys.exit(1)
    print("✅ web.pay LNURL slice: bolt11 decode + integrity-reject + ceilings hold")


if __name__ == "__main__":
    main()
