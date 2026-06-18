#!/bin/bash
# ============================================================
#  Vokter Installer — Linux
#  Run with: bash vokter-linux.sh
#  Or: chmod +x vokter-linux.sh && ./vokter-linux.sh
# ============================================================

set -e
clear

echo "┌─────────────────────────────────────────────────────┐"
echo "│          🛡️  Vokter — Personal AI Agent             │"
echo "│              Installer for Linux                    │"
echo "└─────────────────────────────────────────────────────┘"
echo ""

# ── Step 1: Check Docker ─────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "⚠️  Docker is not installed."
    echo ""
    echo "   Install Docker on Ubuntu / Debian:"
    echo "   curl -fsSL https://get.docker.com | bash"
    echo "   sudo usermod -aG docker \$USER  (then log out and in)"
    echo ""
    echo "   Other distributions: https://docs.docker.com/engine/install/"
    echo ""
    exit 1
fi

# ── Step 2: Check Docker Compose ─────────────────────────────
if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    echo "⚠️  Docker Compose is not installed."
    echo "   Install with: sudo apt install docker-compose-plugin"
    echo "   Or: https://docs.docker.com/compose/install/"
    exit 1
fi

# ── Step 3: Check Docker is running ──────────────────────────
if ! docker info &>/dev/null 2>&1; then
    echo "⚠️  Docker is not running. Start it with:"
    echo "   sudo systemctl start docker"
    echo "   (or launch Docker Desktop if you installed it)"
    exit 1
fi

echo "✅ Docker is running"
echo ""

# ── Step 4: Create Vokter folder ─────────────────────────────
VOKTER_DIR="$HOME/Vokter"
mkdir -p "$VOKTER_DIR"
cd "$VOKTER_DIR"
echo "📁 Vokter folder: $VOKTER_DIR"

# ── Step 5: Download configuration ───────────────────────────
echo "⬇️  Downloading Vokter..."
if ! curl -fsSL \
    "https://raw.githubusercontent.com/vokter-eu/Vokter/main/docker-compose.yml" \
    -o docker-compose.yml; then
    echo "❌ Could not download Vokter. Check your internet connection."
    exit 1
fi

# ── Step 6: Generate encrypted config ────────────────────────
if [ ! -f .env ]; then
    curl -fsSL \
        "https://raw.githubusercontent.com/vokter-eu/Vokter/main/.env.example" \
        -o .env
    DB_KEY=$(openssl rand -hex 32)
    sed -i "s/^VOKTER_DB_KEY=.*/VOKTER_DB_KEY=$DB_KEY/" .env
    echo "🔑 Encryption key generated — your data is protected"
else
    echo "🔑 Existing configuration found — keeping it"
fi

# ── Step 7: Start Vokter ─────────────────────────────────────
echo ""
echo "🚀 Starting Vokter..."
$COMPOSE up -d

# ── Step 8: Download AI models ───────────────────────────────
echo ""
echo "🤖 Downloading AI model — llama3.2:3b (~2 GB)"
echo "   This takes 5-10 minutes the first time."
echo "   Please wait, do not close this window."
echo ""
docker exec vokter-ollama ollama pull llama3.2:3b
docker exec vokter-ollama ollama pull nomic-embed-text

# ── Step 9: Open browser ─────────────────────────────────────
echo ""
echo "┌─────────────────────────────────────────────────────┐"
echo "│   ✅  Vokter is ready!                              │"
echo "│                                                     │"
echo "│   Open your browser and go to:                      │"
echo "│       http://localhost:8080                         │"
echo "│                                                     │"
echo "│   Next time: docker compose up -d  (in ~/Vokter)   │"
echo "└─────────────────────────────────────────────────────┘"
echo ""
# Try to open browser (works on most desktop Linux)
xdg-open "http://localhost:8080" &>/dev/null || \
    sensible-browser "http://localhost:8080" &>/dev/null || \
    echo "Open http://localhost:8080 in your browser."
