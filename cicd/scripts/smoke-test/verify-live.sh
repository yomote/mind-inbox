#!/usr/bin/env bash
# ライブ E2E 検証（CI ランナー上で実行 = egress 制限なし）。
#
# 目的: 「ログイン済み → 対話を開始 → 実 AI が返る」の最後の 1 ホップ
#   （SWA → func(linked backend) → ai-agent → gpt-5-mini）を、人手のログイン無しで
#   自動検証する。func は SWA linked backend で直叩き 401（設計通り）なので、直接は叩けない。
#   そこで SWA の /api/* ゲートを一時的に anonymous にし、公開 URL 経由で consultation.start を
#   叩いて本物の応答を採取 → 直後に認証ゲートを復元する。
#
# 安全策: 復元は trap EXIT で必ず走る（採取失敗・途中エラーでも再ロックする）。開放は dev のみ・
#   数分・実ユーザーデータ無し。復元後に anon 叩きが 401/302 に戻ることも確認する。
set -uo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FE="$ROOT_DIR/apps/frontend"

need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing $1" >&2; exit 1; }; }
need az; need curl; need swa; need pnpm; need python3

SWA_NAME="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query 'properties.outputs.staticSiteName.value' -o tsv 2>/dev/null || true)"
[[ -z "$SWA_NAME" ]] && SWA_NAME="$(az staticwebapp list -g "$RG" --query "[?starts_with(name,'swa-')].name | [0]" -o tsv)"
SWA_HOST="$(az staticwebapp show -g "$RG" -n "$SWA_NAME" --query defaultHostname -o tsv)"
SWA="https://$SWA_HOST"
TOKEN="$(az staticwebapp secrets list -g "$RG" -n "$SWA_NAME" --query 'properties.apiKey' -o tsv)"
[[ -z "$TOKEN" ]] && { echo "ERROR: SWA deployment token を取得できません" >&2; exit 1; }

echo "SWA_NAME=$SWA_NAME"
echo "SWA=$SWA"

REAL_CFG="$FE/public/staticwebapp.config.json"
DIST="$FE/dist"
DIST_CFG="$DIST/staticwebapp.config.json"

anon_post() {
  curl -sS -m 60 -w '\n__HTTP__%{http_code}' -X POST \
    "$SWA/api/trpc/consultation.start?batch=1" \
    -H 'content-type: application/json' \
    -d '{"0":{"concern":"接続テストです。ひとことで返してください。"}}' 2>/dev/null
}

restore() {
  echo ""
  echo "== 復元: 認証ゲートを再ロック =="
  cp "$REAL_CFG" "$DIST_CFG"
  if swa deploy "$DIST" --deployment-token "$TOKEN" --env production >/dev/null 2>&1; then
    echo "  restore deploy: ok"
  else
    echo "  ::error::復元デプロイに失敗。profile=full で up し直して認証ゲートを戻すこと。" >&2
  fi
  sleep 8
  local code
  code="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST "$SWA/api/trpc/consultation.start?batch=1" \
    -H 'content-type: application/json' -d '{"0":{"concern":"x"}}' 2>/dev/null || echo 000)"
  echo "  復元後 anon POST http=$code（401/302 期待）"
}
trap restore EXIT

echo ""
echo "=== 事前 anon チェック（ゲートが閉じているはず: 401/302）==="
pre="$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -X POST "$SWA/api/trpc/consultation.start?batch=1" \
  -H 'content-type: application/json' -d '{"0":{"concern":"x"}}' 2>/dev/null || echo 000)"
echo "  事前 anon POST http=$pre"

echo ""
echo "=== ビルド（real モード）==="
cd "$FE"
pnpm install --frozen-lockfile >/dev/null 2>&1 || pnpm install >/dev/null 2>&1
VITE_USE_MOCK="" pnpm build >/dev/null 2>&1
[[ -f "$DIST_CFG" ]] || { echo "ERROR: dist config 不在" >&2; exit 1; }

echo ""
echo "=== 一時的に /api/* を anonymous 化して配信（検証窓）==="
python3 - "$DIST_CFG" <<'PY'
import json,sys
p=sys.argv[1]
c=json.load(open(p))
# auth プロバイダ定義は残しつつ、ルートのゲート(allowedRoles)を全撤去して API を一時開放。
routes=[r for r in c.get("routes",[]) if "allowedRoles" not in r]
c["routes"]=routes
json.dump(c,open(p,"w"),ensure_ascii=False,indent=2)
print("temp anon config routes:",[r.get("route") for r in routes])
PY
swa deploy "$DIST" --deployment-token "$TOKEN" --env production >/dev/null 2>&1 && echo "  temp anon deploy: ok"

echo ""
echo "=== 公開 URL 経由で実 AI 応答を採取（伝播待ち込み）==="
reply=""
for i in $(seq 1 15); do
  out="$(anon_post)"
  code="${out##*__HTTP__}"
  body="${out%__HTTP__*}"
  echo "  [$i] consultation.start http=$code"
  if [[ "$code" == "200" ]]; then
    reply="$body"
    break
  fi
  sleep 10
done

echo ""
echo "================= 判定 ================="
if [[ -n "$reply" ]]; then
  echo "✅ PASS: SWA→func→ai-agent→gpt-5-mini が公開経路で 200 応答"
  echo "--- 実応答（先頭 900 文字）---"
  printf '%s' "$reply" | head -c 900
  echo ""
else
  echo "❌ FAIL: 公開経路で 200 応答を採取できず（上の http コードを参照）"
fi
echo "======================================="
# trap restore が認証ゲートを戻す
