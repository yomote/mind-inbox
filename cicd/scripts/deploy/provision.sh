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
BOOTSTRAP_IMAGE_ARGS=()
if [[ -n "$IMAGE_TAG" ]]; then
  BOOTSTRAP_IMAGE_ARGS+=(-p "containerImageTag=$IMAGE_TAG")
fi
az deployment group create \
  -g "$RG" \
  -n "$DEPLOYMENT" \
  -f "$IAC_DIR/main-bootstrap.bicep" \
  -p @"$IAC_DIR/main-bootstrap.parameters.json" \
  -p appName="$APP_NAME" environmentName="$ENVIRONMENT" voicevoxTier="$VOICEVOX_TIER" \
  ${BOOTSTRAP_IMAGE_ARGS[@]+"${BOOTSTRAP_IMAGE_ARGS[@]}"} \
  -o none

if [[ "$PRINT_ENTRA_AUTH_HINT" == "true" ]]; then
  echo "==> [hint] Entra 認証(main-config) は UAMI 事前準備が要る。手順は iac/README §3 を参照（ここでは有効化しない）"
fi

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
