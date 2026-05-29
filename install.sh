#!/bin/bash
# surf installer — sets up the virtualenv, dependencies, and /usr/local/bin/surf

set -e

SURF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

echo "surf installer"
echo "─────────────────────────────────────────"
echo "Installing to: $SURF_DIR"
echo

# Check Python version
PYTHON_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    echo "Error: Python 3.10+ required (found $PYTHON_VERSION)"
    exit 1
fi
echo "Python $PYTHON_VERSION ✓"

# Create virtualenv
if [ ! -d "$SURF_DIR/.venv" ]; then
    echo "Creating virtualenv..."
    $PYTHON -m venv "$SURF_DIR/.venv"
fi

# Install dependencies
echo "Installing dependencies..."
"$SURF_DIR/.venv/bin/pip" install -q -r "$SURF_DIR/requirements.txt"
echo "Dependencies ✓"

# Create /usr/local/bin/surf
WRAPPER="/usr/local/bin/surf"
echo "Installing $WRAPPER..."

sudo tee "$WRAPPER" > /dev/null << EOF
#!/bin/bash
exec "$SURF_DIR/.venv/bin/python3" "$SURF_DIR/surf.py" "\$@"
EOF
sudo chmod +x "$WRAPPER"
echo "Installed $WRAPPER ✓"

# Check for API key config
CONFIG="$HOME/.config/surf/config"
if [ ! -f "$CONFIG" ]; then
    echo
    echo "Next: configure your API key"
    echo "─────────────────────────────────────────"
    mkdir -p "$HOME/.config/surf"
    cat > "$CONFIG" << 'CONF'
# surf configuration — at least one API key required
# Claude is recommended: claude.ai/settings → API Keys ($1/month for ~2500 searches)
ANTHROPIC_API_KEY=

# Optional free fallbacks
# GROQ_API_KEY=        # console.groq.com
# GEMINI_API_KEY=      # aistudio.google.com
# CEREBRAS_API_KEY=    # inference.cerebras.ai
CONF
    echo "Created $CONFIG"
    echo "Edit it and add your ANTHROPIC_API_KEY, then run: surf what is a black hole"
else
    echo
    echo "Config already exists at $CONFIG ✓"
fi

echo
echo "Done. Try: surf what is a black hole"
