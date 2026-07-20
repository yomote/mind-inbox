#!/usr/bin/env bash
# Deploy BFF (Node.js + Azure Functions v4 + tRPC) to the existing Function App.
#
# 1. apps/bff で npm ci + tsc build
# 2. dist / node_modules / package.json / host.json を zip
# 3. config-zip で Function App にデプロイ
# 4. Container App の FQDN を取得して AI_AGENT_BASE_URL / VOICEVOX_BASE_URL を BFF env に注入
# 5. Function App を再起動して env を反映
#
# 前提: main-bootstrap が完了して Function App / Container Apps が存在する状態
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
need npm
need zip

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BFF_DIR="$ROOT_DIR/apps/bff"

# ── Resolve deployment outputs ────────────────────────────────────────────────
echo "=== Resolving deployment outputs ==="
_OUTPUTS="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query 'properties.outputs' -o json 2>/dev/null || echo '{}')"
_val() { printf '%s' "$_OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

FUNC_APP_NAME="${FUNC_APP_NAME:-}"
if [[ -z "$FUNC_APP_NAME" ]]; then
  FUNC_HOST="$(_val functionAppDefaultHostname)"
  FUNC_APP_NAME="${FUNC_HOST%%.*}"
fi
if [[ -z "$FUNC_APP_NAME" ]]; then
  echo "ERROR: FUNC_APP_NAME is empty (set FUNC_APP_NAME or ensure deployment outputs.functionAppDefaultHostname exists)" >&2
  exit 1
fi

AI_AGENT_CA_NAME="${AI_AGENT_CA_NAME:-$(_val aiAgentContainerAppName)}"
VV_WRAPPER_CA_NAME="${VV_WRAPPER_CA_NAME:-$(_val voicevoxWrapperContainerAppName)}"

echo "RG=$RG"
echo "DEPLOYMENT=$DEPLOYMENT"
echo "FUNC_APP_NAME=$FUNC_APP_NAME"
echo "AI_AGENT_CA_NAME=${AI_AGENT_CA_NAME:-<unset>}"
echo "VV_WRAPPER_CA_NAME=${VV_WRAPPER_CA_NAME:-<unset>}"

# NOTE: Linux Consumption(Y1) は config-zip(Kudu zipdeploy) 非対応で "Bad Request" になる。
# 正攻法は run-from-package（zip を storage blob に上げ SAS URL を WEBSITE_RUN_FROM_PACKAGE に設定）。
# 旧 preflight（URL 版 RFP を消す処理）は config-zip 前提だったので撤去した。

# ── Build BFF ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Building BFF ==="
cd "$BFF_DIR"
npm ci
npm run build

if [[ ! -d "$BFF_DIR/dist" ]]; then
  echo "ERROR: dist/ not found at $BFF_DIR/dist after build" >&2
  exit 1
fi

# ── Zip ───────────────────────────────────────────────────────────────────────
ZIP_PATH="$ROOT_DIR/.local/functionapp.zip"
mkdir -p "$(dirname "$ZIP_PATH")"
rm -f "$ZIP_PATH"

echo ""
echo "=== Creating deployment zip ==="
# Functions v4 (Node) zip layout:
#   /host.json
#   /package.json     (main = "dist/src/functions/*.js")
#   /dist/...         (compiled output)
#   /node_modules/... (production deps)
zip -qr "$ZIP_PATH" \
  host.json \
  package.json \
  package-lock.json \
  dist \
  node_modules \
  -x "node_modules/.cache/*" \
  -x "node_modules/.bin/*" \
  -x "**/*.map"

ZIP_BYTES="$(stat -c%s "$ZIP_PATH" 2>/dev/null || stat -f%z "$ZIP_PATH")"
echo "Zip size: $ZIP_BYTES bytes ($ZIP_PATH)"

# ── Deploy: run-from-package via storage blob SAS（Linux Consumption 正攻法）────
echo ""
echo "=== Deploying to Function App (run-from-package via blob SAS) ==="
cd "$ROOT_DIR"

# Function App が使う storage account 名を AzureWebJobsStorage 接続文字列から取得。
STG_CONN="$(az functionapp config appsettings list -g "$RG" -n "$FUNC_APP_NAME" \
  --query "[?name=='AzureWebJobsStorage'].value | [0]" -o tsv 2>/dev/null || true)"
STG_ACCOUNT="$(printf '%s' "$STG_CONN" | sed -n 's/.*AccountName=\([^;]*\).*/\1/p')"
if [[ -z "$STG_ACCOUNT" ]]; then
  # フォールバック: RG 内の st{...}func 命名の storage を拾う。
  STG_ACCOUNT="$(az storage account list -g "$RG" --query "[?starts_with(name,'st') && ends_with(name,'func')].name | [0]" -o tsv 2>/dev/null || true)"
fi
if [[ -z "$STG_ACCOUNT" ]]; then
  echo "ERROR: Function App の storage account 名を特定できませんでした。" >&2
  exit 1
fi
echo "Storage account: $STG_ACCOUNT"

STG_KEY="$(az storage account keys list -g "$RG" -n "$STG_ACCOUNT" --query "[0].value" -o tsv)"
if [[ -z "$STG_KEY" ]]; then
  echo "ERROR: storage account key を取得できませんでした（Shared Key 無効化の可能性）。" >&2
  exit 1
fi

CONTAINER="function-releases"
BLOB="bff-$(date -u +%Y%m%d%H%M%S).zip"
az storage container create --account-name "$STG_ACCOUNT" --account-key "$STG_KEY" \
  --name "$CONTAINER" -o none
az storage blob upload --account-name "$STG_ACCOUNT" --account-key "$STG_KEY" \
  --container-name "$CONTAINER" --name "$BLOB" --file "$ZIP_PATH" --overwrite -o none
echo "Uploaded: $CONTAINER/$BLOB"

# 読み取り専用 SAS（長め）。Function App がこの URL からパッケージを読んで実行する。
SAS_EXPIRY="$(date -u -d '+2 years' '+%Y-%m-%dT%H:%MZ' 2>/dev/null || date -u -v+2y '+%Y-%m-%dT%H:%MZ')"
SAS="$(az storage blob generate-sas --account-name "$STG_ACCOUNT" --account-key "$STG_KEY" \
  --container-name "$CONTAINER" --name "$BLOB" --permissions r --expiry "$SAS_EXPIRY" -o tsv)"
PKG_URL="https://${STG_ACCOUNT}.blob.core.windows.net/${CONTAINER}/${BLOB}?${SAS}"

echo "Setting WEBSITE_RUN_FROM_PACKAGE and restarting..."
az functionapp config appsettings set -g "$RG" -n "$FUNC_APP_NAME" \
  --settings "WEBSITE_RUN_FROM_PACKAGE=$PKG_URL" >/dev/null
az functionapp restart -g "$RG" -n "$FUNC_APP_NAME" >/dev/null

# ── Wire BFF env vars to live Container Apps ──────────────────────────────────
echo ""
echo "=== Wiring BFF env vars ==="

ai_agent_url=""
if [[ -n "$AI_AGENT_CA_NAME" ]]; then
  fqdn="$(az containerapp show -g "$RG" -n "$AI_AGENT_CA_NAME" \
    --query 'properties.configuration.ingress.fqdn' -o tsv 2>/dev/null || true)"
  if [[ -n "$fqdn" ]]; then
    ai_agent_url="https://${fqdn}"
    echo "  AI_AGENT_BASE_URL=$ai_agent_url"
  else
    echo "  WARN: AI Agent Container App '$AI_AGENT_CA_NAME' not found or has no ingress; AI_AGENT_BASE_URL will not be set" >&2
  fi
fi

vv_wrapper_url=""
if [[ -n "$VV_WRAPPER_CA_NAME" ]]; then
  fqdn="$(az containerapp show -g "$RG" -n "$VV_WRAPPER_CA_NAME" \
    --query 'properties.configuration.ingress.fqdn' -o tsv 2>/dev/null || true)"
  if [[ -n "$fqdn" ]]; then
    vv_wrapper_url="https://${fqdn}"
    echo "  VOICEVOX_BASE_URL=$vv_wrapper_url"
  else
    echo "  WARN: VOICEVOX Wrapper Container App '$VV_WRAPPER_CA_NAME' not found or has no ingress; VOICEVOX_BASE_URL will not be set" >&2
  fi
fi

settings=()
[[ -n "$ai_agent_url" ]] && settings+=("AI_AGENT_BASE_URL=$ai_agent_url")
[[ -n "$vv_wrapper_url" ]] && settings+=("VOICEVOX_BASE_URL=$vv_wrapper_url")

if [[ ${#settings[@]} -gt 0 ]]; then
  az functionapp config appsettings set \
    -g "$RG" -n "$FUNC_APP_NAME" \
    --settings "${settings[@]}" >/dev/null
  echo "  Applied ${#settings[@]} env vars; restarting Function App"
  az functionapp restart -g "$RG" -n "$FUNC_APP_NAME" >/dev/null
else
  echo "  No Container Apps available to wire; BFF will use stub responses"
fi

# ── Smoke ─────────────────────────────────────────────────────────────────────
echo ""
echo "--- smoke (direct function) ---"
# Even if EasyAuth returns 401, status line is what we care about (not 404 / 500).
curl -sS -D- -o /dev/null "https://$FUNC_APP_NAME.azurewebsites.net/api/trpc/health.ping" | sed -n '1,10p' || true
