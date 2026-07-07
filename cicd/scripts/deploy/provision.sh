#!/usr/bin/env bash
set -euo pipefail

# オンデマンド環境の「立ち上げ（up）」: RG 作成 → bootstrap(IaC) → コンテナ → BFF/frontend。
# cicd/iac/README.md の「0. 最短ルート（初回）」を踏襲し、コンテナ反映の順序を足したもの。
# cleanup-env.sh（down）と対。GitHub Actions(deploy.yml) と device-code セッションの両方から呼べる。
#
# 前提: az ログイン済み（OIDC or device-code）/ サブスクリプション選択済み / bicep 利用可。
# NOTE: このスクリプトは IaC/README のコマンドから機械的に組んだもの。初回は region/quota の
#       影響が出るため、device-code で対話実行して通ることを確認してから CD に委ねること。

# RG 名は既存スクリプト（deploy-all.sh / cleanup-env.sh）の既定に合わせ rg-dev-mind-inbox とする。
# （CLAUDE.md の命名規約 {type}-{env}-{appname} は bicep が作る“中身のリソース名”の話で、
#  RG 名そのものは別。up/down を既存スクリプトと同じ RG に揃えるため意図的にこの値。）
RG="${RG:-rg-dev-mind-inbox}"
LOCATION="${LOCATION:-japaneast}"
APP_NAME="${APP_NAME:-mind-box}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"
# VOICEVOX tier(ADR 0010): cpu = 速く安く（既定）/ gpu = T4 で喋りが速い。
VOICEVOX_TIER="${VOICEVOX_TIER:-cpu}"
# DEPLOY_PROFILE:
#   mock (既定) = 匿名スタンドアロンデモ。frontend を VITE_USE_MOCK=true で SWA へ配信するだけ。
#                 BFF/ai-agent/VOICEVOX wrapper のデプロイは skip（mockApi で自己完結、スマホ即検証）。
#   full        = 実バックエンド経路。wrapper → ai-agent(gated) → BFF+frontend を配線し Entra 認証前提。
# on-demand env の狙い（安く速くスマホ検証）に合わせ既定 mock。実 backend が要るとき full にする。
DEPLOY_PROFILE="${DEPLOY_PROFILE:-mock}"
# Entra 認証(main-config)はUAMI事前準備が要るのでここでは有効化しない（個人デモは匿名SWAで可）。
# true でも実際の有効化はせず案内ログのみ出す（変数名はその挙動を表す）。
PRINT_ENTRA_AUTH_HINT="${PRINT_ENTRA_AUTH_HINT:-false}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
IAC_DIR="$ROOT_DIR/cicd/iac"
DEPLOY_DIR="$ROOT_DIR/cicd/scripts/deploy"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: missing required command: $1" >&2
    exit 1
  }
}
# 下流スクリプト（deploy-backend: npm/zip, deploy-frontend: pnpm/swa/curl）が要るツールを
# 先にまとめて検査し、途中の別スクリプトで初めて落ちて原因特定が遅れるのを防ぐ。
# コンテナ系は `az acr build`（クラウドビルド）なので docker は不要。
# frontend は pnpm-lock.yaml 前提（package-lock.json を置かない方針）なので pnpm は必須。
# deploy-frontend.sh の `npm ci` フォールバックはこのリポでは lockfile 不在で失敗するため当てにしない。
need az
need npm
need pnpm
need zip
need curl
need swa

az account show >/dev/null

echo "==> [1/5] Resource group: $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" >/dev/null
# 立ち上げ時刻を RG タグに記録 → 夜間 schedule teardown が「最小生存時間」ガードで参照する。
# 失敗してもデプロイ自体は続行するが、サイレントにはしない（ガードが効かず夜間に即撤収されうるため警告）。
if ! az group update -n "$RG" --set "tags.deployedAtEpoch=$(date +%s)" -o none >/dev/null 2>&1; then
  echo "WARN: deployedAtEpoch タグの記録に失敗。夜間 teardown の最小生存時間ガードが効かず、" >&2
  echo "      立てた直後でも夜間 schedule で撤収される可能性があります。" >&2
fi

echo "==> [2/5] Bootstrap infra (main-bootstrap.bicep) — voicevoxTier=$VOICEVOX_TIER"
az deployment group create \
  -g "$RG" \
  -n "$DEPLOYMENT" \
  -f "$IAC_DIR/main-bootstrap.bicep" \
  -p @"$IAC_DIR/main-bootstrap.parameters.json" \
  -p appName="$APP_NAME" environmentName="$ENVIRONMENT" voicevoxTier="$VOICEVOX_TIER" \
  -o none

if [[ "$PRINT_ENTRA_AUTH_HINT" == "true" ]]; then
  echo "==> [hint] Entra 認証(main-config) は UAMI 事前準備が要る。手順は iac/README §3 を参照（ここでは有効化しない）"
fi

if [[ "$DEPLOY_PROFILE" == "mock" ]]; then
  # mock: 匿名スタンドアロン。bootstrap で出来た SWA に frontend(mockApi) を配信するだけ。
  # BFF/ai-agent/VOICEVOX wrapper のデプロイは不要（mockApi で自己完結、スマホ即検証）。
  echo "==> [3/3] Frontend (SWA, mock standalone) — profile=mock"
  RG="$RG" DEPLOYMENT="$DEPLOYMENT" FRONTEND_PROFILE=mock "$DEPLOY_DIR/deploy-frontend.sh"
else
  # コンテナは BFF より先に（deploy-backend が wrapper/ai-agent の FQDN を func 設定へ配線するため）。
  echo "==> [3/5] VOICEVOX wrapper (Container App)"
  RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$DEPLOY_DIR/deploy-voicevox-wrapper.sh"

  # AI Agent は enableAiAgentAca=false のとき bootstrap がリソースを作らない（= 出力 aiAgentEnabled=false）。
  # その場合 deploy-ai-agent.sh は CA 名/OpenAI endpoint 不在で必ず落ちるので、ここでスキップする。
  # BFF は AI_AGENT_BASE_URL 未設定ならスタック応答で回る（deploy-backend.sh 参照）ので声/UX 検証は成立する。
  # NOTE: gpt-4o 系は全バージョンが Azure 上で Deprecating（新規デプロイ不可）。実 AI を戻すときは
  #       GA モデル（gpt-5 系）へ移行してから enableOpenAi/enableAiAgentAca を true に戻すこと。
  AI_AGENT_ENABLED="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
    --query 'properties.outputs.aiAgentEnabled.value' -o tsv 2>/dev/null || echo false)"
  if [[ "$AI_AGENT_ENABLED" == "true" ]]; then
    echo "==> [4/5] AI Agent (Container App)"
    RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$DEPLOY_DIR/deploy-ai-agent.sh"
  else
    echo "==> [4/5] AI Agent — スキップ（enableAiAgentAca=false / aiAgentEnabled=$AI_AGENT_ENABLED）。BFF はスタック応答で回る。"
  fi

  echo "==> [5/5] Backend(BFF) + Frontend(SWA)"
  RG="$RG" DEPLOYMENT="$DEPLOYMENT" FRONTEND_PROFILE=full "$DEPLOY_DIR/deploy-all.sh"
fi

echo
echo "✅ Provision 完了（profile=$DEPLOY_PROFILE）。SWA の URL は上の deploy-frontend 出力を参照。"
echo "   撤収（¥0化）: RG=$RG ./cicd/scripts/env/cleanup-env.sh"
