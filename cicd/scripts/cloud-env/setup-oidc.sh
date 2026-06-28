#!/usr/bin/env bash
set -euo pipefail

# 一度きり: GitHub Actions(deploy.yml) が Azure へ OIDC ログインするための連携を作る。
#   1. Entra アプリ登録(+ SP)
#   2. federated credential（GitHub の OIDC subject を信頼）
#   3. ロール付与（up は RG 作成、down は RG 削除するためサブスクリプションスコープ）
# 実行: device-code でログイン後（管理権限が要る）。出力の3変数を GitHub の repo Variables に登録する。
#
# 関連: docs/runbooks/azure-oidc-cd-setup.md / ADR 0009

REPO="${REPO:-yomote/mind-inbox}"
APP_NAME="${APP_NAME:-gha-oidc-mind-inbox-cd}"
BRANCH="${BRANCH:-main}"
ROLE="${ROLE:-Contributor}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command: $1" >&2
    exit 1
  }
}
need az
az account show >/dev/null

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
TENANT_ID="$(az account show --query tenantId -o tsv)"

echo "==> Entra app registration: $APP_NAME"
APP_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [[ -z "${APP_ID}" ]]; then
  APP_ID="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)"
fi
az ad sp show --id "$APP_ID" >/dev/null 2>&1 || az ad sp create --id "$APP_ID" >/dev/null

echo "==> Federated credential (subject = repo:${REPO}:ref:refs/heads/${BRANCH})"
SUBJECT="repo:${REPO}:ref:refs/heads/${BRANCH}"
EXISTING="$(az ad app federated-credential list --id "$APP_ID" \
  --query "[?subject=='${SUBJECT}'].name | [0]" -o tsv 2>/dev/null || true)"
if [[ -z "${EXISTING}" ]]; then
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"gha-${BRANCH}\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"${SUBJECT}\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" >/dev/null
fi

echo "==> Role assignment: ${ROLE} @ /subscriptions/${SUBSCRIPTION_ID}"
SP_OBJECT_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"
az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "$ROLE" \
  --scope "/subscriptions/${SUBSCRIPTION_ID}" >/dev/null 2>&1 || \
  echo "   (role assignment は既存 or 権限不足の可能性。az role assignment list で確認)"

cat <<EOF

✅ 完了。次を GitHub の repo Variables に登録してください
   (Settings → Secrets and variables → Actions → Variables タブ):

   AZURE_CLIENT_ID=${APP_ID}
   AZURE_TENANT_ID=${TENANT_ID}
   AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION_ID}

その後、Actions → "deploy" → Run workflow で up/down を実行できます。
EOF
