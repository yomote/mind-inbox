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
# ロール割り当ての判定 (decide_role_assignment.py) に使う。GitHub runner には既定で入っている。
need python3

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

# ai-agent MI → OpenAI User ロール割り当ての解決 (#262 / #277)
#
# ARM のロール割り当ての一意性は **名前ではなく principal+role+scope** で決まる。
# deploy-ai-agent.sh 時代の `az role assignment create` は名前を指定せず
# ランダム GUID 名で作っていたため、bicep (PR #261) が guid() 名で同じ三つ組を
# 宣言すると ARM が RoleAssignmentExists でデプロイ全体を拒否する。
# PR #278 が入れた対処は「既存名を実行時に解決して bicep へ渡す (養子縁組)」で、
# 方向は正しい (run 31481914784 は実際に provision が成功した)。
#
# 効かなかった理由は判定ではなく **観測性**: 3 つの az 参照がすべて
# `2>/dev/null || true` で潰されており、「既存が無い」と「参照に失敗した」が
# 区別できなかった。参照が一度でも失敗すると黙って guid() 名に戻り、
# 次の bicep 適用が RoleAssignmentExists で落ちる
# (run 31500334037 / 31500900952。養子縁組の echo はログに 1 行も出ていない)。
#
# ここでは 3 つの参照を ok / absent / error に分類し、判定は純粋関数
# (decide_role_assignment.py — テストあり) に任せる。取りうる結末は 3 つ:
#   create-new … 既存が無い。bicep が guid() 名で作る (パラメータを渡さない)
#   adopt      … 既存があった。その名前を渡して no-op 更新にする
#   skip       … 分からなかった。**推測せず** この run では bicep に宣言させない。
#                incremental デプロイなので既存の割り当ては消えず、[4/5] の
#                deploy-ai-agent.sh の冪等な付与が実体を保証する = デプロイは止めない
#
# 削除→再作成で名前を揃える案は採らない: 削除に Owner 相当の権限が要る上、
# 剥奪〜再付与の間 ai-agent が OpenAI を呼べない瞬断が出る。
#
# 名前の組み立ては bicep の命名規約 toLower('ca-{env}-{app から -_ を除去}-ai-agent') と同一。
APP_CLEAN="$(printf '%s' "$APP_NAME" | tr -d '_-' | tr '[:upper:]' '[:lower:]')"
AI_AGENT_CA_NAME="ca-${ENVIRONMENT}-${APP_CLEAN}-ai-agent"
OPENAI_ACCOUNT_NAME="oai-${ENVIRONMENT}-${APP_CLEAN}"
ROLE_OPENAI_USER="5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"

AZ_LOOKUP_ERR="$(mktemp)"
trap 'rm -f "$AZ_LOOKUP_ERR"' EXIT

# az の参照結果を "status<TAB>value" の 1 行で返す。status は ok / absent / error。
# az は「リソースが無い」を exit code 3 で返すが、拡張によっては 1 + メッセージなので
# stderr の文言でも拾う。**rc を捨てない**のがこの関数の存在理由。
az_lookup() {
  local out rc
  set +e
  out="$("$@" 2>"$AZ_LOOKUP_ERR")"
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    if [[ -n "$out" ]]; then
      printf 'ok\t%s\n' "$out"
    else
      # 成功して空 = 対象が無い (identity 未付与 / 割り当て 0 件)
      printf 'absent\t\n'
    fi
    return 0
  fi
  if [[ $rc -eq 3 ]] || grep -qiE 'ResourceNotFound|was not found|could not be found|does not exist' "$AZ_LOOKUP_ERR"; then
    printf 'absent\t\n'
    return 0
  fi
  printf 'error\t%s\n' "$(tr '\n' ' ' <"$AZ_LOOKUP_ERR" | cut -c1-300)"
}

CA_LOOKUP="$(az_lookup az containerapp show -g "$RG" -n "$AI_AGENT_CA_NAME" \
  --query 'identity.principalId' -o tsv)"
CA_STATUS="${CA_LOOKUP%%$'\t'*}"
CA_PRINCIPAL_ID="${CA_LOOKUP#*$'\t'}"

OAI_LOOKUP="$(az_lookup az cognitiveservices account show -g "$RG" -n "$OPENAI_ACCOUNT_NAME" \
  --query 'id' -o tsv)"
OAI_STATUS="${OAI_LOOKUP%%$'\t'*}"
OPENAI_SCOPE="${OAI_LOOKUP#*$'\t'}"

ROLE_STATUS="absent"
ROLE_NAME=""
if [[ "$CA_STATUS" == "ok" && "$OAI_STATUS" == "ok" ]]; then
  # --assignee / --role は使わない: --assignee は Microsoft Graph 参照を伴い
  # (CD の principal に Graph 権限は無い)、scope の文字列一致は ARM が返す
  # 大文字小文字に左右される。scope 直下の一覧を引いて自前で照合する。
  RA_LOOKUP="$(az_lookup az role assignment list --scope "$OPENAI_SCOPE" \
    --query '[].[name,principalId,roleDefinitionId]' -o tsv)"
  ROLE_STATUS="${RA_LOOKUP%%$'\t'*}"
  if [[ "$ROLE_STATUS" == "ok" ]]; then
    ROLE_NAME="$(printf '%s\n' "${RA_LOOKUP#*$'\t'}" \
      | tr '[:upper:]' '[:lower:]' \
      | awk -v p="$(printf '%s' "$CA_PRINCIPAL_ID" | tr '[:upper:]' '[:lower:]')" \
            -v r="$ROLE_OPENAI_USER" \
            '$2 == p && index($3, r) { print $1; exit }')"
  fi
fi

ROLE_DECISION="$(python3 "$ROOT_DIR/cicd/scripts/deploy/decide_role_assignment.py" \
  "$CA_STATUS" "$OAI_STATUS" "$ROLE_STATUS" "$ROLE_NAME")"
ROLE_ACTION="${ROLE_DECISION%%$'\t'*}"
ROLE_DETAIL="${ROLE_DECISION#*$'\t'}"

case "$ROLE_ACTION" in
  adopt)
    echo "==> ロール割り当て: 既存 $ROLE_DETAIL を養子縁組する (#262)"
    BOOTSTRAP_EXTRA_ARGS+=(-p "aiAgentOpenAiRoleAssignmentName=$ROLE_DETAIL")
    ;;
  create-new)
    echo "==> ロール割り当て: bicep が guid() 名で新規作成する ($ROLE_DETAIL)"
    ;;
  *)
    # 「分からない」を「無い」に丸めない。丸めた結果が #277 の 8 連敗だった。
    echo "::warning::ai-agent の OpenAI ロール割り当てを解決できませんでした ($ROLE_DETAIL)。この run では bicep に宣言させず、[4/5] deploy-ai-agent.sh の冪等な付与に委ねます (incremental デプロイなので既存の割り当ては消えません)。az の権限・一過性障害を確認してください。"
    BOOTSTRAP_EXTRA_ARGS+=(-p "manageAiAgentOpenAiRoleAssignment=false")
    ;;
esac

az deployment group create \
  -g "$RG" \
  -n "$DEPLOYMENT" \
  -f "$IAC_DIR/main-bootstrap.bicep" \
  -p @"$IAC_DIR/main-bootstrap.parameters.json" \
  -p appName="$APP_NAME" environmentName="$ENVIRONMENT" voicevoxTier="$VOICEVOX_TIER" \
  ${BOOTSTRAP_EXTRA_ARGS[@]+"${BOOTSTRAP_EXTRA_ARGS[@]}"} \
  -o none

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
