#!/usr/bin/env bash
# read-only の OIDC 識別を 1 発で作る (#209)。
#
# なぜリポジトリのルートに置いているか:
#   PO はスマホから Azure Cloud Shell を開くが、**ペーストが効かない**。
#   打つ量を最小にするため、短い URL で取れる場所に置く。作業が終わったら消してよい。
#
# 使い方 (Azure Cloud Shell / Bash) — **必ず commit sha で固定して取ること**:
#   bash <(curl -sL https://raw.githubusercontent.com/yomote/mind-inbox/<commit-sha>/ro.sh)
#
#   sha は GitHub の ro.sh のページで `y` を押す (Copy permalink) と URL に入る。
#
#   ブランチ名 (`ops/inspect`) で取ってはいけない。**このスクリプトは PO 自身の
#   device-code ログイン (ADR 0006 — PO の Azure 権限をそのまま継承) の下で走る**一方、
#   `ops/inspect` は調査のたびに直 push する運用のためブランチ保護が無い。
#   ブランチ名で always-latest を取ると、このブランチに push できる者が中身を
#   書き換えた瞬間、次の実行で **read-only の範囲を超えて PO の全権限で任意コマンドが
#   走る**。「書き込み系のロールを付けない」という下の安全設計は、実行者が人間に
#   変わることで無意味化する。sha で固定すれば、読んだものと走るものが一致する。
#   (この経路のトレードオフ全体は ADR 0047 に記録した)
#
# 何をするか (すべて冪等 — 既にあるものは作り直さない):
#   1. アプリ登録 gha-oidc-readonly-mind-inbox
#   2. フェデレーション資格情報 (ops/inspect ブランチ用)
#   3. サービスプリンシパル
#   4. ロール 3 つ (Reader / Cost Management Reader / Log Analytics Reader)
# **書き込み系のロールは付けない。**
#
# 失敗の扱い: どこか 1 つでも失敗したら **最後に非ゼロで終了し、client ID の登録案内を出さない**。
#   途中の失敗を握り潰して案内まで進むと、OIDC 資格情報やロールが欠けた ID を登録してしまい、
#   次の ops-inspect が Azure login やクエリで落ちるまで不完全な構成に気づけない。
#   全ステップ冪等なので、原因を直してそのまま再実行してよい。
set -uo pipefail

APP_NAME=gha-oidc-readonly-mind-inbox
REPO=yomote/mind-inbox
BRANCH=ops/inspect

# 失敗したステップ名を溜める (空なら成功)。
FAILURES=""
fail_step() {
  FAILURES="${FAILURES}
  - $1"
  echo "  ⚠️ 失敗: $1"
}

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
  if az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"gha-ro-ops-inspect\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" >/dev/null; then
    echo "  作成: $SUBJECT"
  else
    fail_step "フェデレーション資格情報の作成 (subject: $SUBJECT) — 上のエラーを確認"
  fi
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
# サブスクリプション ID が取れないとスコープが "/subscriptions/" になり、
# 付与先の違う (あるいは失敗する) ロール割り当てになる。取れなければロール付与ごと飛ばす。
SUB=$(az account show --query id -o tsv) || SUB=""
if [ -z "$SUB" ]; then
  fail_step "サブスクリプション ID の取得 (az account show) — ロール付与を行っていない"
else
  for R in "Reader" "Cost Management Reader" "Log Analytics Reader"; do
    if az role assignment list --assignee "$SP_ID" --scope "/subscriptions/$SUB" \
         --query "[?roleDefinitionName=='$R'] | [0].id" -o tsv 2>/dev/null | grep -q .; then
      echo "  既にある: $R"
    else
      if az role assignment create --assignee-object-id "$SP_ID" \
        --assignee-principal-type ServicePrincipal --role "$R" \
        --scope "/subscriptions/$SUB" >/dev/null; then
        echo "  付与: $R"
      else
        fail_step "ロールの付与: $R (権限不足の可能性)"
      fi
    fi
  done
fi

echo ""
if [ -n "$FAILURES" ]; then
  echo "================================================================"
  echo " ❌ 未完了のステップがあります。この ID を登録しないでください:"
  echo "$FAILURES"
  echo ""
  echo " 原因を解消してから、このスクリプトをもう一度実行してください"
  echo " (全ステップ冪等なので、成功済みの分は作り直しません)。"
  echo "================================================================"
  exit 1
fi
echo "================================================================"
echo " GitHub → Settings → Secrets and variables → Actions → Variables"
echo " に、この値を AZURE_CLIENT_ID_RO として登録してください:"
echo ""
echo "   $APP_ID"
echo "================================================================"
