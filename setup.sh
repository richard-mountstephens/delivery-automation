#!/bin/bash
# Delivery Hub — One-time setup script for macOS
set -e

echo "=== Delivery Hub Setup ==="
echo ""

# Find best Python — prefer Homebrew python3.13, then python3
if command -v python3.13 &>/dev/null; then
    PYTHON=${PYTHON:-python3.13}
elif command -v python3.12 &>/dev/null; then
    PYTHON=${PYTHON:-python3.12}
elif command -v python3.11 &>/dev/null; then
    PYTHON=${PYTHON:-python3.11}
else
    PYTHON=${PYTHON:-python3}
fi
PY_VERSION=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "ERROR: Python 3.11+ required (found $PY_VERSION)"
    echo "Install via: brew install python@3.13"
    exit 1
fi
echo "[OK] Python $PY_VERSION"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv venv
fi
source venv/bin/activate
echo "[OK] Virtual environment active"

# Install dependencies
echo "Installing dependencies..."
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt

echo "[OK] Dependencies installed"

# Data directory
mkdir -p data
echo "[OK] data/ directory ready"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  ./start.sh    # Start at http://127.0.0.1:8001"
echo ""
