#!/bin/bash
# Delivery Hub — Full install script for a fresh Mac
# Run with: bash install.sh
set -e

echo ""
echo "=== Delivery Hub — Fresh Mac Install ==="
echo ""

# --- 1. Homebrew ---
if ! command -v brew &>/dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    if [ -f /opt/homebrew/bin/brew ]; then
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    echo "[OK] Homebrew installed"
else
    echo "[OK] Homebrew already installed"
fi

# --- 2. Python + Git + GitHub CLI ---
echo "Installing Python, Git, and GitHub CLI..."
brew install python@3.13 git gh 2>/dev/null || true
echo "[OK] Python, Git, and gh installed"

# --- 3. GitHub auth ---
if ! gh auth status &>/dev/null; then
    echo ""
    echo "Authenticating with GitHub..."
    echo "A browser window will open — sign in and authorize."
    echo ""
    gh auth login -p https -w
    echo "[OK] GitHub authenticated"
else
    echo "[OK] GitHub already authenticated"
fi

# --- 4. Clone repos ---
mkdir -p ~/rbd-apps
cd ~/rbd-apps
if [ ! -d "delivery-automation" ]; then
    echo "Cloning Delivery Hub..."
    gh repo clone richard-mountstephens/delivery-automation
    echo "[OK] delivery-automation cloned"
else
    echo "[OK] delivery-automation/ already exists — pulling latest"
    cd delivery-automation && git pull && cd ~/rbd-apps
fi

# --- 5. Run project setup ---
cd ~/rbd-apps/delivery-automation
./setup.sh

# --- 6. Create desktop launcher ---
echo "Creating desktop launcher..."
mkdir -p ~/Desktop/DeliveryHub.app/Contents/MacOS
LAUNCHER_PATH=~/Desktop/DeliveryHub.app/Contents/MacOS/DeliveryHub
echo '#!/bin/bash' > "$LAUNCHER_PATH"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$LAUNCHER_PATH"
echo 'source ~/.zshrc' >> "$LAUNCHER_PATH"
echo 'cd ~/rbd-apps/delivery-automation' >> "$LAUNCHER_PATH"
echo 'venv/bin/python -m src.web.app &' >> "$LAUNCHER_PATH"
echo 'sleep 2' >> "$LAUNCHER_PATH"
echo 'open http://127.0.0.1:8001' >> "$LAUNCHER_PATH"
chmod +x "$LAUNCHER_PATH"
echo "[OK] DeliveryHub app created on Desktop"

echo ""
echo "========================================="
echo "  Delivery Hub is ready!"
echo "========================================="
echo ""
echo "Double-click DeliveryHub on the Desktop to launch."
echo ""
