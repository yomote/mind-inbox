#!/usr/bin/env bash
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command: $1" >&2
    exit 1
  }
}

need az
need python3
need zip

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"

FUNC_APP_NAME="${FUNC_APP_NAME:-}"
if [[ -z "$FUNC_APP_NAME" ]]; then
  FUNC_HOST="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query 'properties.outputs.functionAppDefaultHostname.value' -o tsv)"
  # func-xxxx.azurewebsites.net -> func-xxxx
  FUNC_APP_NAME="${FUNC_HOST%%.*}"
fi
if [[ -z "$FUNC_APP_NAME" ]]; then
  echo "ERROR: FUNC_APP_NAME is empty (set FUNC_APP_NAME or ensure deployment outputs.functionAppDefaultHostname exists)" >&2
  exit 1
fi

echo "RG=$RG"
echo "DEPLOYMENT=$DEPLOYMENT"
echo "FUNC_APP_NAME=$FUNC_APP_NAME"

echo "--- preflight (app settings) ---"
# Core Tools / previous deployments may leave WEBSITE_RUN_FROM_PACKAGE as a URL.
# That can conflict with config-zip deployments.
RUN_FROM_PACKAGE_VALUE="$(az functionapp config appsettings list -g "$RG" -n "$FUNC_APP_NAME" --query "[?name=='WEBSITE_RUN_FROM_PACKAGE'].value | [0]" -o tsv)"
if [[ -n "$RUN_FROM_PACKAGE_VALUE" ]]; then
  echo "Found WEBSITE_RUN_FROM_PACKAGE=$RUN_FROM_PACKAGE_VALUE"
  if [[ "$RUN_FROM_PACKAGE_VALUE" == http* ]]; then
    echo "Deleting WEBSITE_RUN_FROM_PACKAGE (URL-based run-from-package can block zip deploy)"
    az functionapp config appsettings delete -g "$RG" -n "$FUNC_APP_NAME" --setting-names WEBSITE_RUN_FROM_PACKAGE >/dev/null
  fi
fi
echo "Track deployment: https://$FUNC_APP_NAME.scm.azurewebsites.net/api/deployments/latest"

echo "--- build backend ---"
cd "$BACKEND_DIR"

# Build Python dependencies into the layout expected by Azure Functions.
PYTHON_PKG_DIR="$BACKEND_DIR/.python_packages/lib/site-packages"
rm -rf "$BACKEND_DIR/.python_packages"
mkdir -p "$PYTHON_PKG_DIR"
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt --target "$PYTHON_PKG_DIR"

ZIP_PATH="$ROOT_DIR/.local/functionapp.zip"
mkdir -p "$(dirname "$ZIP_PATH")"
rm -f "$ZIP_PATH"

# zip deploy expects host.json at root of zip
# Exclude local-only files
zip -qr "$ZIP_PATH" . \
  -x "local.settings.json" \
  -x "local.settings.json.example" \
  -x ".venv/*" \
  -x "**/__pycache__/*" \
  -x "**/*.pyc" \
  -x ".git/*" \
  -x ".vscode/*"

echo "--- deploy (config-zip) ---"
cd "$ROOT_DIR"
az functionapp deployment source config-zip -g "$RG" -n "$FUNC_APP_NAME" --src "$ZIP_PATH"

echo "--- smoke (direct function) ---"
# If EasyAuth is enabled, this may be 401. We just want to see it's not a hard 404 from missing functions.
curl -sS -D- -o /dev/null "https://$FUNC_APP_NAME.azurewebsites.net/api/health" | sed -n '1,10p'
