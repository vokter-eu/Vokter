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

# SUDO becomes "sudo" only when this session needs root to reach Docker —
# right after a fresh install the 'docker' group isn't active until the user
# logs out and back in, so every docker call below must honour it.
SUDO=""

# ── Step 1: Check Docker — offer to install it if it's missing ──
if ! command -v docker &>/dev/null; then
    echo "⚠️  Docker isn't installed. Vokter needs it to run safely on your machine."
    echo ""
    echo "   I can install it now using Docker's official script"
    echo "   (https://get.docker.com). This will ask for your administrator password."
    echo ""
    read -rp "   Install Docker now? [Y/n]: " INSTALL_DOCKER || true
    case "$INSTALL_DOCKER" in
        [Nn]*)
            echo ""
            echo "   No problem. To install Docker yourself, run:"
            echo "     curl -fsSL https://get.docker.com | sh"
            echo "   then start this installer again."
            exit 0
            ;;
    esac

    echo ""
    echo "⬇️  Downloading Docker's official install script..."
    if ! curl -fsSL https://get.docker.com -o /tmp/vokter-get-docker.sh; then
        echo "❌ Could not download the Docker installer. Check your internet connection."
        exit 1
    fi
    echo "🔧 Installing Docker (you may be asked for your password)..."
    if ! sudo sh /tmp/vokter-get-docker.sh; then
        rm -f /tmp/vokter-get-docker.sh
        echo "❌ Docker installation failed."
        echo "   See https://docs.docker.com/engine/install/ for manual steps."
        exit 1
    fi
    rm -f /tmp/vokter-get-docker.sh
    # Add the user to the docker group for FUTURE sessions; it only takes
    # effect after they log out and back in, so this run keeps using sudo.
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    SUDO="sudo"
    echo "✅ Docker installed."
    echo ""
fi

# ── Step 2: Make sure the Docker daemon is reachable ─────────
if ! $SUDO docker info &>/dev/null 2>&1; then
    echo "⏳ Starting the Docker service..."
    sudo systemctl start docker &>/dev/null || sudo service docker start &>/dev/null || true
    sleep 3
fi
# If it works only with sudo, the user isn't in the docker group yet —
# fall back to sudo for the rest of this run.
if [ -z "$SUDO" ] && ! docker info &>/dev/null 2>&1 && sudo docker info &>/dev/null 2>&1; then
    # Daemon reachable only as root → the user isn't in the docker group. Add
    # them (effective next login) so the "no more sudo" advice below is true.
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    SUDO="sudo"
fi
if ! $SUDO docker info &>/dev/null 2>&1; then
    echo "❌ Docker is installed but couldn't be started."
    echo "   Try: sudo systemctl start docker   — then run this installer again."
    exit 1
fi

# ── Step 3: Check Docker Compose ─────────────────────────────
if $SUDO docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    echo "⚠️  Docker Compose is not installed."
    echo "   Install with: sudo apt install docker-compose-plugin"
    echo "   Or: https://docs.docker.com/compose/install/"
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

# ── Step 6: Config + model choice ────────────────────────────
if [ ! -f .env ]; then
    curl -fsSL \
        "https://raw.githubusercontent.com/vokter-eu/Vokter/main/.env.example" \
        -o .env
    DB_KEY=$(openssl rand -hex 32)
    sed -i "s/^VOKTER_DB_KEY=.*/VOKTER_DB_KEY=$DB_KEY/" .env
    echo "🔑 Encryption key generated — your data is protected"
    echo ""

    echo "🤖 Choose your AI model:"
    echo ""
    echo "   [1] Compact  — llama3.2:1b  (~800 MB)"
    echo "       Fast download. Good for questions and summaries."
    echo "       Best if your machine has 8 GB RAM."
    echo ""
    echo "   [2] Standard — llama3.2:3b  (~2 GB)  ← recommended"
    echo "       Better quality answers. Works well on 8 GB+ RAM."
    echo ""
    read -rp "   Your choice (1 or 2, or just press Enter for Standard): " MODEL_CHOICE
    case "$MODEL_CHOICE" in
        1) CHAT_MODEL="llama3.2:1b" ; MODEL_SIZE="~800 MB" ;;
        *) CHAT_MODEL="llama3.2:3b" ; MODEL_SIZE="~2 GB"   ;;
    esac
    sed -i "s/^VOKTER_CHAT_MODEL=.*/VOKTER_CHAT_MODEL=$CHAT_MODEL/" .env
    echo ""
    echo "   ✅ Model selected: $CHAT_MODEL ($MODEL_SIZE)"
else
    CHAT_MODEL=$(grep "^VOKTER_CHAT_MODEL=" .env | cut -d= -f2 | tr -d '[:space:]')
    CHAT_MODEL="${CHAT_MODEL:-llama3.2:3b}"
    MODEL_SIZE="already downloaded"
    echo "🔑 Existing configuration found — keeping it"
    echo "   Model: $CHAT_MODEL"
fi

# ── Step 7: Start Vokter ─────────────────────────────────────
echo ""
echo "🚀 Starting Vokter..."
$SUDO $COMPOSE up -d

# ── Step 8: Wait for Ollama to be ready ──────────────────────
echo "⏳ Waiting for Ollama to start..."
for i in $(seq 1 15); do
    if $SUDO docker exec vokter-ollama ollama list &>/dev/null 2>&1; then
        break
    fi
    sleep 3
done

# ── Step 9: Download AI models ───────────────────────────────
echo ""
echo "🤖 Downloading $CHAT_MODEL ($MODEL_SIZE)..."
echo "   Please wait, do not close this window."
echo ""
$SUDO docker exec vokter-ollama ollama pull "$CHAT_MODEL"
$SUDO docker exec vokter-ollama ollama pull nomic-embed-text

# ── Step 10: Open browser ─────────────────────────────────────
echo ""
echo "┌─────────────────────────────────────────────────────┐"
echo "│   ✅  Vokter is ready!                              │"
echo "│                                                     │"
echo "│   Open your browser and go to:                      │"
echo "│       http://localhost:8080                         │"
echo "│                                                     │"
echo "│   Next time: cd ~/Vokter && docker compose up -d   │"
echo "└─────────────────────────────────────────────────────┘"
echo ""
if [ -n "$SUDO" ]; then
    echo "ℹ️  Log out and back in once so Docker works without 'sudo'."
    echo "   Until then, start Vokter with: sudo docker compose up -d"
    echo ""
fi
xdg-open "http://localhost:8080" &>/dev/null || \
    sensible-browser "http://localhost:8080" &>/dev/null || \
    echo "Open http://localhost:8080 in your browser."
