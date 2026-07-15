#!/usr/bin/env bash
# Setup script for Caldova Waypoint
# Run: ./setup.sh

set -e

echo "🚀 Setting up Caldova Waypoint..."

# Check prerequisites
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "❌ $1 is not installed. Please install it first."
        echo "   $2"
        exit 1
    fi
    echo "✅ $1 found"
}

echo ""
echo "Checking prerequisites..."
check_command "dotnet" "Install from: https://dotnet.microsoft.com/download"
check_command "python" "Install from: https://www.python.org/"
check_command "node" "Install from: https://nodejs.org/"
check_command "uv" "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"

# Check for Aspire CLI
if ! command -v aspire &> /dev/null; then
    echo ""
    echo "📦 Installing Aspire CLI..."
    curl -sSL https://aspire.dev/install.sh | bash
    export PATH="$HOME/.aspire/bin:$PATH"
fi
echo "✅ Aspire CLI found"

# Install pre-commit hooks
echo ""
echo "🔧 Setting up pre-commit hooks..."
uv tool install pre-commit 2>/dev/null || true
pre-commit install

echo ""
echo "✅ Setup complete!"
echo ""
echo "To start the application, run:"
echo "  aspire run"
echo ""
echo "Access points:"
echo "  • Web App:         http://localhost:5173"
echo "  • API Docs:        http://localhost:8000/docs"
echo "  • Aspire Dashboard: http://localhost:15888"
