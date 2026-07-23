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

# ── 高速パス: frontend だけ差し替え（既存 env 前提）────────────────────────────
# フロント（staticwebapp.config.json / UI）だけ直したときに、bootstrap・コンテナ
# ビルド(acr build)・BFF を丸ごとやり直す必要はない。それらが ~20分の主因なので、
# frontend profile では deploy-frontend.sh だけを既存 env に対して回す（~2分）。
# 前提: 既に up 済み（main-bootstrap deployment が RG に存在する）。無ければ通常の
# フル up を促して終わる（ここで bootstrap を勝手に始めない＝意図しない再構築を防ぐ）。
if [[ "$DEPLOY_PROFILE" == "frontend" ]]; then
  echo "==> [frontend-only] 既存 env にフロントだけ再デプロイ（bootstrap/コンテナ/BFF はスキップ）"
  if ! az deployment group show -g "$RG" -n "$DEPLOYMENT" -o none >/dev/null 2>&1; then
    echo "ERROR: '$DEPLOYMENT' deployment が RG '$RG' に見つかりません（env が未 up）。" >&2
    echo "       まず profile=full で up してから frontend で差し替えてください。" >&2
    exit 1
  fi
  RG="$RG" DEPLOYMENT="$DEPLOYMENT" FRONTEND_PROFILE=full "$DEPLOY_DIR/deploy-frontend.sh"
  echo
  echo "✅ Frontend 再デプロイ完了（profile=frontend）。SWA の URL は上の deploy-frontend 出力を参照。"
  exit 0
fi

echo "==> [1/5] Resource group: $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" >/dev/null
# 立ち上げ時刻を RG タグに記録 → 夜間 schedule teardown が「最小生存時間」ガードで参照する。
# 失敗してもデプロイ自体は続行するが、サイレントにはしない（ガードが効かず夜間に即撤収されうるため警告）。
if ! az group update -n "$RG" --set "tags.deployedAtEpoch=$(date +%s)" -o none >/dev/null 2>&1; then
  echo "WARN: deployedAtEpoch タグの記録に失敗。夜間 teardown の最小生存時間ガードが効かず、" >&2
  echo "      立てた直後でも夜間 schedule で撤収される可能性があります。" >&2
fi

# teardown 後に soft-delete で残る OpenAI アカウント / Key Vault を、bootstrap の前に purge する。
# これらが soft-deleted のまま残っていると bootstrap の作成が FlagMustBeSetForRestore /
# VaultAlreadyExists で失敗する（cleanup-env の自動 purge が取りこぼしても up を自己修復する）。
# 名前は bicep の既定命名に一致させる。非致命（無ければ何もしない）。
# bicep は appName から '-' と '_' を除去して命名する（例: mind-box → mindbox）。
# bash パラメータ展開で同じ正規化をする（tr -d '-_' は '-' がオプション扱いされ壊れるため使わない）。
OAI_NAME="oai-${ENVIRONMENT}-${APP_NAME//[-_]/}"
if az cognitiveservices account list-deleted --query "[?name=='$OAI_NAME'] | [0].name" -o tsv 2>/dev/null | grep -q .; then
  echo "==> soft-deleted OpenAI アカウント '$OAI_NAME' を purge（teardown 残骸の自己修復）"
  az cognitiveservices account purge --name "$OAI_NAME" --resource-group "$RG" --location "$LOCATION" -o none 2>&1 | tail -2 || \
    echo "WARN: OpenAI purge に失敗（権限/伝播）。bootstrap が失敗する場合は手動 purge を検討。" >&2
fi
KV_NAME="$(python3 -c "import json;print(json.load(open('$IAC_DIR/main-bootstrap.parameters.json'))['parameters'].get('sqlAdminKeyVaultName',{}).get('value',''))" 2>/dev/null || true)"
if [[ -n "$KV_NAME" ]] && az keyvault list-deleted --query "[?name=='$KV_NAME'] | [0].name" -o tsv 2>/dev/null | grep -q .; then
  echo "==> soft-deleted Key Vault '$KV_NAME' を purge（teardown 残骸の自己修復）"
  az keyvault purge --name "$KV_NAME" -o none 2>&1 | tail -2 || \
    echo "WARN: KV purge に失敗（purge protection の可能性）。" >&2
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

if [[ "$DEPLOY_PROFILE" == "mock" || "$DEPLOY_PROFILE" == "mockvoice" ]]; then
  # mock:      匿名スタンドアロン。bootstrap で出来た SWA に frontend(mockApi) を配信するだけ。
  #            TTS はブラウザ内蔵音声（ずんだもんではない）。BFF/ai-agent/wrapper 不要で最安・最速。
  # mockvoice: 上記に加え VOICEVOX wrapper を配信し、frontend が wrapper を直接叩いてずんだもんで
  #            読み上げる（BFF/認証は無いまま）。engine cold-start で初回だけ数秒待つことがある。
  VITE_VOICEVOX_BASE_URL=""
  if [[ "$DEPLOY_PROFILE" == "mockvoice" ]]; then
    echo "==> [3/4] VOICEVOX wrapper (Container App) — mockvoice"
    RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$DEPLOY_DIR/deploy-voicevox-wrapper.sh"
    VV_WRAP_NAME="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
      --query 'properties.outputs.voicevoxWrapperContainerAppName.value' -o tsv 2>/dev/null || true)"
    VV_FQDN="$(az containerapp show -g "$RG" -n "$VV_WRAP_NAME" \
      --query 'properties.configuration.ingress.fqdn' -o tsv 2>/dev/null || true)"
    if [[ -z "$VV_FQDN" ]]; then
      echo "ERROR: VOICEVOX wrapper の FQDN を取得できませんでした（mockvoice には必須）。" >&2
      exit 1
    fi
    VITE_VOICEVOX_BASE_URL="https://$VV_FQDN"
    echo "==> [4/4] Frontend (SWA, mock standalone + VOICEVOX ずんだもん) — profile=mockvoice"
  else
    echo "==> [3/3] Frontend (SWA, mock standalone) — profile=mock"
  fi
  RG="$RG" DEPLOYMENT="$DEPLOYMENT" FRONTEND_PROFILE=mock \
    VITE_VOICEVOX_BASE_URL="$VITE_VOICEVOX_BASE_URL" "$DEPLOY_DIR/deploy-frontend.sh"
elif [[ "$DEPLOY_PROFILE" == "aiagent" ]]; then
  # stage1（B 前哨）: 実 AI を存在させて /chat が本当に喋るかだけ確認する診断 profile。
  # bootstrap(enableOpenAi/enableAiAgentAca=true で OpenAI アカウント+gpt-5-mini+ai-agent env を作成) の後、
  # ai-agent をデプロイし deploy-ai-agent.sh 内の /chat smoke で実応答を検証する。BFF/frontend は触らない。
  echo "==> [3/3] AI Agent (Container App) + /chat smoke — profile=aiagent（stage1）"
  RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$DEPLOY_DIR/deploy-ai-agent.sh"
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
