#!/usr/bin/env bash
# ============================================================
# Project Tanishi — Quick Setup
# ============================================================

set -e

echo ""
echo "  ████████╗ █████╗ ███╗   ██╗██╗███████╗██╗  ██╗██╗"
echo "  ╚══██╔══╝██╔══██╗████╗  ██║██║██╔════╝██║  ██║██║"
echo "     ██║   ███████║██╔██╗ ██║██║███████╗███████║██║"
echo "     ██║   ██╔══██║██║╚██╗██║██║╚════██║██╔══██║██║"
echo "     ██║   ██║  ██║██║ ╚████║██║███████║██║  ██║██║"
echo "     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝╚══════╝╚═╝  ╚═╝"
echo ""
echo "  Setting up your personal AI..."
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
    echo "❌ Python 3.11+ required. You have Python $PYTHON_VERSION"
    exit 1
fi
echo "✅ Python $PYTHON_VERSION"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate
source .venv/bin/activate
echo "✅ Virtual environment activated"

# Install dependencies
echo "📦 Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✅ Dependencies installed"

# Create .env if not exists
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  Created .env file. Please add your API key:"
    echo "    Edit .env and set ANTHROPIC_API_KEY=your-key-here"
    echo ""
    echo "    Get a key at: https://console.anthropic.com"
    echo ""
fi

# Create Tanishi home directory
mkdir -p ~/.tanishi/{memory,skills,logs}
echo "✅ Created ~/.tanishi/"

# Check for Ollama (optional)
if command -v ollama &> /dev/null; then
    echo "✅ Ollama detected (local LLM available)"
else
    echo "ℹ️  Ollama not found (optional — install for local/private processing)"
    echo "    https://ollama.ai"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  🧠 Setup complete!"
echo ""
echo "  To start Tanishi:"
echo "    source .venv/bin/activate"
echo "    python -m tanishi.cli"
echo ""
echo "  To start the API server:"
echo "    python -m tanishi.api.server"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
