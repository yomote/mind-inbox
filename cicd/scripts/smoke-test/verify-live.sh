#!/usr/bin/env bash
# ライブ E2E 検証（CI ランナー上で実行 = egress 制限なし）。
#
# 目的: 「ログイン済み → 対話を開始 → 実 AI が返る」の最後の 1 ホップ
#   （SWA → func(linked backend) → ai-agent → gpt-5-mini）を、人手のログイン無しで
#   自動検証する。func は SWA linked backend で直叩き 401（設計通り）なので直接は叩けない。
#   そこで SWA の /api/* ゲートを一時的に anonymous にし、公開 URL 経由で consultation.start を
#   叩いて本物の応答を採取 → 直後に認証ゲートを復元する。
#
# 実装方針: ビルド/配信は実績のある deploy-frontend.sh（full）に委譲し、その前後で
#   public/staticwebapp.config.json を「匿名版 ↔ 本来の認証版」に差し替えるだけにする。
#   （自前ビルドは BFF の共有型 install を忘れて tsc -b が落ちるため使わない）。
#
# 安全策: 復元は trap EXIT で必ず走る（採取失敗・途中エラーでも認証版を再配信）。開放は dev のみ・
#   数分・実ユーザーデータ無し。復元後に anon 叩きが 401/302 へ戻ることも確認する。
set -uo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FE="$ROOT_DIR/apps/frontend"
DEPLOY_DIR="$ROOT_DIR/cicd/scripts/deploy"
PUB="$FE/public/staticwebapp.config.json"

need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing $1" >&2; exit 1; }; }
need az; need curl; need swa; need pnpm; need python3; need git

SWA_NAME="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query 'properties.outputs.staticSiteName.value' -o tsv 2>/dev/null || true)"
[[ -z "$SWA_NAME" ]] && SWA_NAME="$(az staticwebapp list -g "$RG" --query "[?starts_with(name,'swa-')].name | [0]" -o tsv)"
SWA_HOST="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" --query defaultHostname -o tsv)"
SWA="https://$SWA_HOST"
echo "SWA_NAME=$SWA_NAME"
echo "SWA=$SWA"

anon_post() {
  curl -sS -m 60 -w '\n__HTTP__%{http_code}' -X POST \
    "$SWA/api/trpc/consultation.start?batch=1" \
    -H 'content-type: application/json' \
    -d '{"0":{"concern":"接続テストです。ひとことで返してください。"}}' 2>/dev/null
}
anon_code() {
  curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST "$SWA/api/trpc/consultation.start?batch=1" \
    -H 'content-type: application/json' -d '{"0":{"concern":"x"}}' 2>/dev/null || echo 000
}

restore() {
  echo ""
  echo "== 復元: 認証版 config を再配信して再ロック =="
  git -C "$ROOT_DIR" checkout -- apps/frontend/public/staticwebapp.config.json 2>/dev/null || true
  if RG="$RG" DEPLOYMENT="$DEPLOYMENT" FRONTEND_PROFILE=full "$DEPLOY_DIR/deploy-frontend.sh"; then
    echo "  restore deploy: ok"
  else
    echo "::error::復元デプロイに失敗。profile=full で up し直して認証ゲートを戻すこと。"
  fi
  sleep 8
  echo "  復元後 anon POST http=$(anon_code)（401/302 期待）"
}
trap restore EXIT

echo ""
echo "=== 事前 anon チェック（ゲートが閉じているはず: 401/302）==="
echo "  事前 anon POST http=$(anon_code)"

echo ""
echo "=== /api/* を一時 anonymous 化した config を配信（検証窓を開く）==="
# 認証ブロックもゲート(allowedRoles)も外した最小 config。deploy-frontend(full) は
# <TENANT_ID> があれば sed し、無ければ no-op（残存チェックも通る）。
cat > "$PUB" <<'JSON'
{
  "navigationFallback": { "rewrite": "/index.html" }
}
JSON
RG="$RG" DEPLOYMENT="$DEPLOYMENT" FRONTEND_PROFILE=full "$DEPLOY_DIR/deploy-frontend.sh"

echo ""
echo "=== 公開 URL 経由で実 AI 応答を採取（伝播待ち込み）==="
reply=""
for i in $(seq 1 15); do
  out="$(anon_post)"
  code="${out##*__HTTP__}"
  body="${out%__HTTP__*}"
  echo "  [$i] consultation.start http=$code"
  if [[ "$code" == "200" ]]; then reply="$body"; break; fi
  sleep 10
done

echo ""
echo "================= 判定 ================="
if [[ -n "$reply" ]]; then
  echo "✅ PASS: SWA→func→ai-agent→gpt-5-mini が公開経路で 200 応答"
  echo "--- 実応答（先頭 900 文字）---"
  printf '%s' "$reply" | head -c 900
  echo ""
  RESULT=0
else
  echo "❌ FAIL: 公開経路で 200 応答を採取できず（上の http コードを参照）"
  RESULT=1
fi
echo "======================================="
# trap restore が認証ゲートを戻す。判定結果を job の成否に反映。
exit "$RESULT"
