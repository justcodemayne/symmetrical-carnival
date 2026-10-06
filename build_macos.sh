#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "creating virtualenv"
  python3 -m venv "$VENV"
fi

install_requirements() {
  if "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q "$@"
    return
  fi
  if "$PY" -m ensurepip --version >/dev/null 2>&1; then
    "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
  fi
  if "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m pip install -q "$@"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    VIRTUAL_ENV="$VENV" uv pip install -q "$@"
    return
  fi
  echo "no pip or uv available to install dependencies"
  exit 1
}

echo "installing dependencies"
install_requirements -r requirements-dev.txt

mkdir -p build dist

echo "running tests"
"$PY" tests/test_gaswatch.py

echo "generating icon"
"$PY" scripts/make_icon.py build/GasWatch.png
"$PY" scripts/verify_icon.py build/GasWatch.png
rm -rf build/GasWatch.iconset
mkdir -p build/GasWatch.iconset
for spec in "16:icon_16x16" "32:icon_16x16@2x" "32:icon_32x32" "64:icon_32x32@2x" \
            "128:icon_128x128" "256:icon_128x128@2x" "256:icon_256x256" \
            "512:icon_256x256@2x" "512:icon_512x512" "1024:icon_512x512@2x"; do
  px="${spec%%:*}"
  name="${spec##*:}"
  sips -z "$px" "$px" build/GasWatch.png --out "build/GasWatch.iconset/${name}.png" >/dev/null
done
iconutil -c icns build/GasWatch.iconset -o build/GasWatch.icns

echo "building app bundle"
rm -rf dist/GasWatch.app
"$VENV/bin/pyinstaller" gaswatch.spec --noconfirm --clean --log-level WARN

APP="dist/GasWatch.app"
if [ ! -d "$APP" ]; then
  echo "build failed: $APP not found"
  exit 1
fi

if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "ad-hoc signing skipped"
fi

echo "verifying bundle"
"$PY" - "$APP" <<'PY'
import sys, subprocess
from pathlib import Path
app = Path(sys.argv[1])
binary = app / "Contents" / "MacOS" / "GasWatch"
assert binary.exists(), f"missing {binary}"
info = app / "Contents" / "Info.plist"
assert info.exists(), "missing Info.plist"
icon = app / "Contents" / "Resources" / "GasWatch.icns"
print(f"bundle ok: {binary.stat().st_size / 1e6:.1f} MB executable")
print(f"icon embedded: {icon.exists()}")
result = subprocess.run([str(binary), "--help"], capture_output=True, text=True, timeout=60)
print("cli reachable from bundle:", result.returncode == 0)
print(result.stdout.splitlines()[0] if result.stdout else result.stderr.splitlines()[0])
PY

mkdir -p "$ROOT/release/macos-arm64"
cp -R "$APP" "$ROOT/release/macos-arm64/"
if [ -f build/GasWatch.png ]; then
  cp build/GasWatch.png "$ROOT/release/macos-arm64/GasWatch-icon.png"
fi

cat > "$ROOT/release/macos-arm64/How-to-install.txt" <<'TXT'
GasWatch 1.0.0 for macOS (Apple silicon)

1. Move GasWatch.app into your Applications folder, or drag it to your Desktop.
2. The first launch is blocked because the app is not notarised:
   right click the app, choose Open, then click Open again.
   Or run this once in Terminal:
     xattr -dr com.apple.quarantine /Applications/GasWatch.app
3. Launch it from Applications. There is no Terminal window; it is a
   normal windowed desktop app.

Settings, RPC endpoints and thresholds are stored in:
  ~/.gaswatch/config.json
TXT

echo "done: $ROOT/release/macos-arm64/GasWatch.app"