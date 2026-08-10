#!/usr/bin/env bash
# read-only の OIDC 識別を 1 発で作る (#209)。
#
# なぜリポジトリのルートに置いているか:
#   PO はスマホから Azure Cloud Shell を開くが、**ペーストが効かない**。
#   打つ量を最小にするため、短い URL で取れる場所に置く。作業が終わったら消してよい。
#
# 使い方 (Azure Cloud Shell / Bash):
#   bash <(curl -sL https://raw.githubusercontent.com/yomote/mind-inbox/ops/inspect/ro.sh)
#
# 何をするか (すべて冪等 — 既にあるものは作り直さない):
#   1. アプリ登録 gha-oidc-readonly-mind-inbox
#   2. フェデレーション資格情報 (ops/inspect ブランチ用)
#   3. サービスプリンシパル
#   4. ロール 3 つ (Reader / Cost Management Reader / Log Analytics Reader)
# **書き込み系のロールは付けない。**
set -uo pipefail

APP_NAME=gha-oidc-readonly-mind-inbox
REPO=yomote/mind-inbox
BRANCH=ops/inspect

echo "== 1. アプリ登録 =="
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv 2>/dev/null)
if [ -z "$APP_ID" ]; then
  APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv) || exit 1
  echo "  作成: $APP_ID"
else
  echo "  既存を再利用: $APP_ID"
fi

echo "== 2. フェデレーション資格情報 =="
SUBJECT="repo:${REPO}:ref:refs/heads/${BRANCH}"
if az ad app federated-credential list --id "$APP_ID" --query "[?subject=='$SUBJECT'] | [0].name" -o tsv 2>/dev/null | grep -q .; then
  echo "  既にある (subject: $SUBJECT)"
else
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"gha-ro-ops-inspect\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" >/dev/null && echo "  作成: $SUBJECT" || echo "  ⚠️ 作成に失敗 (上のエラーを確認)"
fi

echo "== 3. サービスプリンシパル =="
SP_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv 2>/dev/null)
if [ -z "$SP_ID" ]; then
  SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv) || exit 1
  echo "  作成: $SP_ID"
else
  echo "  既存を再利用: $SP_ID"
fi

echo "== 4. ロール (read-only のみ) =="
SUB=$(az account show --query id -o tsv)
for R in "Reader" "Cost Management Reader" "Log Analytics Reader"; do
  if az role assignment list --assignee "$SP_ID" --scope "/subscriptions/$SUB" \
       --query "[?roleDefinitionName=='$R'] | [0].id" -o tsv 2>/dev/null | grep -q .; then
    echo "  既にある: $R"
  else
    az role assignment create --assignee-object-id "$SP_ID" \
      --assignee-principal-type ServicePrincipal --role "$R" \
      --scope "/subscriptions/$SUB" >/dev/null \
      && echo "  付与: $R" || echo "  ⚠️ 付与に失敗: $R (権限不足の可能性)"
  fi
done

echo ""
echo "================================================================"
echo " GitHub → Settings → Secrets and variables → Actions → Variables"
echo " に、この値を AZURE_CLIENT_ID_RO として登録してください:"
echo ""
echo "   $APP_ID"
echo "================================================================"
