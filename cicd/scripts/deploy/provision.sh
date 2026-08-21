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
# （cicd/CLAUDE.md「リソース命名」の {resourcetype}-{env}-{appname} は bicep が作る“中身のリソース名”の話で、
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
# deploy-*.sh が bicep の outputs (JSON) を読むのに使う。GitHub runner には既定で入っている。
need python3

az account show >/dev/null

echo "==> [1/5] Resource group: $RG ($LOCATION)"
# CD の SP は **RG スコープ**の権限しか持たない (#46)。RG の作成はサブスクリプション
# レベルの書き込みなので、常設 dev (ADR 0013) の日常経路では **呼ばない**。
# 既存なら何もしない = CD がサブスクリプションスコープを要求しない、が要点。
#
# RG が無い場合だけ作成を試みる: device-code の人間セッション (Owner 相当) から
# 初回構築するときはここで作れた方が早い。CD から実行された場合は権限が無いので
# ここで落ちるが、それは「RG が消えている」という異常であって、黙って RG を
# 作り直すべき状況ではない (原因を見ずに再構築すると別の事故を隠す)。
if az group show -n "$RG" -o none 2>/dev/null; then
  echo "    既存の RG を再利用 (作成は行わない)"
else
  echo "    RG が存在しないので作成を試みます"
  az group create -n "$RG" -l "$LOCATION" >/dev/null || {
    echo "ERROR: RG '$RG' が存在せず、作成もできませんでした。" >&2
    echo "       CD の SP は RG スコープの権限しか持ちません (#46)。RG が消えている場合は" >&2
    echo "       人間が device-code セッションで作り直してください:" >&2
    echo "         RG=$RG ./cicd/scripts/cloud-env/setup-oidc.sh" >&2
    echo "       (手順: docs/runbooks/azure-oidc-cd-setup.md)" >&2
    exit 1
  }
fi
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

# ロール割り当ての持ち主は bicep 1 本 (#297)。ここでは何も作らず・何も渡さない。
# 以前は既存割り当ての「養子縁組」(既存名を実行時に解決して bicep へ渡す) をしていたが、
# 「渡し続けないと再発する」恒久的な依存を生み、実際に渡し損ねて #262 が再発した。
# 正しい形は「古い手動割り当てを 1 回削除し、bicep が自分の決定的な名前 (guid()) で作る」
# = 以後は宣言と実体が常に一致し、パラメータの受け渡し自体が要らない。
# 削除には Owner 相当の権限が要るため人手 (Issue #297) — それが済むまでは、下の
# RoleAssignmentExists 検出が「何をすればいいか」を名指しで出す。
echo "==> ロール割り当ては bicep が宣言する (シェルからは作らない / #297)"

# bootstrap を流す。stderr も含めてログに残し、失敗の型を読めるようにする。
BOOTSTRAP_LOG="$(mktemp)"
trap 'rm -f "$BOOTSTRAP_LOG"' EXIT
rc=0
az deployment group create \
  -g "$RG" \
  -n "$DEPLOYMENT" \
  -f "$IAC_DIR/main-bootstrap.bicep" \
  -p @"$IAC_DIR/main-bootstrap.parameters.json" \
  -p appName="$APP_NAME" environmentName="$ENVIRONMENT" voicevoxTier="$VOICEVOX_TIER" \
  ${BOOTSTRAP_EXTRA_ARGS[@]+"${BOOTSTRAP_EXTRA_ARGS[@]}"} \
  -o none 2>&1 | tee "$BOOTSTRAP_LOG" || rc=$?

if [[ "$rc" -ne 0 ]]; then
  if grep -q "RoleAssignmentExists" "$BOOTSTRAP_LOG"; then
    echo "::error::RoleAssignmentExists — bicep が宣言する割り当てと同じ (principal+role+scope) が別名で既に存在します。" >&2
    echo "         スクリプト時代に作られた手動割り当ての残骸です。**1 回だけ手で削除**してください (Issue #297):" >&2
    echo "           az role assignment delete --ids <上のエラーが示す割り当て ID>" >&2
    echo "         削除後は bicep が guid() の決定的な名前で作り直し、以後この失敗は起きません。" >&2
  else
    echo "::error::bootstrap (main-bootstrap.bicep) が失敗しました。上の ARM エラーを参照してください。" >&2
  fi
  exit 1
fi
echo "==> bootstrap 完了 (main-bootstrap)"

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
