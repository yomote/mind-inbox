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

# frontend の `tsc -b` は tsconfig.app.json 経由で ../bff/src/trpc/router.ts も型検査するため、
# bff の依存が要る（未導入だと tRPC/domain 型が any に落ち、implicit-any で build が失敗する）。
# full profile は deploy-backend が先に bff を npm ci するが、mock profile は backend を経由しないので
# ここで担保する。既に導入済みなら skip。
if [[ ! -d "$ROOT_DIR/apps/bff/node_modules" ]]; then
  echo "--- install bff deps (shared trpc types) ---"
  npm --prefix "$ROOT_DIR/apps/bff" ci
fi

echo "--- build frontend (profile=$FRONTEND_PROFILE) ---"
cd "$FRONTEND_DIR"
# mock: swa CLI は public/staticwebapp.config.json を自動検出して使う（dist だけ差し替えても
# 無視され、本番 config の allowedRoles:authenticated が残って匿名アクセスが 401→ログインへ飛び 404 化する）。
# そこで build 前に source(public) を匿名 config に差し替える。vite が dist へコピーし、swa の探索も
# 匿名版を拾う。CI は fresh checkout なので source の書き換えは commit されず問題ない。
if [[ "$FRONTEND_PROFILE" != "full" ]]; then
  cp "$FRONTEND_DIR/public/staticwebapp.mock.config.json" "$FRONTEND_DIR/public/staticwebapp.config.json"
  echo "mock プロファイル: public/staticwebapp.config.json を匿名版に差し替え"
fi
# mock プロファイルは VITE_USE_MOCK=true で「BFF も認証も無い自己完結デモ」をビルドする。
BUILD_ENV=()
if [[ "$FRONTEND_PROFILE" != "full" ]]; then
  BUILD_ENV=(VITE_USE_MOCK=true)
fi
# VITE_VOICEVOX_BASE_URL が渡っていれば、standalone でも VOICEVOX wrapper を直接叩いて
# ずんだもんで読み上げる（mockvoice profile）。空なら frontend はブラウザ TTS にフォールバック。
if [[ -n "${VITE_VOICEVOX_BASE_URL:-}" ]]; then
  BUILD_ENV+=("VITE_VOICEVOX_BASE_URL=$VITE_VOICEVOX_BASE_URL")
  echo "VOICEVOX 直叩き URL: $VITE_VOICEVOX_BASE_URL"
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
  # mock: public を差し替え済みなので dist の config は既に匿名版。念のため認証ゲートが
  # 残っていないことを assert（allowedRoles が残っていると匿名アクセスが 404 化する）。
  if grep -q 'allowedRoles' "$CONFIG_FILE" 2>/dev/null; then
    echo "ERROR: dist の staticwebapp.config.json に allowedRoles が残存（匿名化に失敗）" >&2
    exit 1
  fi
  echo "mock プロファイル: 匿名 staticwebapp.config.json を確認"
fi

echo "--- deploy to SWA (production) ---"
# swa deploy supports passing the artifact folder directly
swa deploy "$DIST_DIR" --deployment-token "$TOKEN" --env production

SWA_HOST="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" --query defaultHostname -o tsv)"
echo "--- smoke (frontend) ---"
# -L を付けず、生の HTTP コードを確認する。認証ゲートが残っていると 200 ではなく
# 302(→/.auth/login) や 401 が返る。`curl -fsS` だけだと 3xx を成功扱いして誤検知するので、
# 明示的に 200 を要求する。
SMOKE_CODE="$(curl -sS -o /dev/null -w '%{http_code}' "https://$SWA_HOST" || echo 000)"
echo "smoke: http=$SMOKE_CODE https://$SWA_HOST"
if [[ "$FRONTEND_PROFILE" == "full" ]]; then
  # 認証あり: 匿名アクセスは 302(→/.auth/login) が正常。200/302/401 を許容し、404/500/000 のみ失敗。
  case "$SMOKE_CODE" in
    200 | 302 | 401)
      echo "OK(認証ゲート動作中): http=$SMOKE_CODE https://$SWA_HOST" ;;
    *)
      echo "ERROR: 予期しない応答 http=$SMOKE_CODE（配信未反映/500 の可能性）。" >&2
      exit 1 ;;
  esac
else
  # mock/mockvoice: 匿名で 200 必須（認証ゲートが残っていれば 302 になり 404 化する）。
  if [[ "$SMOKE_CODE" != "200" ]]; then
    echo "ERROR: 匿名アクセスで 200 が返りません（http=$SMOKE_CODE）。認証ゲート残存か配信未反映の可能性。" >&2
    exit 1
  fi
  echo "OK: https://$SWA_HOST"
fi
