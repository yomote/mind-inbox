#!/usr/bin/env bash
set -euo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"
# FRONTEND_PROFILE:
#   full (既定) = Entra 認証あり本番経路。SWA に AZURE_CLIENT_ID/SECRET が要る。
#   mock        = 匿名スタンドアロンデモ。BFF も認証も無く、mockApi で自己完結。
#                 スマホから即触れる検証用（on-demand env の既定）。
FRONTEND_PROFILE="${FRONTEND_PROFILE:-full}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command: $1" >&2
    exit 1
  }
}

need az
need curl
need swa

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/apps/frontend"

resolve_auth_tenant_id() {
  local tenant_id="${ENTRA_TENANT_ID:-}"

  if [[ -n "$tenant_id" ]]; then
    printf '%s\n' "$tenant_id"
    return 0
  fi

  tenant_id="$(az staticwebapp appsettings list -g "$RG" -n "$SWA_NAME" --query 'properties.AZURE_TENANT_ID' -o tsv 2>/dev/null || true)"
  if [[ -n "$tenant_id" ]]; then
    printf '%s\n' "$tenant_id"
    return 0
  fi

  tenant_id="$(az account show --query tenantId -o tsv 2>/dev/null || true)"
  if [[ -n "$tenant_id" ]]; then
    printf '%s\n' "$tenant_id"
    return 0
  fi

  echo "ERROR: failed to resolve Entra tenant ID. Set ENTRA_TENANT_ID explicitly." >&2
  exit 1
}

sync_swa_auth_app_settings() {
  local tenant_id="$1"
  local existing_client_id existing_client_secret
  local keyvault_name="${ENTRA_APP_KEYVAULT_NAME:-}"
  local client_id_secret_name="${ENTRA_APP_CLIENT_ID_SECRET_NAME:-}"
  local client_secret_secret_name="${ENTRA_APP_CLIENT_SECRET_SECRET_NAME:-}"

  if [[ -n "$keyvault_name" || -n "$client_id_secret_name" || -n "$client_secret_secret_name" ]]; then
    if [[ -z "$keyvault_name" || -z "$client_id_secret_name" || -z "$client_secret_secret_name" ]]; then
      echo "ERROR: ENTRA_APP_KEYVAULT_NAME, ENTRA_APP_CLIENT_ID_SECRET_NAME, and ENTRA_APP_CLIENT_SECRET_SECRET_NAME must be set together." >&2
      exit 1
    fi

    local client_id client_secret
    client_id="$(az keyvault secret show --vault-name "$keyvault_name" --name "$client_id_secret_name" --query value -o tsv)"
    client_secret="$(az keyvault secret show --vault-name "$keyvault_name" --name "$client_secret_secret_name" --query value -o tsv)"

    if [[ -z "$client_id" || -z "$client_secret" ]]; then
      echo "ERROR: failed to resolve AZURE_CLIENT_ID / AZURE_CLIENT_SECRET from Key Vault $keyvault_name" >&2
      exit 1
    fi

    az staticwebapp appsettings set \
      -g "$RG" \
      -n "$SWA_NAME" \
      --setting-names \
      "AZURE_CLIENT_ID=$client_id" \
      "AZURE_CLIENT_SECRET=$client_secret" \
      "AZURE_TENANT_ID=$tenant_id" >/dev/null

    echo "Updated SWA app settings from Key Vault: $keyvault_name"
    return 0
  fi

  existing_client_id="$(az staticwebapp appsettings list -g "$RG" -n "$SWA_NAME" --query 'properties.AZURE_CLIENT_ID' -o tsv 2>/dev/null || true)"
  existing_client_secret="$(az staticwebapp appsettings list -g "$RG" -n "$SWA_NAME" --query 'properties.AZURE_CLIENT_SECRET' -o tsv 2>/dev/null || true)"

  if [[ -z "$existing_client_id" || -z "$existing_client_secret" ]]; then
    echo "ERROR: SWA app settings AZURE_CLIENT_ID / AZURE_CLIENT_SECRET are missing." >&2
    echo "       Either set them beforehand or provide Key Vault env vars for this deploy:" >&2
    echo "       ENTRA_APP_KEYVAULT_NAME, ENTRA_APP_CLIENT_ID_SECRET_NAME, ENTRA_APP_CLIENT_SECRET_SECRET_NAME" >&2
    exit 1
  fi

  az staticwebapp appsettings set \
    -g "$RG" \
    -n "$SWA_NAME" \
    --setting-names \
    "AZURE_TENANT_ID=$tenant_id" >/dev/null
}

SWA_NAME="${SWA_NAME:-}"
if [[ -z "$SWA_NAME" ]]; then
  SWA_NAME="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query 'properties.outputs.staticSiteName.value' -o tsv)"
fi
if [[ -z "$SWA_NAME" ]]; then
  mapfile -t SWA_NAMES < <(az staticwebapp list -g "$RG" --query "[].name" -o tsv)
  if [[ ${#SWA_NAMES[@]} -eq 1 ]]; then
    SWA_NAME="${SWA_NAMES[0]}"
  elif [[ ${#SWA_NAMES[@]} -eq 0 ]]; then
    echo "ERROR: no Static Web App found in resource group $RG" >&2
    echo "       Set SWA_NAME explicitly." >&2
    exit 1
  else
    echo "ERROR: multiple Static Web Apps found in resource group $RG" >&2
    printf '       - %s\n' "${SWA_NAMES[@]}" >&2
    echo "       Set SWA_NAME explicitly." >&2
    exit 1
  fi
fi
if [[ -z "$SWA_NAME" ]]; then
  echo "ERROR: SWA_NAME is empty (set SWA_NAME or ensure deployment outputs.staticSiteName exists)" >&2
  exit 1
fi

echo "RG=$RG"
echo "DEPLOYMENT=$DEPLOYMENT"
echo "SWA_NAME=$SWA_NAME"
echo "FRONTEND_PROFILE=$FRONTEND_PROFILE"

# mock プロファイルは匿名デモなので Entra 認証の解決/同期をまるごと skip する
# （SWA に AZURE_CLIENT_ID/SECRET が無くても成立させる）。
if [[ "$FRONTEND_PROFILE" == "full" ]]; then
  AUTH_TENANT_ID="$(resolve_auth_tenant_id)"
  echo "AUTH_TENANT_ID=$AUTH_TENANT_ID"
  sync_swa_auth_app_settings "$AUTH_TENANT_ID"
else
  echo "mock プロファイル: Entra 認証の同期を skip（匿名デモ）"
fi

TOKEN="$(az staticwebapp secrets list -g "$RG" -n "$SWA_NAME" --query 'properties.apiKey' -o tsv)"
if [[ -z "$TOKEN" ]]; then
  echo "ERROR: failed to retrieve deployment token (apiKey)" >&2
  exit 1
fi

echo "--- build frontend (profile=$FRONTEND_PROFILE) ---"
cd "$FRONTEND_DIR"
# mock プロファイルは VITE_USE_MOCK=true で「BFF も認証も無い自己完結デモ」をビルドする。
BUILD_ENV=()
if [[ "$FRONTEND_PROFILE" != "full" ]]; then
  BUILD_ENV=(VITE_USE_MOCK=true)
fi
if command -v pnpm >/dev/null 2>&1; then
  pnpm install --frozen-lockfile
  env "${BUILD_ENV[@]}" pnpm build
else
  npm ci
  env "${BUILD_ENV[@]}" npm run build
fi

DIST_DIR="$FRONTEND_DIR/dist"
if [[ ! -d "$DIST_DIR" ]]; then
  echo "ERROR: dist not found at $DIST_DIR" >&2
  exit 1
fi

CONFIG_FILE="$DIST_DIR/staticwebapp.config.json"
if [[ "$FRONTEND_PROFILE" == "full" ]]; then
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: staticwebapp.config.json not found at $CONFIG_FILE" >&2
    exit 1
  fi
  sed -i "s|<TENANT_ID>|$AUTH_TENANT_ID|g" "$CONFIG_FILE"
  if grep -q '<TENANT_ID>' "$CONFIG_FILE"; then
    echo "ERROR: failed to replace <TENANT_ID> in $CONFIG_FILE" >&2
    exit 1
  fi
else
  # mock: 認証ゲート無しの匿名 config に差し替える（本番 config の allowedRoles:authenticated を外す）。
  MOCK_CONFIG="$DIST_DIR/staticwebapp.mock.config.json"
  if [[ ! -f "$MOCK_CONFIG" ]]; then
    echo "ERROR: staticwebapp.mock.config.json not found at $MOCK_CONFIG (public/ から dist へコピーされていない)" >&2
    exit 1
  fi
  cp "$MOCK_CONFIG" "$CONFIG_FILE"
  echo "mock プロファイル: 匿名 staticwebapp.config.json を適用"
fi

echo "--- deploy to SWA (production) ---"
# swa deploy supports passing the artifact folder directly
swa deploy "$DIST_DIR" --deployment-token "$TOKEN" --env production

SWA_HOST="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" --query defaultHostname -o tsv)"
echo "--- smoke (frontend) ---"
curl -fsS "https://$SWA_HOST" >/dev/null && echo "OK: https://$SWA_HOST"
