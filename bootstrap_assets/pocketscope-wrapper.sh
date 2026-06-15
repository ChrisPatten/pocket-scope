#!/bin/bash
# Wrapper script for PocketScope service
# Resolves the user's home directory and runs PocketScope from their venv

# Get the user's home directory
USER_HOME=$(getent passwd "$1" | cut -d: -f6)

if [ -z "$USER_HOME" ]; then
    echo "ERROR: Could not determine home directory for user $1" >&2
    exit 1
fi

VENV_PYTHON="$USER_HOME/pocket-scope/.venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: Python not found at $VENV_PYTHON" >&2
    exit 1
fi

# Shift to remove the username argument, pass remaining args to Python
shift
exec "$VENV_PYTHON" -m pocketscope "$@"

