#!/bin/bash
# ============================================================
#  Vokter Installer — macOS
#  Double-click this file to install Vokter on your Mac.
#  First time: right-click → Open if macOS asks for permission.
# ============================================================

# Open in a visible Terminal window with a title
printf '\033]0;Vokter Installer\007'
clear

echo "┌─────────────────────────────────────────────────────┐"
echo "│          🛡️  Vokter — Personal AI Agent             │"
echo "│              Installer for macOS                    │"
echo "└─────────────────────────────────────────────────────┘"
echo ""

# ── Step 1: Check Docker ─────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "⚠️  Docker Desktop is not installed."
    echo ""
    echo "   Docker is a free app that lets Vokter run safely"
    echo "   on your Mac. Opening the download page now..."
    echo ""
    open "https://www.docker.com/products/docker-desktop/"
    echo "   Install Docker Desktop, then run this file again."
    echo ""
    read -rp "   Press Enter to close..."
    exit 0
fi

# ── Step 2: Start Docker if not running ──────────────────────
if ! docker info &>/dev/null 2>&1; then
    echo "⏳ Docker is installed but not running. Starting it..."
    open -a Docker 2>/dev/null || true
    echo "   Waiting for Docker to start (up to 60 seconds)..."
    WAITED=0
    while ! docker info &>/dev/null 2>&1; do
        if [ "$WAITED" -ge 60 ]; then
            echo ""
            echo "❌ Docker didn't start in time."
            echo "   Please open Docker Desktop from your Applications"
            echo "   folder and then run this file again."
            read -rp "   Press Enter to close..."
            exit 1
        fi
        sleep 2
        WAITED=$((WAITED + 2))
        printf "."
    done
    echo ""
fi

echo "✅ Docker is running"
echo ""

# ── Step 3: Create Vokter folder ─────────────────────────────
VOKTER_DIR="$HOME/Vokter"
mkdir -p "$VOKTER_DIR"
cd "$VOKTER_DIR"
echo "📁 Vokter folder: $VOKTER_DIR"

# ── Step 4: Download configuration ───────────────────────────
echo "⬇️  Downloading Vokter..."
if ! curl -fsSL \
    "https://raw.githubusercontent.com/vokter-eu/Vokter/main/docker-compose.yml" \
    -o docker-compose.yml; then
    echo "❌ Could not download Vokter. Check your internet connection."
    read -rp "Press Enter to close..."
    exit 1
fi

# ── Step 5: Generate encrypted config ────────────────────────
if [ ! -f .env ]; then
    curl -fsSL \
        "https://raw.githubusercontent.com/vokter-eu/Vokter/main/.env.example" \
        -o .env
    DB_KEY=$(openssl rand -hex 32)
    sed -i '' "s/^VOKTER_DB_KEY=.*/VOKTER_DB_KEY=$DB_KEY/" .env
    echo "🔑 Encryption key generated — your data is protected"
else
    echo "🔑 Existing configuration found — keeping it"
fi

# ── Step 6: Start Vokter ─────────────────────────────────────
echo ""
echo "🚀 Starting Vokter..."
docker compose up -d

# ── Step 7: Download AI models ───────────────────────────────
echo ""
echo "🤖 Downloading AI model — llama3.2:3b (~2 GB)"
echo "   This takes 5-10 minutes the first time."
echo "   Please wait, do not close this window."
echo ""
docker exec vokter-ollama ollama pull llama3.2:3b
docker exec vokter-ollama ollama pull nomic-embed-text

# ── Step 8: Open browser ─────────────────────────────────────
echo ""
echo "┌─────────────────────────────────────────────────────┐"
echo "│   ✅  Vokter is ready!                              │"
echo "│                                                     │"
echo "│   Opening http://localhost:8080 in your browser...  │"
echo "│                                                     │"
echo "│   Next time: Vokter starts automatically with       │"
echo "│   Docker Desktop — no need to run this again.       │"
echo "└─────────────────────────────────────────────────────┘"
echo ""
sleep 2
open "http://localhost:8080"
read -rp "Press Enter to close this window..."
