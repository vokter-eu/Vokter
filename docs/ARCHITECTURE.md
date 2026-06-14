# Vokter — Architecture

This document defines the technical architecture across all phases. Every feature we build must be consistent with what's written here. When in doubt, this document wins.

---

## Core principle

Vokter is a local-first sovereign agent. The user's machine is the trust boundary. Nothing crosses that boundary without explicit user permission. This is not a UX choice — it is the architectural invariant that everything else derives from.

---

## Phase map

| Phase | Name | What it adds |
|-------|------|-------------|
| 0 | Skeleton | Repo, manifesto, Docker scaffold |
| 1 | Local guardian | Document ingestion, local RAG, conversation memory, encrypted SQLite |
| 2 | Agent goes out | Web browsing, task planning, local voice (Whisper + Piper) |
| 3 | Agent pays | Non-custodial wallet, MiCA stablecoins default, pluggable asset adapters |

---

## Identity architecture

This is the most critical design decision in the project. Get it wrong here and every phase that follows is compromised.

### The problem

A persistent public key (e.g. a Nostr npub) is a **correlation vector**. If Vokter uses the same identity across external services, those services — individually or by sharing data — can reconstruct a detailed picture of the user's life: where they travel, what they buy, who they talk to. This defeats the privacy mission entirely.

### The solution: three-layer identity

```
┌─────────────────────────────────────────────────┐
│  Layer 1 — Master Key                           │
│  Never leaves the local machine.                │
│  Stored in encrypted SQLite (VOKTER_DB_KEY).    │
│  Used only to derive session keys.              │
└────────────────────┬────────────────────────────┘
                     │ derives (unlinkable)
                     ▼
┌─────────────────────────────────────────────────┐
│  Layer 2 — Ephemeral Session Keys               │
│  One fresh keypair per external interaction.    │
│  Airline sees key A. Hotel sees key B.          │
│  Theatre sees key C.                            │
│  A, B, C are mathematically unlinkable.         │
│  Derived from: HMAC(master_secret, nonce)       │
│  Nonces are random and stored locally.          │
└────────────────────┬────────────────────────────┘
                     │ pays via
                     ▼
┌─────────────────────────────────────────────────┐
│  Layer 3 — Blind Payment Tokens (Phase 3)       │
│  Cashu e-cash layer on top of any asset.        │
│  Blind signatures: even the mint cannot link    │
│  withdrawal to spending.                        │
│  Payment rail reveals no identity.              │
└─────────────────────────────────────────────────┘
```

**Rule**: the master key is never sent to any external service, ever. External services only ever see ephemeral session keys that are rotated per interaction.

### Future: capability proofs (Phase 3+)

When Vokter needs to prove something to a service — "I have already paid", "I am authorized", "I am over 18" — without linking to any persistent identity, we use zero-knowledge proofs. Technologies to evaluate: Semaphore, BBS+ signatures. This is a Phase 3+ concern; do not implement early.

---

## Payment architecture

### Design principle

Vokter has no opinion on what money is legitimate. The user does. Vokter's only non-negotiable rule is **non-custodial always**: whatever asset the user chooses, their keys never leave their machine.

### Default (ships out of the box)

MiCA-regulated euro-denominated stablecoins:
- **EURC** (Circle)
- **EURe** (Monerium)
- **EURCV** (Société Générale)

These work in Europe without legal friction. No configuration needed.

### Pluggable adapters (user installs, user's responsibility)

Any other asset — BTC, ETH, Lightning, or others — is available as an optional adapter. The user chooses to install it. Vokter core ships no opinion about these assets and provides no defaults for them. Legal responsibility for the use of any non-default adapter rests with the user, as with any open source tool.

### Payment privacy layer

Cashu (Chaumian e-cash with blind signatures) sits on top of any asset. The mint cannot link a withdrawal to a spending event. This applies equally to the default stablecoins and any pluggable adapter. Privacy is asset-agnostic.

### Human confirmation always

Every payment requires explicit user confirmation. Spending limits are enforced locally. No payment is ever fully autonomous.

---

## Module structure (target for Phase 2+)

The current flat `app/main.py` is acceptable for Phase 1. Before Phase 2 work begins, split into:

```
app/
  main.py          — FastAPI app init and route registration only
  config.py        — All env vars and constants
  db.py            — SQLCipher connection and schema
  ingestion.py     — Document parsing, chunking, embedding storage
  rag.py           — Retrieval and context assembly
  chat.py          — Conversation memory and Ollama interaction
  identity.py      — Master key, session key derivation (Phase 2)
  wallet/
    __init__.py    — Wallet interface (abstract)
    cashu.py       — Cashu e-cash layer
    adapters/      — One file per asset adapter
  voice/
    whisper.py     — Speech to text (Phase 2)
    piper.py       — Text to speech (Phase 2)
```

---

## What Vokter will never do

These are architectural constraints, not policy choices. Violating them would require a fork.

1. Send any request to a third-party AI API (all inference is local via Ollama)
2. Use a persistent public key as an external-facing identity
3. Hold custody of user funds
4. Make any payment without explicit user confirmation
5. Use engagement mechanics, retention design, or exploit loneliness
6. Delete a document without also deleting its embeddings

---

## Housekeeping (known issues to fix)

- `docker-compose.yml` at repo root is outdated — canonical file is `docker/docker-compose.yml`
- `main.py` and `requirements.txt` at repo root are dead files from initial upload — delete them
- `index.html` at repo root is a duplicate — canonical file is `app/static/index.html`
