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

# 2026-08-12 (#310): curl の終了コードを捨てていたため、接続レベルの失敗が
# すべて `status=000` + 空 body に潰れていた。実ログ:
#
#   NG  - tts が壊れている (status=000):        ← コロンの後ろが空
#
# http_code が 000 のとき、**本当の理由は curl の終了コードにしかない**
# (7 接続不可 / 28 タイムアウト / 35 TLS / 52 無応答 / 56 切断 は全部意味が違う)。
# `code="$(curl ...)"` の形だと rc が $(...) の中で捨てられるので、
# 代入の直後に $? を取る。**「壊れている」と「確かめられなかった」を混ぜない** (#262 と同じ根)。
CURL_RC=0
CURL_OUT=""
# 出力を CURL_OUT に、終了コードを CURL_RC に残す。
# **戻り値を標準出力で返してはいけない** — 呼び出し側が `x="$(run_curl ...)"` と書くと
# 関数がコマンド置換のサブシェルで走り、CURL_RC の代入が親に届かない (捨てるのと同じ)。
# 2026-08-12 に最初この形で書いて、#310 のテストがそれを捕まえた。
run_curl() {
  CURL_OUT="$(curl "$@")"
  CURL_RC=$?
}
# 転送が完了しなかったか。**成功判定の前に必ず通す。**
#
# 実 curl は「ヘッダと body の途中まで受信してからタイムアウト / 切断」のとき、
# **rc != 0 と http_code=200 と部分 body を同時に返す** (`-m` は転送全体の上限、
# `%{http_code}` は最後に受け取った応答コード)。この状態で中身だけを見ると、
# 例えば hop 5 は「200 + RIFF + 1500 bytes」で条件を満たしてしまい、
# **壊れているのに OK が出る** — #310 で消していたのが「理由」だったのに対し、
# ここで消えるのは「失敗そのもの」なので、より悪い。
# したがって **rc != 0 なら中身が何であれ成功にしない**。
incomplete() { [[ "$CURL_RC" != "0" ]]; }
# NG 行に付ける診断。**rc の数字だけでは読めない**ので意味も添える
curl_why() {
  case "$CURL_RC" in
    0) printf 'curl rc=0 — 通信は成立したので応答内容の問題' ;;
    6) printf 'curl rc=6 — 名前解決に失敗 (ホスト名が違う / DNS)' ;;
    7) printf 'curl rc=7 — 接続できなかった (到達不能 / 拒否)' ;;
    28) printf 'curl rc=28 — タイムアウト (-m の上限に達した)' ;;
    35) printf 'curl rc=35 — TLS ハンドシェイク失敗' ;;
    52) printf 'curl rc=52 — サーバが何も返さなかった' ;;
    56) printf 'curl rc=56 — 受信中に接続が切れた' ;;
    *) printf 'curl rc=%s' "$CURL_RC" ;;
  esac
}

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
  elif [[ "$DEPLOY_STATE" == "Failed" ]]; then
    # 直近の bicep 適用そのものが失敗している = **ゴールデンパスの故障ではなく deploy の故障**。
    # ここを 1 行にまとめると、自動起票される Issue が「実環境が壊れた」としか読めず、
    # 載ってもいない新コードを疑って時間が溶ける (2026-08-11 の #287 が実例。真因は #262)。
    ng "直近の bootstrap デプロイが失敗している (provisioningState=Failed) — 実環境に新コードは載っておらず、ゴールデンパスの判定自体ができていない。先に deploy workflow の失敗を直すこと (#262)"
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
run_curl -s -o /dev/null -m 60 -w '%{http_code}' -H "$A" "$H/api/trpc/health.ping"; code="$CURL_OUT"
if [[ "$code" == "200" ]] && ! incomplete; then
  ok "health.ping 200"
elif [[ "$code" == "000" ]]; then
  ng "health.ping に到達できていない ($(curl_why))。EasyAuth 以前にネットワークで失敗している"
elif incomplete; then
  ng "health.ping の応答が途中で切れている (status=$code / $(curl_why))"
else
  ng "health.ping が $code (期待 200)。EasyAuth/audience の破損疑い"
fi

echo "== 2. consultation.start 空 concern (AI 非呼び出しの入口) =="
# #241 (ADR 0039 Phase A) で開始時挨拶は撤去された。空 concern の start は
# 「セッションが作られ、会話は空」が正 — assistant メッセージが返ったら
# 挨拶が復活している (仕様退行) ので落とす。
run_curl -s -m 60 -X POST -H "$A" -H "Content-Type: application/json" -d '{"concern":""}' "$H/api/trpc/consultation.start"; body="$CURL_OUT"
start_verdict="$(echo "$body" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)['result']['data']['session']
    assistants = [m for m in d.get('messages', []) if m.get('role') == 'assistant']
    print('ok' if (d.get('id') or d.get('sessionId')) and not assistants else 'regressed' if assistants else 'broken')
except Exception:
    print('broken')")"
if incomplete; then start_verdict="incomplete"; fi
case "$start_verdict" in
  ok) ok "空 concern でセッションが作られ、挨拶なし (#241 の仕様どおり)" ;;
  incomplete) ng "空 concern の開始が途中で切れている ($(curl_why))" ;;
  regressed) ng "撤去したはずの開始時挨拶が返っている (#241 の仕様退行): $(echo "$body" | head -c 200)" ;;
  *) ng "空 concern の開始が壊れている [$(curl_why)]: $(echo "$body" | head -c 200)" ;;
esac

echo "== 3. consultation.start concern あり (実 AI) =="
run_curl -s -m 180 -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"concern":"ゴールデンパス実測です。今の調子を一言で教えてください"}' "$H/api/trpc/consultation.start"
body="$CURL_OUT"
reply_len="$(echo "$body" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); ms=d['result']['data']['session']['messages']
  print(len([m for m in ms if m['role']=='assistant'][0]['text']))
except Exception: print(0)")"
# stub 応答 (\"[stub] received: ...\") は「AI_AGENT_BASE_URL の結線が外れている」であり、
# 実 AI 検証としては失敗。2026-08-08 に stub がここを緑ですり抜けた実績があるため明示的に落とす
if incomplete; then
  ng "実 AI 応答が途中で切れている ($(curl_why) / ${#body} bytes 受信)"
elif echo "$body" | grep -q '\[stub\]'; then
  ng "実 AI ではなく stub 応答が返っている。AI_AGENT_BASE_URL の結線が外れている"
elif [[ "$reply_len" -gt 0 ]]; then
  ok "実 AI 応答が返る (assistant text ${reply_len} chars)"
else
  ng "実 AI 応答が返らない [$(curl_why)]: $(echo "$body" | head -c 300)"
fi

echo "== 4. consultation.sendMessage (実 AI 対話) =="
run_curl -s -m 180 -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"sessionId":"golden-path-probe","message":"一言だけ返してください"}' "$H/api/trpc/consultation.sendMessage"
body="$CURL_OUT"
if incomplete; then
  ng "sendMessage の応答が途中で切れている ($(curl_why) / ${#body} bytes 受信)"
elif echo "$body" | grep -q '\[stub\]'; then
  ng "sendMessage が stub 応答。AI_AGENT_BASE_URL の結線が外れている"
elif echo "$body" | grep -q '"reply":"'; then
  ok "sendMessage が reply を返す"
else
  ng "sendMessage が壊れている [$(curl_why)]: $(echo "$body" | head -c 300)"
fi

echo "== 5. tts (実 VOICEVOX 合成) =="
tmp="$(mktemp)"
run_curl -s -m 180 -o "$tmp" -w '%{http_code}' -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"text":"ゴールデンパスのテストなのだ","speaker":3}' "$H/api/tts"
code="$CURL_OUT"
tts_bytes="$(wc -c < "$tmp")"
# サイズ閾値はここが唯一の担当 (E2E 側は blob 消費との競合で body を読めない — #125)。
# 「200 + RIFF ヘッダだが中身がほぼ空」の壊れ方をここで止める
if incomplete && [[ "$code" != "000" ]]; then
  # **ここが一番危ない**: 部分 WAV は RIFF ヘッダを持ちサイズ条件も満たしうるので、
  # rc を見ないと「壊れた音声」を OK と報告してしまう
  ng "tts の応答が途中で切れている (status=$code / $(curl_why) / ${tts_bytes} bytes 受信) — WAV として読めても転送が完了していない"
elif [[ "$code" == "200" ]] && ! incomplete && head -c 4 "$tmp" | grep -q "RIFF" && [[ "$tts_bytes" -gt 1000 ]]; then
  ok "VOICEVOX が WAV を返す (${tts_bytes} bytes)"
elif [[ "$code" == "200" ]]; then
  ng "tts が 200 だが WAV として不正 (${tts_bytes} bytes / RIFF=$(head -c 4 "$tmp" | grep -c RIFF || true)) — 空/破損の疑い"
elif [[ "$code" == "204" ]]; then
  ng "tts が 204 (stub)。VOICEVOX_BASE_URL の結線が外れている"
elif [[ "$code" == "000" ]]; then
  # ここが #310 の実害そのもの。応答が無いので body には何も無く、rc だけが理由を持つ。
  # -m 180 に届かず落ちているなら 52 / 56 = 途中切断で、コールドスタートとの相関を疑える
  ng "tts が応答を返していない ($(curl_why))。HTTP 応答が成立していないので body は空 — status だけでは切り分けられない"
else
  ng "tts が壊れている (status=$code / $(curl_why)): $(head -c 120 "$tmp")"
fi
rm -f "$tmp"

# 2026-08-10: PO が実環境で「困りごと抽出がエラーになる」を踏んだ。調べると **抽出は
# どのテストも実環境で通していなかった** — ここは対話と音声で終わっており、UI 込み E2E も
# 抽出ボタンを押しておらず、UC 受け入れテスト (L3-real) は偽 ai-agent + メモリ保存で走る。
# 「実 AI が JSON を返す」「Cosmos に書ける」はこのホップだけが見られる。
echo "== 6. consultation.extract (実 AI の構造化出力) =="
run_curl -s -m 300 -X POST -H "$A" -H "Content-Type: application/json" \
  -d '{"sessionId":"golden-path-probe","messages":[{"role":"user","text":"最近レビュー待ちが長くて手が止まる。あと会議が多くて集中できない"},{"role":"assistant","text":"どちらがつらいですか"},{"role":"user","text":"レビュー待ちの方がつらい"}]}' \
  "$H/api/trpc/consultation.extract"
body="$CURL_OUT"
extracted="$(echo "$body" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); print(len(d['result']['data']['items']))
except Exception: print(-1)")"
if incomplete; then
  ng "抽出の応答が途中で切れている ($(curl_why) / ${#body} bytes 受信)"
elif [[ "$extracted" -gt 0 ]]; then
  ok "抽出が困りごとを $extracted 件返す"
elif [[ "$extracted" == "0" ]]; then
  ng "抽出が 0 件。実 AI が構造化出力を返せていない疑い: $(echo "$body" | head -c 300)"
else
  ng "抽出が壊れている [$(curl_why)]: $(echo "$body" | head -c 400)"
fi

# problem.list は query なので GET (health.ping と同じ)。POST すると tRPC が
# METHOD_NOT_SUPPORTED を返し、「一覧が壊れている」と誤診する
echo "== 7. problem.list (抽出結果が読み出せるか) =="
run_curl -s -m 60 -H "$A" "$H/api/trpc/problem.list"; body="$CURL_OUT"
listed="$(echo "$body" | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); print(len(d['result']['data']))
except Exception: print(-1)")"
if incomplete; then
  ng "一覧の応答が途中で切れている ($(curl_why) / ${#body} bytes 受信)"
elif [[ "$listed" -ge 0 ]]; then
  ok "一覧が読める (${listed} 件)"
else
  ng "一覧が壊れている [$(curl_why)]: $(echo "$body" | head -c 400)"
fi

echo ""
echo "== Result =="
if [[ "$FAIL" == "0" ]]; then
  echo "PASS — ゴールデンパス (認証 → 対話 (実 AI) → 音声 → 抽出 → 一覧) が実環境で通っている"
else
  echo "FAIL — ゴールデンパスが壊れている。上の NG 行から壊れたホップを特定すること"
  exit 1
fi
