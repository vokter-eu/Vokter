# Vokter — Security Audit (2026-06-19)

Full-system review, triggered after adding the agent-to-agent network surface
(NIP-17, A2A-over-HTTP, outbound client, known-agents registry).

## Threat model

A Vokter is a **high-value target** and must be assumed to be under constant,
automated attack. A single instance holds, in one place:

- **Money** — a non-custodial wallet (Cashu / Lightning / EURC / …).
- **Private data** — the human's documents and emails, embedded and queryable.
- **Autonomy** — the ability to act, browse, and (with approval) pay.

Compromise is not "an annoyance"; it is theft of funds and exfiltration of a
person's private life. Every networked surface must therefore be **fail-closed,
input-hostile by default, minimal, and rate-limitable**. "It's only on
localhost" is a deployment accident away from being false.

---

## Findings

### H1 — The port-8080 admin API has no authentication (architectural)
The FastAPI app exposes `wallet/send`, `config`, `ingestion`, `agents/talk`,
`browse`, etc. with **no auth** — it trusts the network boundary. Today
`docker-compose.yml` binds it to `127.0.0.1:8080`, so by default it is reachable
only from the host. **But the entire point of the A2A-HTTP work is to make
Vokter reachable**, and the two-device test requires LAN exposure. The moment a
user exposes 8080 (tunnel, `0.0.0.0` mapping, LAN IP), every admin endpoint —
including `wallet/send` (whose only gate is a `confirmed: true` boolean any
caller can set) — becomes reachable by attackers.

- **Status:** open (recommendation, not a one-line fix).
- **Fix:** never expose all of 8080. Expose **only** `/a2a` + `/.well-known/`
  through a reverse proxy, OR split the public agent transport into its own
  app/port. Longer term, add authentication to the sensitive routers so safety
  does not depend on the network boundary at all.

### M1 — Bearer token compared in non-constant time — FIXED
`a2a_server._is_trusted` compared the A2A bearer token with `==`, leaking it to
a timing attack. Now uses `hmac.compare_digest`.

### M2 — `browse` performed the request before validating for internal IPs — FIXED
`browser.py` only checked `_is_private_host` on the *final* URL (after the
request + redirects ran), so an allowlisted domain resolving to an internal
address (incl. DNS rebinding) was reached before being blocked (blind SSRF).
Now the initial host is validated **before** any request is made.

### M3 — `wallet/send` confirmation is a UI-trust boolean, not an authorization
`confirmed: true` is the only gate. Safe while the API is local-only and driven
by the human's UI; if H1's exposure happens, it is trivially bypassed. Tie-in to
H1 — the confirmation must become a real, attacker-resistant approval before any
exposure of the wallet route.

### L1 — Decrypted message content logged at INFO
`nostr_listener` logs the first 120 chars of decrypted DMs at INFO. Private
agent conversation content lands in logs. Lower to DEBUG or redact.

### L2 — No request-size cap on the JSON-RPC endpoint
`/a2a` reads `await request.json()` with no explicit size limit (DoS via huge
body). Low while local-only; add a cap before exposure.

### L3 — `_is_private_host` resolves only the first A record
Uses `socket.gethostbyname` (single IPv4). Multi-record / IPv6 / rebinding
edge-cases can slip the *pre-fetch* check (the post-redirect re-check still
catches most). Use `getaddrinfo` and reject if **any** resolved address is
internal.

---

## Verified good (no action)

- No `eval` / `exec` / `subprocess` / `os.system` / `pickle` / `shell=True`.
- SQL is fully parameterised (the one f-string is the unavoidable `PRAGMA key`,
  with quote-escaping, on an operator-set value).
- DB encrypted at rest (SQLCipher) when `VOKTER_DB_KEY` is set; no plaintext
  fallback in the documented Docker path.
- Wallet: explicit `confirmed` gate + daily spend limit enforced under an
  `asyncio.Lock` (race-safe); `wallet_send` is **not** reachable through the
  agent dispatch — agents can read balance, never spend.
- Agent trust floor is fail-closed: an unauthenticated caller reaches only the
  public `introduce` card and triggers **no** backend call (tested).
- Outbound `agent_client` has an SSRF gate (allows LAN, blocks loopback /
  link-local / metadata / multicast / reserved).
- NIP-17: allowlist is checked against the cryptographically authenticated
  `unwrapped.sender()`, not the throwaway wrap key.
- Identity keys are derived from a master key that is never exported or sent.

---

## Hardening roadmap (for "constant attack")

1. **Isolate the public surface** (H1): reverse proxy → only `/a2a` +
   `/.well-known`. Treat all `/api/*` as private.
2. **Authentication** on sensitive routers (wallet, config, agents, ingestion)
   so safety is not a function of the network boundary.
3. **Rate limiting** per peer / per IP on every networked endpoint — the first
   defence against automated probing and the economic spam vector.
4. **Real payment approval** (M3): out-of-band human confirmation, not a boolean.
5. **Pay-to-contact** for unknown agents (ties into the reputation layer and
   AIRadar's L402 model): make spamming Vokter cost money.
6. Logging hygiene (L1), request caps (L2), stricter DNS resolution (L3).
