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
need jq
az account show >/dev/null

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-$(az account show --query id -o tsv)}"
TENANT_ID="$(az account show --query tenantId -o tsv)"

echo "==> Entra app registration: $APP_NAME"
# 同名アプリが複数あると [0] をサイレント選択してしまう。意図しないアプリ操作を避けて中断。
APP_COUNT="$(az ad app list --display-name "$APP_NAME" --query 'length(@)' -o tsv)"
if [[ "${APP_COUNT:-0}" -gt 1 ]]; then
  echo "ERROR: 同名アプリが ${APP_COUNT} 件あります（$APP_NAME）。手動で整理してから再実行してください。" >&2
  exit 1
fi
APP_ID="$(az ad app list --display-name "$APP_NAME" --query '[0].appId' -o tsv)"
if [[ -z "${APP_ID}" ]]; then
  APP_ID="$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)"
fi
az ad sp show --id "$APP_ID" >/dev/null 2>&1 || az ad sp create --id "$APP_ID" >/dev/null

echo "==> Federated credential (subject = repo:${REPO}:ref:refs/heads/${BRANCH})"
SUBJECT="repo:${REPO}:ref:refs/heads/${BRANCH}"
# 既存判定も jq でフィルタ（subject に ' 等が入っても JMESPath 直展開で壊れないように）。
EXISTING="$(az ad app federated-credential list --id "$APP_ID" -o json 2>/dev/null \
  | jq -r --arg s "$SUBJECT" '[.[] | select(.subject == $s) | .name][0] // empty' 2>/dev/null || true)"
if [[ -z "${EXISTING}" ]]; then
  # JSON は jq で生成（ブランチ名に "や\ が入っても壊れないように raw 展開を避ける）。
  FED_PARAMS="$(jq -n --arg name "gha-${BRANCH}" --arg subject "$SUBJECT" \
    '{name: $name, issuer: "https://token.actions.githubusercontent.com", subject: $subject, audiences: ["api://AzureADTokenExchange"]}')"
  az ad app federated-credential create --id "$APP_ID" --parameters "$FED_PARAMS" >/dev/null
fi

echo "==> Role assignment: ${ROLE} @ /subscriptions/${SUBSCRIPTION_ID}"
SP_OBJECT_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"
SCOPE="/subscriptions/${SUBSCRIPTION_ID}"
# 冪等: 既にあれば再利用。無ければ作成し、失敗（権限不足等）は握り潰さず exit する。
# ここを飲み込むと「✅ 完了」と表示されたまま、後段 CD で初めて AuthorizationFailed として顕在化し、
# ユーザーが原因（ロール未付与）にたどり着けなくなるため、確実に exit させる。
EXISTING_ROLE="$(az role assignment list \
  --assignee "$APP_ID" --role "$ROLE" --scope "$SCOPE" \
  --query "[0].id" -o tsv 2>/dev/null || true)"
if [[ -n "${EXISTING_ROLE}" ]]; then
  echo "   既存のロール割当を再利用: ${ROLE}"
elif az role assignment create \
  --assignee-object-id "$SP_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "$ROLE" \
  --scope "$SCOPE" -o none; then
  echo "   ロール割当を作成: ${ROLE}"
else
  echo "ERROR: ロール付与に失敗しました（サブスクリプションへの権限不足の可能性）。" >&2
  echo "       Owner 相当の権限で実行し直してください。付与されないと CD で AuthorizationFailed になります。" >&2
  exit 1
fi

cat <<EOF

✅ 完了。次を GitHub の repo Variables に登録してください
   (Settings → Secrets and variables → Actions → Variables タブ):

   AZURE_CLIENT_ID=${APP_ID}
   AZURE_TENANT_ID=${TENANT_ID}
   AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION_ID}

その後、Actions → "deploy" → Run workflow で up/down を実行できます。
EOF
