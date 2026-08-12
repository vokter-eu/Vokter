# Contributing to Vokter

Thanks for being here. Vokter is built by people, not corporations.

## How to help

- **Try the quick start** from the README on your machine and open an issue with whatever fails or confuses you. Every operating system and every GB of RAM counts.
- **Report bugs**: open an issue with your OS, RAM, Docker version, and the exact error. Clear reproduction steps help a lot.
- **Discuss the architecture**: the roadmap lives in Discussions. Well-grounded technical opinions are worth gold — especially around Phase 6 (MCP, Nostr).
- **Translate**: the project's home language is English. Other languages are welcome, especially for the manifesto.
- **Spread the word**: a star or a message to someone who cares about privacy helps more than you'd think.

## Ground rules

1. Be kind. People of all levels belong here.
2. All code contributions are published under AGPL-3.0.
3. **Non-negotiable red line**: no PR that sends user data to third-party services without explicit consent will be accepted, no matter how useful.
4. Keep the principles in `docs/ARCHITECTURE.md` in mind. When in doubt, local first wins.

## Process

Issues for bugs and concrete proposals; Discussions for open ideas; small, focused PRs over giant ones. The maintainer responds in English and Spanish.

## Development setup

```bash
git clone https://github.com/vokter-eu/Vokter.git
cd Vokter
cp .env.example .env          # set VOKTER_DB_KEY
docker compose up -d --build
docker exec -it vokter-ollama ollama pull llama3.2:3b
docker exec -it vokter-ollama ollama pull nomic-embed-text
```

The app reloads automatically when you edit files in `app/` if you add `--reload` to the uvicorn command in `app/Dockerfile`.
