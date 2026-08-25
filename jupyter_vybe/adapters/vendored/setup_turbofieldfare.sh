#!/usr/bin/env bash
# Helper script to clone, build, and prepare TurboFieldfare on Apple Silicon MacBooks.
#
# Usage:
#   ./setup_turbofieldfare.sh

set -euo pipefail

INSTALL_DIR="${TURBOFIELDFARE_INSTALL_DIR:-$HOME/.turbofieldfare}"
REPO_URL="https://github.com/drumih/turbo-fieldfare.git"

echo "=== TurboFieldfare Setup Helper ==="
echo "Target directory: $INSTALL_DIR"

if ! command -v swift >/dev/null 2>&1; then
    echo "error: Swift toolchain not found. Please install Xcode or Xcode Command Line Tools (`xcode-select --install`)." >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

if [[ ! -d "turbo-fieldfare" ]]; then
    echo "Cloning TurboFieldfare repository..."
    git clone "$REPO_URL" turbo-fieldfare
else
    echo "TurboFieldfare repository already present at $INSTALL_DIR/turbo-fieldfare."
fi

cd turbo-fieldfare

echo "Building release binaries using Swift..."
swift build -c release

CLI_BIN="$(pwd)/.build/release/TurboFieldfareCLI"
SERVER_BIN="$(pwd)/.build/release/TurboFieldfareServer"

echo ""
echo "=== Build Complete ==="
echo "CLI binary:    $CLI_BIN"
echo "Server binary: $SERVER_BIN"
echo ""
echo "Next step: Download and repack the Gemma 4 26B-A4B model (~15 GB download)."
echo "Run TurboFieldfareRepack to fetch and create your .gturbo model file:"
echo "   $INSTALL_DIR/turbo-fieldfare/.build/release/TurboFieldfareRepack --output scratch/gemma4.gturbo --overwrite"
echo ""
echo "Then test with:"
echo "   ./run_turbofieldfare.sh \"Write a python function to compute fibonacci numbers.\""
