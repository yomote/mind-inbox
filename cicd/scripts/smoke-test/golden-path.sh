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
# 2026-08-10: ここが `2>/dev/null` で az のエラーを捨てていたため、
# 「デプロイ実行中」「デプロイが存在しない」「権限が無い」がすべて同じ 1 行の NG に見えた。
# 実際に「マージ直後で bicep が実行中 → outputs が空」を実環境の障害と誤診しかけた。
# **取れなかった理由を捨てない**。
DEPLOY_JSON="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" \
  --query '{state: properties.provisioningState, outputs: properties.outputs}' -o json 2>/tmp/deploy-err.txt)"
if [[ -z "$DEPLOY_JSON" ]]; then
  ng "az deployment group show が失敗した: $(head -c 300 /tmp/deploy-err.txt)"
  exit 1
fi
DEPLOY_STATE="$(printf '%s' "$DEPLOY_JSON" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('state') or '')" 2>/dev/null)"
OUTPUTS="$(printf '%s' "$DEPLOY_JSON" | python3 -c \
  "import sys,json; print(json.dumps(json.load(sys.stdin).get('outputs') or {}))" 2>/dev/null)"
_val() { printf '%s' "$OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

FUNC_HOST="$(_val functionAppDefaultHostname)"
SPA_CLIENT_ID="$(_val functionAuthEntraClientId)"
if [[ -z "$FUNC_HOST" || -z "$SPA_CLIENT_ID" ]]; then
  if [[ "$DEPLOY_STATE" == "Running" || "$DEPLOY_STATE" == "Accepted" ]]; then
    # デプロイ中は outputs が空になる。これは「壊れている」ではなく「まだ確かめられない」。
    ng "デプロイが実行中 (provisioningState=$DEPLOY_STATE) のため outputs が空。デプロイ完了後に再実行すること"
  else
    ng "deployment outputs から functionAppDefaultHostname / functionAuthEntraClientId を解決できない (provisioningState=${DEPLOY_STATE:-unknown})"
  fi
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
# stub 応答 (\"[stub] received: ...\") は「AI_AGENT_BASE_URL の結線が外れている」であり、
# 実 AI 検証としては失敗。2026-08-08 に stub がここを緑ですり抜けた実績があるため明示的に落とす
if echo "$body" | grep -q '\[stub\]'; then
  ng "実 AI ではなく stub 応答が返っている。AI_AGENT_BASE_URL の結線が外れている"
elif [[ "$reply_len" -gt 0 ]]; then
  ok "実 AI 応答が返る (assistant text ${reply_len} chars)"
else
  ng "実 AI 応答が返らない: $(echo "$body" | head -c 300)"
fi

echo "== 4. consultation.sendMessage (実 AI 対話) =="
body="$(curl -s -m 180 -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"sessionId":"golden-path-probe","message":"一言だけ返してください"}' "$H/api/trpc/consultation.sendMessage")"
if echo "$body" | grep -q '\[stub\]'; then
  ng "sendMessage が stub 応答。AI_AGENT_BASE_URL の結線が外れている"
elif echo "$body" | grep -q '"reply":"'; then
  ok "sendMessage が reply を返す"
else
  ng "sendMessage が壊れている: $(echo "$body" | head -c 300)"
fi

echo "== 5. tts (実 VOICEVOX 合成) =="
tmp="$(mktemp)"
code="$(curl -s -m 180 -o "$tmp" -w '%{http_code}' -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"text":"ゴールデンパスのテストなのだ","speaker":3}' "$H/api/tts")"
tts_bytes="$(wc -c < "$tmp")"
# サイズ閾値はここが唯一の担当 (E2E 側は blob 消費との競合で body を読めない — #125)。
# 「200 + RIFF ヘッダだが中身がほぼ空」の壊れ方をここで止める
if [[ "$code" == "200" ]] && head -c 4 "$tmp" | grep -q "RIFF" && [[ "$tts_bytes" -gt 1000 ]]; then
  ok "VOICEVOX が WAV を返す (${tts_bytes} bytes)"
elif [[ "$code" == "200" ]]; then
  ng "tts が 200 だが WAV として不正 (${tts_bytes} bytes / RIFF=$(head -c 4 "$tmp" | grep -c RIFF || true)) — 空/破損の疑い"
elif [[ "$code" == "204" ]]; then
  ng "tts が 204 (stub)。VOICEVOX_BASE_URL の結線が外れている"
else
  ng "tts が壊れている (status=$code): $(head -c 120 "$tmp")"
fi
rm -f "$tmp"

# 2026-08-10: PO が実環境で「困りごと抽出がエラーになる」を踏んだ。調べると **抽出は
# どのテストも実環境で通していなかった** — ここは対話と音声で終わっており、UI 込み E2E も
# 抽出ボタンを押しておらず、UC 受け入れテスト (L3-real) は偽 ai-agent + メモリ保存で走る。
# 「実 AI が JSON を返す」「Cosmos に書ける」はこのホップだけが見られる。
echo "== 6. consultation.extract (実 AI の構造化出力) =="
body="$(curl -s -m 300 -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"sessionId":"golden-path-probe","messages":[{"role":"user","text":"最近レビュー待ちが長くて手が止まる。あと会議が多くて集中できない"},{"role":"assistant","text":"どちらがつらいですか"},{"role":"user","text":"レビュー待ちの方がつらい"}]}' \
  "$H/api/trpc/consultation.extract")"
extracted="$(echo "$body" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); print(len(d['result']['data']['items']))
except Exception: print(-1)")"
if [[ "$extracted" -gt 0 ]]; then
  ok "抽出が困りごとを $extracted 件返す"
elif [[ "$extracted" == "0" ]]; then
  ng "抽出が 0 件。実 AI が構造化出力を返せていない疑い: $(echo "$body" | head -c 300)"
else
  ng "抽出が壊れている: $(echo "$body" | head -c 400)"
fi

# problem.list は query なので GET (health.ping と同じ)。POST すると tRPC が
# METHOD_NOT_SUPPORTED を返し、「一覧が壊れている」と誤診する
echo "== 7. problem.list (抽出結果が読み出せるか) =="
body="$(curl -s -m 60 -H "$A" "$H/api/trpc/problem.list")"
listed="$(echo "$body" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); print(len(d['result']['data']))
except Exception: print(-1)")"
if [[ "$listed" -ge 0 ]]; then
  ok "一覧が読める (${listed} 件)"
else
  ng "一覧が壊れている: $(echo "$body" | head -c 400)"
fi

echo ""
echo "== Result =="
if [[ "$FAIL" == "0" ]]; then
  echo "PASS — ゴールデンパス (認証 → 対話 (実 AI) → 音声 → 抽出 → 一覧) が実環境で通っている"
else
  echo "FAIL — ゴールデンパスが壊れている。上の NG 行から壊れたホップを特定すること"
  exit 1
fi
