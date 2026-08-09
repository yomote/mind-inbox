#!/usr/bin/env bash
# UX judge の採点レポートを検証し、スコアボード Issue へ 1 採点 = 1 コメントで投稿する。
# runbook `docs/runbooks/ux-probe-judge.md` Step 4 の自動化 (ADR 0027 D1 / #154)。
#
# **検証に落ちたら投稿しない。** 蓄積は時系列データなので、壊れた 1 件が混ざると
# トレンド判断がそのぶん狂う。人が転記していたときは目視が検証だったため、
# 無人化にあたって検証を機械へ移す (validate-judge-score.py)。
#
# 使い方:
#   cicd/scripts/ux-probe/post-judge-score.sh <judge レポートのパス>
#
# 終了コード:
#   0 = 投稿した
#   2 = 採点ブロックが無い (judge が rubric の出力ルールに従っていない)
#   3 = 検証に失敗 (投稿していない)
#   1 = 前提不足

set -euo pipefail

REPO="${REPO:-yomote/mind-inbox}"
SCOREBOARD_ISSUE="${SCOREBOARD_ISSUE:-127}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "$@" >&2; }

REPORT="${1:-}"
if [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
  log "使い方: $0 <judge レポートのパス>"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  log "gh CLI が見つかりません。"
  log "  → agent セッションでは gh は使えません (設計上の制約 / #160)。"
  log "     検証だけを 'python3 cicd/scripts/ux-probe/validate-judge-score.py <レポート>' で回し、"
  log "     **終了コード 0 のときだけ** GitHub MCP で Issue #$SCOREBOARD_ISSUE へ投稿してください。"
  log "     検証を飛ばして投稿しないこと (壊れた 1 件が時系列トレンドを狂わせる)。"
  log "  → 手元で回す場合は runbook の Prerequisites を参照してください。"
  exit 1
fi

# 検証 — ここで落ちたら投稿しない。
# 終了コードは必ず**コマンド単体を実行した直後**に取る。`if ! cmd; then rc=$?` と書くと
# $? は `! cmd` という条件式の評価結果 (then に入った時点で常に 0) になり、
# 呼び出し元の無人 Routine が失敗を成功と誤認する (PR #157 レビュー指摘)。
set +e
python3 "$HERE/validate-judge-score.py" "$REPORT" > /dev/null
rc=$?
set -e

if [ "$rc" -ne 0 ]; then
  log ""
  log "投稿を中止しました。レポートは $REPORT に残っています。"
  log "judge の出力が rubric (.github/claude/ux-rubric.md) に従っているか確認してください。"
  exit "$rc"
fi

log "採点ブロックの検証 OK — Issue #$SCOREBOARD_ISSUE へ投稿します"

gh issue comment "$SCOREBOARD_ISSUE" -R "$REPO" -F "$REPORT"

log "投稿しました: https://github.com/$REPO/issues/$SCOREBOARD_ISSUE"
