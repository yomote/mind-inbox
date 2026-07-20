#!/usr/bin/env bash
set -euo pipefail

# 一度きり（A 方式）: SWA の Entra 認証を有効化する。
#   1. Entra アプリ登録（SWA 認証用）を作成 or 更新
#      - web リダイレクト URI = https://<SWA host>/.auth/login/aad/callback
#      - 単一テナント（openIdIssuer .../<TENANT_ID>/v2.0 に合わせる）
#   2. クライアントシークレットを発行
#   3. SWA の app settings に AZURE_CLIENT_ID / AZURE_CLIENT_SECRET / AZURE_TENANT_ID を設定
# その後 profile=full でデプロイすると deploy-frontend が認証ゲート付き config で配信する。
#
# 注意: SWA を作り直す（down→up）とホスト名が変わりリダイレクト URI が壊れる。その場合はこの
#       スクリプトを再実行する（現ホスト名で URI を貼り直す）。恒久自動化は別途（option B）。
#
# 前提: az ログイン済み（アプリ登録＋シークレット発行の権限が要る）。

RG="${RG:-rg-dev-mind-inbox}"
APP_NAME="${APP_NAME:-swa-auth-mind-inbox}"
SWA_NAME="${SWA_NAME:-}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 1; }; }
need az

az account show >/dev/null
TENANT_ID="$(az account show --query tenantId -o tsv)"

if [[ -z "$SWA_NAME" ]]; then
  SWA_NAME="$(az staticwebapp list -g "$RG" --query "[0].name" -o tsv)"
fi
if [[ -z "$SWA_NAME" ]]; then
  echo "ERROR: SWA が見つかりません（先に up してください）。" >&2; exit 1
fi

SWA_HOST="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" --query defaultHostname -o tsv)"
REDIRECT="https://${SWA_HOST}/.auth/login/aad/callback"
echo "SWA:      $SWA_NAME"
echo "Host:     $SWA_HOST"
echo "Redirect: $REDIRECT"
echo "Tenant:   $TENANT_ID"

# ── Entra アプリ登録（find or create）──────────────────────────────────────────
APP_COUNT="$(az ad app list --display-name "$APP_NAME" --query 'length(@)' -o tsv)"
if [[ "${APP_COUNT:-0}" -gt 1 ]]; then
  echo "ERROR: 同名アプリが ${APP_COUNT} 件あります（$APP_NAME）。整理してから再実行。" >&2; exit 1
fi
APP_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [[ -z "$APP_ID" ]]; then
  echo "==> Entra アプリ登録を作成: $APP_NAME"
  APP_ID="$(az ad app create \
    --display-name "$APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$REDIRECT" \
    --enable-id-token-issuance true \
    --query appId -o tsv)"
else
  echo "==> 既存アプリを更新（リダイレクト URI を現ホスト名に）: $APP_NAME ($APP_ID)"
  az ad app update --id "$APP_ID" \
    --web-redirect-uris "$REDIRECT" \
    --enable-id-token-issuance true
fi
echo "    appId: $APP_ID"

# ── クライアントシークレット（毎回ローテーション。SWA 設定へ即投入する）──────────
echo "==> クライアントシークレットを発行"
CLIENT_SECRET="$(az ad app credential reset --id "$APP_ID" \
  --display-name "swa-auth" --query password -o tsv)"
if [[ -z "$CLIENT_SECRET" ]]; then
  echo "ERROR: シークレット発行に失敗。" >&2; exit 1
fi

# ── SWA app settings へ配線 ────────────────────────────────────────────────────
echo "==> SWA app settings に AZURE_CLIENT_ID/SECRET/TENANT_ID を設定"
az staticwebapp appsettings set -g "$RG" -n "$SWA_NAME" --setting-names \
  "AZURE_CLIENT_ID=$APP_ID" \
  "AZURE_CLIENT_SECRET=$CLIENT_SECRET" \
  "AZURE_TENANT_ID=$TENANT_ID" >/dev/null

cat <<EOF

✅ SWA 認証セットアップ完了。
   appId(AZURE_CLIENT_ID) = $APP_ID
   tenant                 = $TENANT_ID
   redirect               = $REDIRECT

次: profile=full でデプロイすると認証ゲート付き（要ログイン）で実 AI 相談が配信されます。
   ※ down→up で SWA を作り直したら、このスクリプトを再実行してください（ホスト名が変わるため）。
EOF
