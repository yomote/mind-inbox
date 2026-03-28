#!/usr/bin/env bash
set -euo pipefail

# Downloads the official Azure architecture icon pack (SVG) and maps a handful
# of services to the filenames expected by viz-structure.sh (PNG).
#
# Source (official): https://learn.microsoft.com/en-us/azure/architecture/icons/

HERE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ICONS_DIR="$HERE_DIR/icons"
CACHE_DIR="$ICONS_DIR/.cache"
RAW_DIR="$ICONS_DIR/raw"

ZIP_URL="https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V23.zip"
ZIP_PATH="$CACHE_DIR/Azure_Public_Service_Icons_V23.zip"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 2
  }
}

need curl

have() { command -v "$1" >/dev/null 2>&1; }

mkdir -p "$CACHE_DIR" "$RAW_DIR"

echo "== Download =="
if [[ -f "$ZIP_PATH" ]]; then
  echo "Using cached: $ZIP_PATH"
else
  echo "Fetching: $ZIP_URL"
  curl -fsSL -o "$ZIP_PATH" "$ZIP_URL"
fi

extract_zip() {
  local zip="$1"
  local out="$2"

  rm -rf "$out"
  mkdir -p "$out"

  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$zip" -d "$out"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import os, sys, zipfile
zip_path = sys.argv[1]
out_dir = sys.argv[2]
with zipfile.ZipFile(zip_path) as z:
    z.extractall(out_dir)
PY
    return 0
  fi

  echo "Need unzip or python3 to extract zip" >&2
  return 1
}

echo "== Extract =="
extract_zip "$ZIP_PATH" "$RAW_DIR"

# Find first match by patterns (case-insensitive)
find_one() {
  local pattern="$1"
  # Prefer SVG
  find "$RAW_DIR" -type f -iname "$pattern" | head -n 1
}

copy_or_link() {
  local src="$1"
  local dest="$2"
  if [[ -z "$src" ]]; then
    return 1
  fi
  # Copy (not symlink) so Graphviz IMG SRC works consistently.
  cp -f "$src" "$dest"
}

# Best-effort mapping: these patterns may need adjustment depending on icon pack structure.
# After running, you can manually replace any missing/incorrect icons.

declare -A MAP
# Prefer reasonably specific names to avoid accidental mismatches.
MAP["function-app.svg"]="*icon-service-Function-Apps.svg"
MAP["static-web-app.svg"]="*icon-service-Static-Apps.svg"
MAP["app-service-plan.svg"]="*icon-service-App-Service-Plans.svg"
MAP["sql-server.svg"]="*icon-service-SQL-Server.svg"
MAP["sql-db.svg"]="*icon-service-SQL-Database.svg"
MAP["vnet.svg"]="*icon-service-Virtual-Networks.svg"
MAP["private-endpoint.svg"]="*icon-service-Private-Endpoints.svg"
MAP["private-dns.svg"]="*icon-service-DNS-Zones.svg"
MAP["vnet-link.svg"]="*icon-service-Virtual-Networks.svg"
MAP["storage-account.svg"]="*icon-service-Storage-Accounts*.svg"
MAP["keyvault.svg"]="*icon-service-Key-Vaults.svg"
MAP["log-analytics.svg"]="*icon-service-Log-Analytics-Workspaces.svg"
MAP["container-apps.svg"]="*icon-service-Worker-Container-App.svg"
MAP["container-apps-environment.svg"]="*icon-service-Container-Apps-Environments.svg"

echo "== Map (best-effort) =="
missing=0
for out_name in "${!MAP[@]}"; do
  pattern="${MAP[$out_name]}"
  src=$(find_one "$pattern" || true)
  dest="$ICONS_DIR/$out_name"

  if [[ -n "$src" ]]; then
    copy_or_link "$src" "$dest"
    echo "OK  - $out_name <= $(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$src" "$HERE_DIR" 2>/dev/null || echo "$src")"
  else
    echo "WARN- not found for $out_name (pattern: $pattern)" >&2
    missing=$((missing+1))
  fi
done

echo
echo "== Convert SVG -> PNG =="

svg_to_png() {
  local src_svg="$1"
  local dest_png="$2"
  local size_px="${3:-64}"

  if have inkscape; then
    inkscape "$src_svg" --export-type=png --export-width="$size_px" --export-filename="$dest_png" >/dev/null 2>&1
    return 0
  fi

  if have convert; then
    # ImageMagick: -background none keeps transparency.
    convert -background none "$src_svg" -resize "${size_px}x${size_px}" "$dest_png"
    return 0
  fi

  return 1
}

converted=0
conv_missing=0

for svg in "$ICONS_DIR"/*.svg; do
  [[ -e "$svg" ]] || continue
  png="${svg%.svg}.png"
  if svg_to_png "$svg" "$png" 64; then
    echo "OK  - $(basename "$png")"
    converted=$((converted+1))
  else
    echo "WARN- could not convert $(basename "$svg") (need inkscape or imagemagick 'convert')" >&2
    conv_missing=$((conv_missing+1))
  fi
done

echo
echo "Done. Icons dir: $ICONS_DIR"
if [[ "$missing" -ne 0 ]]; then
  echo "WARN: $missing icons were not mapped automatically." >&2
  echo "- Open $RAW_DIR and copy correct SVGs into $ICONS_DIR with the expected filenames." >&2
fi

if [[ "$converted" -eq 0 ]]; then
  echo "WARN: No PNGs were generated. Graphviz usually embeds PNG more reliably than SVG." >&2
  echo "- Install inkscape or imagemagick, then re-run this script." >&2
fi

if [[ "$conv_missing" -ne 0 ]]; then
  echo "WARN: Some SVGs could not be converted to PNG." >&2
fi
