#!/usr/bin/env bash
# UX judge の採点レポートを検証し、データブランチ data/ux-observations へ
# 1 採点 = 1 行 (kind: ux-judge-score) で追記する。
# runbook `docs/runbooks/ux-probe-judge.md` Step 4 の自動化 (ADR 0027 D1 / ADR 0041 D4)。
# 旧実装は Issue #127 へのコメント投稿 (gh 必須 = 人間の手元専用) だった。
# git push は agent セッションからも通るため、human / agent の別なくこの 1 本で投稿できる。
#
# **検証に落ちたら追記しない。** 蓄積は時系列データなので、壊れた 1 件が混ざると
# トレンド判断がそのぶん狂う。人が転記していたときは目視が検証だったため、
# 無人化にあたって検証を機械へ移す (validate-judge-score.py)。
#
# 使い方:
#   cicd/scripts/ux-probe/post-judge-score.sh <judge レポートのパス>
#
# 終了コード:
#   0 = 追記した
#   2 = 採点ブロックが無い (judge が rubric の出力ルールに従っていない)
#   3 = 検証に失敗 (追記していない)
#   1 = 前提不足 (git push の失敗を含む)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "$@" >&2; }

REPORT="${1:-}"
if [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
  log "使い方: $0 <judge レポートのパス>"
  exit 1
fi

# 検証 — ここで落ちたら追記しない。妥当なら正規化済みの採点 JSON が stdout に出る。
# 終了コードは必ず**コマンド単体を実行した直後**に取る。`if ! cmd; then rc=$?` と書くと
# $? は `! cmd` という条件式の評価結果 (then に入った時点で常に 0) になり、
# 呼び出し元が失敗を成功と誤認する (PR #157 レビュー指摘)。
set +e
SCORE_JSON="$(python3 "$HERE/validate-judge-score.py" "$REPORT")"
rc=$?
set -e

if [ "$rc" -ne 0 ]; then
  log ""
  log "追記を中止しました。レポートは $REPORT に残っています。"
  log "judge の出力が rubric (.github/claude/ux-rubric.md) に従っているか確認してください。"
  exit "$rc"
fi

log "採点ブロックの検証 OK — データブランチへ追記します"

# payload = 正規化済み採点 + recordedAt (鮮度・月振り分けの基準) + レポート全文。
# レポート本文も同じ行に持たせる — 白眉/最悪往復などの根拠が採点と一緒に残り、
# 置き場が 1 つで済む (ADR 0041 D2)
PAYLOAD="$(mktemp)"
SCORE_JSON="$SCORE_JSON" REPORT_PATH="$REPORT" python3 - "$PAYLOAD" <<'EOF'
import json
import os
import sys
from datetime import datetime, timezone

score = json.loads(os.environ["SCORE_JSON"])
with open(os.environ["REPORT_PATH"], encoding="utf-8") as f:
    score["report"] = f.read()
score["recordedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    f.write(json.dumps(score, ensure_ascii=False))
EOF

# 成功表示は push が通った後だけ (#466)。`set -e` に頼らず終了コードを明示的に見る —
# 上の validate と同じ理由で、判定を `if ! cmd` に書き換えられても失敗を拾えるようにする。
set +e
"$HERE/../ux-data/append-observation.sh" "$PAYLOAD"
rc=$?
set -e

if [ "$rc" -ne 0 ]; then
  log ""
  log "データブランチへの追記に失敗しました (exit $rc) — 採点は載っていません。"
  log "レポートは $REPORT に残っています。原因を直して再実行してください。"
  exit 1
fi

log "追記しました: data/ux-observations の evals/ (kind: ux-judge-score)"
