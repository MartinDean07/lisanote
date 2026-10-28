#!/bin/sh
# macOS: double-click in Finder. First run: allow Microphone for Terminal when asked.
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/livescribe-launch.sh"
