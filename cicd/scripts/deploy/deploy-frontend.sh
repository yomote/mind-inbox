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

AUTH_TENANT_ID="$(resolve_auth_tenant_id)"
echo "AUTH_TENANT_ID=$AUTH_TENANT_ID"

sync_swa_auth_app_settings "$AUTH_TENANT_ID"

TOKEN="$(az staticwebapp secrets list -g "$RG" -n "$SWA_NAME" --query 'properties.apiKey' -o tsv)"
if [[ -z "$TOKEN" ]]; then
  echo "ERROR: failed to retrieve deployment token (apiKey)" >&2
  exit 1
fi

echo "--- build frontend ---"
cd "$FRONTEND_DIR"
if command -v pnpm >/dev/null 2>&1; then
  pnpm install --frozen-lockfile
  pnpm build
else
  npm ci
  npm run build
fi

DIST_DIR="$FRONTEND_DIR/dist"
if [[ ! -d "$DIST_DIR" ]]; then
  echo "ERROR: dist not found at $DIST_DIR" >&2
  exit 1
fi

CONFIG_FILE="$DIST_DIR/staticwebapp.config.json"
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: staticwebapp.config.json not found at $CONFIG_FILE" >&2
  exit 1
fi

sed -i "s|<TENANT_ID>|$AUTH_TENANT_ID|g" "$CONFIG_FILE"

if grep -q '<TENANT_ID>' "$CONFIG_FILE"; then
  echo "ERROR: failed to replace <TENANT_ID> in $CONFIG_FILE" >&2
  exit 1
fi

echo "--- deploy to SWA (production) ---"
# swa deploy supports passing the artifact folder directly
swa deploy "$DIST_DIR" --deployment-token "$TOKEN" --env production

SWA_HOST="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" --query defaultHostname -o tsv)"
echo "--- smoke (frontend) ---"
curl -fsS "https://$SWA_HOST" >/dev/null && echo "OK: https://$SWA_HOST"
