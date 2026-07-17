#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Video Refactor"
BUILD_DIR="$ROOT_DIR/build"
DIST_DIR="$ROOT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
RESOURCES_DIR="$APP_DIR/Contents/Resources"
ARCH="$(uname -m)"

mkdir -p "$BUILD_DIR" "$MACOS_DIR" "$RESOURCES_DIR"

swiftc \
  -parse-as-library \
  -O \
  -module-cache-path "$BUILD_DIR/ModuleCache" \
  -target "$ARCH-apple-macos12.0" \
  -framework SwiftUI \
  -framework AppKit \
  "$ROOT_DIR/MacApp/VideoRefactorApp.swift" \
  -o "$MACOS_DIR/VideoRefactor"

cp "$ROOT_DIR/MacApp/Info.plist" "$APP_DIR/Contents/Info.plist"

echo "Built: $APP_DIR"
