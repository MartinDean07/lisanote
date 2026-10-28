#!/bin/sh
# Universal launcher (Linux/macOS). Python, in order of preference:
#   1. Lisanote's own venv (created by install.sh when extra packages are needed)
#   2. Hermes Agent's venv, if installed (enables the "Hermes Agent" provider)
#   3. system python3 (cloud providers need nothing beyond the standard library)
DIR="$(cd "$(dirname "$0")" && pwd)"
HP="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python"
if [ -x "$DIR/.venv/bin/python" ]; then PY="$DIR/.venv/bin/python"
elif [ -x "$HP" ]; then PY="$HP"
else PY="$(command -v python3)"; fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ "$1" = "--cli" ]; then shift; exec "$PY" "$DIR/lisanote.py" "$@"; fi
exec "$PY" "$DIR/lisanote_gui.py" "$@"
