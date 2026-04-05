#!/usr/bin/env bash
# Package the .app bundle into a DMG for distribution.
#
# Usage:
#   ./scripts/create_dmg.sh
#
# Prerequisites:
#   Run ./scripts/build_app.sh first.

set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="Muesli"
DMG_NAME="Muesli-0.1.0.dmg"
APP_PATH="dist/app.app"

if [ ! -d "$APP_PATH" ]; then
    echo "Error: $APP_PATH not found. Run ./scripts/build_app.sh first."
    exit 1
fi

echo "==> Creating DMG..."

# Create a temporary directory for the DMG contents
DMG_DIR=$(mktemp -d)
cp -R "$APP_PATH" "$DMG_DIR/$APP_NAME.app"

# Create a symlink to /Applications for drag-and-drop install
ln -s /Applications "$DMG_DIR/Applications"

# Create the DMG
hdiutil create -volname "$APP_NAME" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "dist/$DMG_NAME"

# Clean up
rm -rf "$DMG_DIR"

echo ""
echo "==> DMG created: dist/$DMG_NAME"
