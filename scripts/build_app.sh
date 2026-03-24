#!/usr/bin/env bash
# Build Meeting Recorder as a macOS .app bundle using py2app.
#
# Usage:
#   ./scripts/build_app.sh
#
# Prerequisites:
#   pip install py2app
#   pip install -e .

set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Cleaning previous builds..."
rm -rf build dist

echo "==> Building .app with py2app..."
python setup.py py2app --emulate-shell-environment

echo ""
echo "==> Build complete!"
echo "    App bundle: dist/app.app"
echo "    To test: open dist/app.app"
