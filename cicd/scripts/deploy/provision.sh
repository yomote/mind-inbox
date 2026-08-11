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
# コンテナ image タグ (#107): :latest 差し替えは ARM 的に no-op で新 image が反映されない。
# CD (deploy.yml) は build-images の直近成功 run から sha-<full-sha> を解決して渡してくる。
# 未指定なら各 deploy スクリプトの既定 (latest) に落ちる（スクリプト側が WARN を出す）。
IMAGE_TAG="${IMAGE_TAG:-}"
# コンテナ image の座標 (PR #261): 既定は deploy-*.sh と同じ ghcr.io/yomote/mind-inbox。
# fork 等で IMAGE_REGISTRY / IMAGE_REPO を差し替えた実行でも、bicep (bootstrap) と
# 子スクリプト (deploy-ai-agent / deploy-voicevox-wrapper) が同じ座標を見るよう
# ここで解決して両方に渡す (ズレると bootstrap が存在しない image の pull で止まる)。
IMAGE_REGISTRY="${IMAGE_REGISTRY:-ghcr.io}"
IMAGE_REPO="${IMAGE_REPO:-yomote/mind-inbox}"
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
# コンテナ image は ghcr の事前ビルド済み（build-images.yml, #67）を差し替えるだけなので docker は不要。
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
# 最終デプロイ時刻を RG タグに記録する（「この環境はいつのものか」を後から追える）。
# 夜間 teardown の最小生存時間ガードで使っていたが、ADR 0013 で自動 teardown 自体を廃止したため
# 現在は記録用途のみ。失敗してもデプロイは続行する。
az group update -n "$RG" --set "tags.deployedAtEpoch=$(date +%s)" -o none >/dev/null 2>&1 \
  || echo "WARN: deployedAtEpoch タグの記録に失敗（デプロイは続行。記録用途のみ）" >&2

echo "==> [2/5] Bootstrap infra (main-bootstrap.bicep) — voicevoxTier=$VOICEVOX_TIER"
# ai-agent / vv-wrap の Container App は #188 で bicep 管理になった。IMAGE_TAG が
# 解決済みなら bicep にも同じ sha タグを渡し、bicep 適用が稼働 image を過去に
# 戻さないようにする (ADR 0025)。未指定なら bicep 既定 (latest) — 直後の
# deploy-*.sh が従来どおり収束させる。
BOOTSTRAP_EXTRA_ARGS=(-p "ghcrImageRepository=${IMAGE_REGISTRY}/${IMAGE_REPO}")
if [[ -n "$IMAGE_TAG" ]]; then
  BOOTSTRAP_EXTRA_ARGS+=(-p "containerImageTag=$IMAGE_TAG")
fi

# ai-agent MI → OpenAI User ロール割り当ての「養子縁組」(#262):
# deploy-ai-agent.sh 時代の az role assignment create は名前を指定せずランダム GUID 名で
# 作っていたため、bicep (PR #261) が guid() 名で同じ principal+role+scope を宣言すると
# ARM が RoleAssignmentExists で毎回拒否する (割り当ての一意性は名前ではなく
# principal+role+scope)。既存の名前を実行時に解決して bicep へ渡し、その名前で宣言させる
# (削除→再作成は Owner 相当の削除権限が要る上、剥奪〜再付与の間に ai-agent が OpenAI を
# 呼べない瞬断が出るため採らない)。
# 初回 (Container App 未作成で principalId が取れない) は既存割り当ても無いので、
# パラメータを渡さず従来どおり guid() で新規作成させる。
# 名前の組み立ては bicep の命名規約 toLower('ca-{env}-{app から -_ を除去}-ai-agent') と同一。
#
# 解決は 2 段構え (PR #278 の 1 段目だけでは 1 回しか効かなかった / #262 再発):
#   1. 一覧から拾う (下)。判定は role_assignment.py の純粋関数 = 大文字小文字を無視する
#   2. それでも空振りしたら、ARM の RoleAssignmentExists が教えてくる ID を使って
#      **1 度だけやり直す** (deploy_bootstrap の retry)。az 側の一過性・綴り揺れ・
#      権限不足で 1 が黙って空になっても、実環境が半日止まらないようにするための保険
APP_CLEAN="$(printf '%s' "$APP_NAME" | tr -d '_-' | tr '[:upper:]' '[:lower:]')"
AI_AGENT_CA_NAME="ca-${ENVIRONMENT}-${APP_CLEAN}-ai-agent"
OPENAI_ACCOUNT_NAME="oai-${ENVIRONMENT}-${APP_CLEAN}"
ROLE_OPENAI_USER="5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
ROLE_HELPER="$DEPLOY_DIR/role_assignment.py"

# az の失敗を握り潰さない (握り潰すと「養子縁組しなかった」と「する必要が無かった」が
# 見分けられず、#262 のように同じ壁に毎回ぶつかる)。stderr はログに出す。
AI_AGENT_PRINCIPAL_ID="$(az containerapp show -g "$RG" -n "$AI_AGENT_CA_NAME" \
  --query 'identity.principalId' -o tsv 2>&1 || true)"
[[ "$AI_AGENT_PRINCIPAL_ID" =~ ^[0-9a-fA-F-]{36}$ ]] || {
  echo "==> ai-agent の principalId を解決できず (${AI_AGENT_PRINCIPAL_ID:-空}) — 養子縁組はスキップ"
  AI_AGENT_PRINCIPAL_ID=""
}
OPENAI_SCOPE="$(az cognitiveservices account show -g "$RG" -n "$OPENAI_ACCOUNT_NAME" \
  --query 'id' -o tsv 2>/dev/null || true)"
if [[ -z "$OPENAI_SCOPE" ]]; then
  echo "==> OpenAI アカウント $OPENAI_ACCOUNT_NAME を解決できず — 養子縁組はスキップ"
fi

ADOPTED_ROLE_ASSIGNMENT_NAME=""
if [[ -n "$AI_AGENT_PRINCIPAL_ID" && -n "$OPENAI_SCOPE" ]]; then
  # --assignee は Graph 解決に依存するので使わない (CD の SP にディレクトリ読み取りは無い)。
  # scope で引いて、principal / role / scope の一致は純粋関数側で見る。
  # stderr は JSON に混ぜない (az は Graph 権限不足の警告を stderr に出すので、
  # 混ぜると出力が JSON として壊れ、また静かな空振りに戻る)。
  ROLE_LIST_ERR="$(mktemp)"
  ROLE_ASSIGNMENTS_JSON="$(az role assignment list --scope "$OPENAI_SCOPE" -o json \
    2>"$ROLE_LIST_ERR" || true)"
  if [[ -s "$ROLE_LIST_ERR" ]]; then
    echo "==> (az role assignment list の出力メッセージ) $(head -c 300 "$ROLE_LIST_ERR" | tr '\n' ' ')"
  fi
  rm -f "$ROLE_LIST_ERR"
  ADOPTED_ROLE_ASSIGNMENT_NAME="$(printf '%s' "$ROLE_ASSIGNMENTS_JSON" \
    | python3 "$ROLE_HELPER" pick \
        --scope "$OPENAI_SCOPE" \
        --principal-id "$AI_AGENT_PRINCIPAL_ID" \
        --role "$ROLE_OPENAI_USER" || true)"
  if [[ -n "$ADOPTED_ROLE_ASSIGNMENT_NAME" ]]; then
    echo "==> 既存ロール割り当てを養子縁組: aiAgentOpenAiRoleAssignmentName=$ADOPTED_ROLE_ASSIGNMENT_NAME"
  else
    echo "==> 一致する既存ロール割り当ては見つからず (新規作成に倒す。衝突したら ARM の応答から拾い直す)"
  fi
fi

# bootstrap を 1 回流す。stderr も含めてログに残しつつ、失敗時は本文を呼び出し元へ返す
# (RoleAssignmentExists の ID を読むため)。
BOOTSTRAP_LOG="$(mktemp)"
trap 'rm -f "$BOOTSTRAP_LOG"' EXIT
deploy_bootstrap() {
  local role_name="$1"
  local args=()
  if [[ ${#BOOTSTRAP_EXTRA_ARGS[@]} -gt 0 ]]; then
    args=("${BOOTSTRAP_EXTRA_ARGS[@]}")
  fi
  if [[ -n "$role_name" ]]; then
    args+=(-p "aiAgentOpenAiRoleAssignmentName=$role_name")
  fi
  local rc=0
  az deployment group create \
    -g "$RG" \
    -n "$DEPLOYMENT" \
    -f "$IAC_DIR/main-bootstrap.bicep" \
    -p @"$IAC_DIR/main-bootstrap.parameters.json" \
    -p appName="$APP_NAME" environmentName="$ENVIRONMENT" voicevoxTier="$VOICEVOX_TIER" \
    ${args[@]+"${args[@]}"} \
    -o none 2>&1 | tee "$BOOTSTRAP_LOG" || rc=$?
  return "$rc"
}

if ! deploy_bootstrap "$ADOPTED_ROLE_ASSIGNMENT_NAME"; then
  CONFLICT_NAME="$(python3 "$ROLE_HELPER" from-error < "$BOOTSTRAP_LOG" || true)"
  if [[ -z "$CONFLICT_NAME" || "$CONFLICT_NAME" == "$ADOPTED_ROLE_ASSIGNMENT_NAME" ]]; then
    echo "::error::bootstrap (main-bootstrap.bicep) が失敗しました。上の ARM エラーを参照してください。" >&2
    exit 1
  fi
  echo "==> RoleAssignmentExists — ARM が返した既存 ID $CONFLICT_NAME を養子縁組して 1 度だけやり直します (#262)"
  if ! deploy_bootstrap "$CONFLICT_NAME"; then
    echo "::error::既存ロール割り当て ($CONFLICT_NAME) を養子縁組しても bootstrap が通りませんでした。上の ARM エラーを参照してください。" >&2
    exit 1
  fi
fi

if [[ "$PRINT_ENTRA_AUTH_HINT" == "true" ]]; then
  echo "==> [hint] Entra 認証(main-config) は UAMI 事前準備が要る。手順は iac/README §3 を参照（ここでは有効化しない）"
fi

# image 座標は bicep に渡したものと同じ値を子スクリプトにも届ける (常に非空なので無条件)。
export IMAGE_REGISTRY IMAGE_REPO
# IMAGE_TAG が来ていれば子スクリプト (deploy-voicevox-wrapper / deploy-ai-agent) に届ける。
# 空のまま export すると子側の既定 (latest) が効かなくなるため、指定時のみ export する。
if [[ -n "$IMAGE_TAG" ]]; then
  export IMAGE_TAG
  echo "==> コンテナ image タグ: $IMAGE_TAG"
else
  echo "==> コンテナ image タグ: (未指定 → 各スクリプト既定の latest。既存 CA の更新は no-op になりうる #107)"
fi

# コンテナは BFF より先に（deploy-backend が wrapper/ai-agent の FQDN を func 設定へ配線するため）。
echo "==> [3/5] VOICEVOX wrapper (Container App)"
RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$DEPLOY_DIR/deploy-voicevox-wrapper.sh"

echo "==> [4/5] AI Agent (Container App)"
RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$DEPLOY_DIR/deploy-ai-agent.sh"

echo "==> [5/5] Backend(BFF) + Frontend(SWA)"
RG="$RG" DEPLOYMENT="$DEPLOYMENT" "$DEPLOY_DIR/deploy-all.sh"

echo
echo "✅ Provision 完了。SWA の URL は上の deploy-frontend 出力を参照。"
echo "   撤収（¥0化）: RG=$RG ./cicd/scripts/env/cleanup-env.sh"
