#!/usr/bin/env bash
# 永続化の実測 (L4) — 「昨日保存したものが今日も残っている」を毎朝確かめる。
#
# 背景 (#165 / ADR 0030 / ADR 0018): Problem は in-memory の module singleton にしか
# 無く、Functions は Y1 (Consumption) なのでアイドルで確実にリサイクルされ、実質翌日には
# 空になっていた。Cosmos DB へ移したあと、それが**本当に効いているか**は
# 「プロセスが落ちるだけの時間を空けて読み直す」ことでしか分からない。
#
# この監視の肝は **run をまたぐこと**:
#   1. 前回 run が残したマーカーが今も見えるか (= 生き延びたか) を検証する
#   2. 次回 run のためのマーカーを新しく保存する
# 毎朝の golden-path-monitor から呼ぶと、24 時間のアイドル (= 確実なリサイクル) を挟んだ
# 実測になる。デプロイ直後のスモークテストでは絶対に捕まらない層。
#
# 見る対象は **Problem** (UC-02〜05 の事前条件そのもの)。ADR 0034 で会話中心モデル
# (history) を撤去したので、マーカーは Problem のタイトルに置く。
#
# **定常状態では AI を呼ばない** — マーカー Problem が既にあれば `problem.list` の
# 読み取りだけで判定が終わる。マーカーが 1 件も無いとき (初回 / データ消去後) だけ、
# 唯一の書き込み経路である `consultation.extract` を 1 回使って種を置く。
#
# 保存先のパーティションは **このスクリプトを実行した主体の userId** (CI なら gha-oidc SP、
# 手動なら実行した人間) になる。PO 本人の履歴を汚さない代わりに、**PO の目に見える
# データが残っているかまでは見ていない** — 見ているのは「BFF ↔ Cosmos の往復が
# プロセスの寿命を越えて成立しているか」。
#
# 初回実行は「前回のマーカーが無い」ので **PASS 扱いで seed だけ置く** (ここだけ AI を 1 回呼ぶ)。
# 2 回目以降が本番の検証になり、以降は読み取りだけで済む。
set -uo pipefail

RG="${RG:-rg-dev-mind-inbox}"
DEPLOYMENT="${DEPLOYMENT:-main-bootstrap}"

# 「前回 run のもの」と認めるまでの最短経過時間 (秒)。同一 run 内で保存したものを
# 読んで「残っていた」と誤判定しないための下限。既定 1 時間 (毎日 1 回の監視なので十分)。
MIN_AGE_SEC="${MIN_AGE_SEC:-3600}"

MARKER_PREFIX="persistence-probe"

FAIL=0
ok() { echo "OK  - $1"; }
ng() {
  echo "NG  - $1"
  FAIL=1
}

echo "== Resolve outputs =="
OUTPUTS="$(az deployment group show -g "$RG" -n "$DEPLOYMENT" --query 'properties.outputs' -o json 2>/dev/null || echo '{}')"
_val() { printf '%s' "$OUTPUTS" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('$1',{}).get('value',''))" 2>/dev/null; }

FUNC_HOST="$(_val functionAppDefaultHostname)"
SPA_CLIENT_ID="$(_val functionAuthEntraClientId)"
COSMOS_ENABLED="$(_val cosmosEnabled)"

if [[ -z "$FUNC_HOST" || -z "$SPA_CLIENT_ID" ]]; then
  ng "deployment outputs から functionAppDefaultHostname / functionAuthEntraClientId を解決できない"
  exit 1
fi

# Cosmos を無効にしてデプロイした環境で赤くしても意味が無い (in-memory が仕様どおり)。
if [[ "$COSMOS_ENABLED" != "True" && "$COSMOS_ENABLED" != "true" ]]; then
  echo "SKIP - cosmosEnabled=$COSMOS_ENABLED — 永続化が無効な環境なのでスキップします"
  exit 0
fi

H="https://$FUNC_HOST"
echo "  BFF: $H / audience: api://$SPA_CLIENT_ID"

echo "== Acquire token =="
TOKEN="$(az account get-access-token --scope "api://$SPA_CLIENT_ID/.default" --query accessToken -o tsv 2>/dev/null)"
if [[ -z "$TOKEN" ]]; then
  ng "トークン取得に失敗 (golden-path.sh と同じ経路 — runbook: container-apps-auth-gate.md)"
  exit 1
fi
A="Authorization: Bearer $TOKEN"

echo "== 1. problem.list で前回 run のマーカーを探す =="
list_body="$(curl -s -m 120 -H "$A" "$H/api/trpc/problem.list")"

verdict="$(MARKER_PREFIX="$MARKER_PREFIX" MIN_AGE_SEC="$MIN_AGE_SEC" python3 -c "
import json, os, sys
from datetime import datetime, timezone

prefix = os.environ['MARKER_PREFIX']
min_age = int(os.environ['MIN_AGE_SEC'])
try:
    payload = json.load(sys.stdin)
    items = payload['result']['data']
except Exception as exc:
    print(f'ERROR\t応答を解釈できません: {exc}')
    raise SystemExit(0)

now = datetime.now(timezone.utc)
markers = []
for item in items:
    if not str(item.get('title', '')).startswith(prefix):
        continue
    try:
        created = datetime.fromisoformat(str(item['createdAt']).replace('Z', '+00:00'))
    except Exception:
        continue
    markers.append((item['id'], item['title'], (now - created).total_seconds()))

if not markers:
    print('SEED\t過去のマーカーが 1 件も無い (初回 / データ消去後)')
    raise SystemExit(0)

old = [m for m in sorted(markers, key=lambda m: -m[2]) if m[2] >= min_age]
if not old:
    youngest = min(age for _, _, age in markers)
    print(f'TOOYOUNG\tマーカーはあるが全て {int(youngest)}s 前より新しい (閾値 {min_age}s)')
    raise SystemExit(0)

mid, title, age = old[0]
print(f'SURVIVED\t{title} (id={mid}) が {int(age / 3600)}h 前から残っている / 総数 {len(markers)}')
" <<<"$list_body")"

kind="${verdict%%$'\t'*}"
detail="${verdict#*$'\t'}"

case "$kind" in
  SURVIVED)
    ok "永続化が効いている — $detail"
    echo "== 2. マーカーは既にある — 書き込みは不要 (AI を呼ばない) =="
    ;;
  SEED | TOOYOUNG)
    if [[ "$kind" == "SEED" ]]; then
      echo "INFO- $detail → 今回は seed だけ置きます (次回 run が本番の検証)"
    else
      # アイドルを挟んでいない (手動連続実行など)。赤くはしない。
      echo "INFO- $detail → 判定を保留します"
    fi
    ;;
  *)
    ng "problem.list を読めない — $detail: $(echo "$list_body" | head -c 200)"
    ;;
esac

# マーカーが無いときだけ種を置く。**唯一の書き込み経路が extract なので AI を 1 回呼ぶ** —
# 定常状態 (SURVIVED) では通らないので、日々のコストはゼロのまま。
if [[ "$kind" == "SEED" ]]; then
  echo "== 2. マーカーを作る (extract を 1 回だけ使う) =="
  STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  MARKER_TITLE="$MARKER_PREFIX $STAMP"

  extract_body="$(curl -s -m 180 -X POST -H "$A" -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json
print(json.dumps({
    'sessionId': 'persistence-probe',
    'messages': [{'role': 'user', 'text': '永続化の実測用マーカーです。これは監視のための固定文です。'}],
}))
")" \
    "$H/api/trpc/consultation.extract")"

  new_id="$(python3 -c "
import json, sys
try:
    items = json.load(sys.stdin)['result']['data']['items']
    print(items[0]['grouping']['problemId'] if items else '')
except Exception:
    print('')
" <<<"$extract_body")"

  if [[ -z "$new_id" ]]; then
    ng "マーカーの種を作れなかった: $(echo "$extract_body" | head -c 300)"
  else
    # AI が付けたタイトルを、次回 run が見つけられる決定的な名前に上書きする。
    rename_body="$(curl -s -m 120 -X POST -H "$A" -H "Content-Type: application/json" \
      -d "$(MARKER_TITLE="$MARKER_TITLE" NEW_ID="$new_id" python3 -c "
import json, os
print(json.dumps({
    'action': 'editTitle',
    'problemId': os.environ['NEW_ID'],
    'title': os.environ['MARKER_TITLE'],
}))
")" \
      "$H/api/trpc/problem.triage")"

    if echo "$rename_body" | grep -q "$MARKER_PREFIX"; then
      ok "マーカーを保存した ($MARKER_TITLE)"
    else
      ng "マーカーの命名に失敗: $(echo "$rename_body" | head -c 300)"
    fi
  fi
fi

echo ""
echo "== Result =="
if [[ "$FAIL" == "0" ]]; then
  echo "PASS — 永続化プローブ"
else
  echo "FAIL — 永続化が壊れている。Cosmos の結線 (COSMOS_ENDPOINT / MI のロール割り当て) を疑うこと"
  exit 1
fi
