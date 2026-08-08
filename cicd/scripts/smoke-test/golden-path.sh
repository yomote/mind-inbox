#!/usr/bin/env bash
# ゴールデンパス実測 (L4) — 「相談ユースケースが実環境で通ること」を認証込みで検証する。
#
# 背景 (#108 / ADR 0018): 2026-08-07〜08 に「自動テスト全緑のままユーザー操作で発覚」する
# 障害が 5 連発した (SP 未作成ログインループ / 空 concern 拒否 / :latest no-op / IP 403 /
# TTS オリジン違い)。共通構造は「認証済みユーザーとしてユースケースを通す検証の不在」。
# このスクリプトは実トークンで BFF を叩き、対話 (実 AI) と音声合成 (実 VOICEVOX) が
# 返ることを毎回実測する。
#
# トークン: 実行主体の az ログイン識別で取得する。
#   - CI: gha-oidc SP (client credentials)。SPA アプリ登録の app role
#     `GoldenPath.Probe` を割り当て済み (runbook: container-apps-auth-gate.md)
#   - 手動: az login した人間の delegated トークン
# どちらも aud = SPA client ID となり、Functions EasyAuth の allowedAudiences を通る。
#
# コスト: 実 AI 呼び出し 2 回 + VOICEVOX 合成 1 回 (数円/実行)。
set -uo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"

FAIL=0
ok() { echo "OK  - $1"; }
ng() { echo "NG  - $1"; FAIL=1; }

echo "== Resolve outputs =="
OUTPUTS="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query 'properties.outputs' -o json 2>/dev/null || echo '{}')"
_val() { printf '%s' "$OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

FUNC_HOST="$(_val functionAppDefaultHostname)"
SPA_CLIENT_ID="$(_val functionAuthEntraClientId)"
if [[ -z "$FUNC_HOST" || -z "$SPA_CLIENT_ID" ]]; then
  ng "deployment outputs から functionAppDefaultHostname / functionAuthEntraClientId を解決できない"
  exit 1
fi
H="https://$FUNC_HOST"
echo "  BFF: $H / audience: api://$SPA_CLIENT_ID"

echo "== Acquire token =="
TOKEN="$(az account get-access-token --scope "api://$SPA_CLIENT_ID/.default" --query accessToken -o tsv 2>/dev/null)"
if [[ -z "$TOKEN" ]]; then
  ng "トークン取得に失敗。CI なら gha-oidc SP に app role (GoldenPath.Probe) が割り当て済みか確認 (runbook: container-apps-auth-gate.md)"
  exit 1
fi
ok "トークン取得 (${#TOKEN} chars)"
A="Authorization: Bearer $TOKEN"

echo "== 1. health.ping (認証チェーン) =="
code="$(curl -s -o /dev/null -m 60 -w '%{http_code}' -H "$A" "$H/api/trpc/health.ping")"
[[ "$code" == "200" ]] && ok "health.ping 200" || ng "health.ping が $code (期待 200)。EasyAuth/audience の破損疑い"

echo "== 2. consultation.start 空 concern (AI 非呼び出しの入口) =="
body="$(curl -s -m 60 -X POST -H "$A" -H "Content-Type: application/json" -d '{"concern":""}' "$H/api/trpc/consultation.start")"
echo "$body" | grep -q '"role":"assistant"' \
  && ok "空 concern で opener が返る" \
  || ng "空 concern の開始が壊れている: $(echo "$body" | head -c 200)"

echo "== 3. consultation.start concern あり (実 AI) =="
body="$(curl -s -m 180 -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"concern":"ゴールデンパス実測です。今の調子を一言で教えてください"}' "$H/api/trpc/consultation.start")"
reply_len="$(echo "$body" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); ms=d['result']['data']['session']['messages']
  print(len([m for m in ms if m['role']=='assistant'][0]['text']))
except Exception: print(0)")"
[[ "$reply_len" -gt 0 ]] \
  && ok "実 AI 応答が返る (assistant text ${reply_len} chars)" \
  || ng "実 AI 応答が返らない: $(echo "$body" | head -c 300)"

echo "== 4. consultation.sendMessage (実 AI 対話) =="
body="$(curl -s -m 180 -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"sessionId":"golden-path-probe","message":"一言だけ返してください"}' "$H/api/trpc/consultation.sendMessage")"
echo "$body" | grep -q '"reply":"' \
  && ok "sendMessage が reply を返す" \
  || ng "sendMessage が壊れている: $(echo "$body" | head -c 300)"

echo "== 5. tts (実 VOICEVOX 合成) =="
tmp="$(mktemp)"
code="$(curl -s -m 180 -o "$tmp" -w '%{http_code}' -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"text":"ゴールデンパスのテストなのだ","speaker":3}' "$H/api/tts")"
if [[ "$code" == "200" ]] && head -c 4 "$tmp" | grep -q "RIFF"; then
  ok "VOICEVOX が WAV を返す ($(wc -c < "$tmp") bytes)"
elif [[ "$code" == "204" ]]; then
  ng "tts が 204 (stub)。VOICEVOX_BASE_URL の結線が外れている"
else
  ng "tts が壊れている (status=$code): $(head -c 120 "$tmp")"
fi
rm -f "$tmp"

echo ""
echo "== Result =="
if [[ "$FAIL" == "0" ]]; then
  echo "PASS — ゴールデンパス (認証 → 対話 (実 AI) → 音声) が実環境で通っている"
else
  echo "FAIL — ゴールデンパスが壊れている。上の NG 行から壊れたホップを特定すること"
  exit 1
fi
