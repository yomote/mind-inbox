#!/usr/bin/env bash
# 直近の golden-path-monitor 実行から UX プローブ記録 (会話 JSON) を取り出す。
# runbook `docs/runbooks/ux-probe-judge.md` Step 2 の自動化 (ADR 0027 D1 / #154)。
#
# **これは人間が手元で回すための経路** (gh CLI が要る)。agent セッションには GitHub の
# 直接 API の経路が無く gh が使えないため、agent は artifact を GitHub MCP で取得し、
# 取得後の判断は `inspect-probe-artifact.py` を直接呼ぶ (#160 選択肢 B)。
# どちらの経路も「どの JSON を採点するか / 採点する材料があるか」の判断は同じ部品を通る。
#
# 標準出力に**記録 JSON のパスだけ**を出す (採点セッションがそのまま judge に渡せるように)。
# **1 行 1 本で、シナリオの数だけ出る** (#435 で台本が 2 本になった — 1 行目だけ読むと
# 片方のシナリオが毎朝記録されるのに一度も採点されない)。
# 診断メッセージはすべて stderr へ — 混ぜると呼び出し側が壊れる (PR #88 で踏んだ実例)。
#
# 使い方:
#   cicd/scripts/ux-probe/fetch-latest-probe.sh [出力先ディレクトリ]
#
# 終了コード:
#   0 = 記録 JSON を取得できた (パスを stdout に 1 行 1 本で出力)
#   3 = 実行はあったが artifact が無い (プローブ手前で fail した / 全体スキップ)
#   4 = 記録はあるが全シナリオで turns が 0 件 (採点する材料がない)
#   1 = 前提不足・想定外

set -euo pipefail

REPO="${REPO:-yomote/mind-inbox}"
WORKFLOW="${WORKFLOW:-golden-path-monitor.yml}"
OUT_DIR="${1:-/tmp/ux-probe}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "$@" >&2; }

if ! command -v gh >/dev/null 2>&1; then
  log "gh CLI が見つかりません。"
  log "  → agent セッションでは gh は使えません (設計上の制約 / #160)。"
  log "     artifact を GitHub MCP で取得し、展開先を渡して"
  log "     'cicd/scripts/ux-probe/inspect-probe-artifact.py <ディレクトリ>' を呼んでください。"
  log "  → 手元で回す場合は runbook の Prerequisites を参照してください。"
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

# どの JSON を採点するか / 採点する材料があるかの判断は共有部品に委譲する。
# agent 経路 (GitHub MCP で取得) もこの同じスクリプトを呼ぶので、判断が 2 箇所に分裂しない。
#
# 終了コードは必ず**コマンド単体を実行した直後**に取る。`if ! cmd; then rc=$?` と書くと
# $? は `! cmd` という条件式の評価結果 (then に入った時点で常に 0) になり、
# 呼び出し元の無人 Routine が失敗を成功と誤認する (PR #157 レビュー指摘)。
set +e
PROBE_JSON="$(python3 "$HERE/inspect-probe-artifact.py" "$OUT_DIR")"
rc=$?
set -e

if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi

echo "$PROBE_JSON"
