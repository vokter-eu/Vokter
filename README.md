# Vokter

**Your agent. Your data. Your money. By right.**

Vokter (Norwegian: *guardian*) is a personal, sovereign AI agent that runs on **your** machine. No third-party cloud, no accounts, no telemetry. It only knows what you teach it, and you can audit every line of code that makes it work.

> There's a right older than the internet: what is yours cannot be taken. Norwegians call it *odel*. Vokter is its digital guardian.
> — [Read the full manifesto](docs/MANIFESTO.md)

## Project status

🚧 **Phase 0 — Skeleton.** This is day one. If you found your way here, you're one of the first. Star the repo and check back soon, or better yet: [contribute](CONTRIBUTING.md).

## Roadmap

- [ ] **Phase 1 — Your agent on your machine**: local LLM (Ollama), encrypted personal memory, chat with your documents. 100% offline.
- [ ] **Phase 2 — Your agent goes out into the world**: web browsing with granular permissions, real task planning (travel, shopping, errands), and **100% local voice** (hearing via Whisper, speech via Piper — talk to Vokter without your voice leaving home). It proposes; you decide.
- [ ] **Phase 3 — Your agent pays**: non-custodial wallet with a **modular, asset-agnostic architecture** — by default, MiCA-regulated stablecoins (authorized EMTs); any other asset (BTC, ETH…) as an optional pluggable adapter, without touching the core. Human confirmation and spending limits always.

## Quick start (v0.1)

Requirements: Docker and Docker Compose. Recommended: 16 GB RAM (with 8 GB, switch the model to `llama3.2:3b` in the compose file).

```bash
git clone https://github.com/vokter-eu/Vokter.git
cd Vokter/docker
docker compose up -d --build
docker exec -it vokter-ollama ollama pull llama3.1:8b
docker exec -it vokter-ollama ollama pull nomic-embed-text
```

Open **http://localhost:8080**: upload a PDF and ask it anything. Not a single byte has left your machine — check for yourself: that's the whole point.

What v0.1 already does: ingest PDF/TXT/MD, local memory in SQLite, answers grounded only in your documents with source citations, a "what Vokter knows" panel, and real deletion (document + embeddings). Honestly pending for v0.2: at-rest encryption of the database and an email connector.

## Non-negotiable principles

1. **Local first.** By default, everything is processed on your hardware.
2. **Zero hidden calls.** No requests to third-party AI APIs. Verified in CI.
3. **Your keys, your money.** When payments arrive: non-custodial or nothing.
4. **Real deletion.** Delete means delete, embeddings included.
5. **Open source.** We don't ask for trust; we give proof.
6. **For your life, not to retain you.** Vokter knows your world to give you back time and push you toward your real life — it will never use mechanics of attachment, loneliness, or engagement.

## License

AGPL-3.0 — free forever, and improvements flow back to the community.

## Community

- Web: [vokter.eu](https://vokter.eu) *(coming soon)*
- Discussions: the Discussions tab of this repository

---

*Vokter is an independent European project. Not affiliated with any big tech, and that's exactly the point.*
