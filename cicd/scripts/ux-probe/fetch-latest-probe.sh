#!/usr/bin/env bash
# 直近の golden-path-monitor 実行から UX プローブ記録 (会話 JSON) を取り出す。
# runbook `docs/runbooks/ux-probe-judge.md` Step 2 の自動化 (ADR 0027 D1 / #154)。
#
# 標準出力に**記録 JSON のパスだけ**を出す (採点セッションがそのまま judge に渡せるように)。
# 診断メッセージはすべて stderr へ — 混ぜると呼び出し側が壊れる (PR #88 で踏んだ実例)。
#
# 使い方:
#   cicd/scripts/ux-probe/fetch-latest-probe.sh [出力先ディレクトリ]
#
# 終了コード:
#   0 = 記録 JSON を取得できた (パスを stdout に出力)
#   3 = 実行はあったが artifact が無い (プローブ手前で fail した / 全体スキップ)
#   4 = 記録はあるが turns が 0 件 (採点する材料がない)
#   1 = 前提不足・想定外

set -euo pipefail

REPO="${REPO:-yomote/mind-inbox}"
WORKFLOW="${WORKFLOW:-golden-path-monitor.yml}"
OUT_DIR="${1:-/tmp/ux-probe}"

log() { echo "$@" >&2; }

if ! command -v gh >/dev/null 2>&1; then
  log "gh CLI が見つかりません。runbook の Prerequisites を参照してください。"
  exit 1
fi

RUN_ID="$(gh run list -R "$REPO" -w "$WORKFLOW" -L 1 --json databaseId -q '.[0].databaseId')"
if [ -z "$RUN_ID" ]; then
  log "golden-path-monitor の実行履歴が見つかりません。"
  exit 1
fi
log "対象 run: $RUN_ID"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

if ! gh run download "$RUN_ID" -R "$REPO" -n "ux-probe-$RUN_ID" -D "$OUT_DIR" 2>/dev/null; then
  log "artifact 'ux-probe-$RUN_ID' がありません。"
  log "  → プローブ手前 (curl 版 golden-path / 結線カナリア) で fail したか、"
  log "     AZURE_* variables 未設定で全体がスキップされた可能性があります。"
  log "  → run の step summary / NG 行でホップを特定してください (プローブ自体の問題ではない)。"
  exit 3
fi

# 同 run に複数あれば最新のものを採る
PROBE_JSON="$(find "$OUT_DIR" -name '*.json' -type f | sort | tail -1)"
if [ -z "$PROBE_JSON" ]; then
  log "artifact は取得できましたが JSON がありません: $OUT_DIR"
  exit 3
fi

# 完了往復数を診断として出す。turns が計画未満でも採点は可能なので落とさない
# (runbook「記録はあるが turns が 4 件未満」— 壊れる直前までは残っている)。
N_TURNS="$(python3 -c "
import json, sys
rec = json.load(open('$PROBE_JSON'))
sc = rec.get('scenario', {})
n = len(rec.get('turns', []))
print(f\"scenario={sc.get('id','?')} turns={n}/{sc.get('plannedTurns','?')} probeId={rec.get('probeId','?')}\", file=sys.stderr)
print(n)
")"
log "記録: $PROBE_JSON"

if [ "$N_TURNS" -eq 0 ]; then
  log "turns が 0 件です — 採点する材料がありません。"
  exit 4
fi

echo "$PROBE_JSON"
