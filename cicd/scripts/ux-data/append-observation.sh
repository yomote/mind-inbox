#!/usr/bin/env bash
# UX 観測 payload をデータブランチ data/ux-observations に追記して push する (ADR 0040)。
#
# golden-path-monitor / ux-eval.yml / PM tick (post-judge-score.sh) の 3 経路が
# 全部これを通る。git 操作をここに集約し、ファイル操作は append.py に委ねる。
#
# - ブランチが無ければ orphan で作る (main と履歴を共有しない — ADR 0040 D1)
# - push が競合したら (同時書き込み)、fetch からやり直して最大 3 回リトライする。
#   追記は JSONL の append なので、最新の先端から append し直せば必ず合流できる
# - 呼び出し元の checkout には触らない (使い捨ての worktree + 一時ブランチで作業)
#
# 使い方:
#   append-observation.sh <payload.json> [<payload.json>...]
#     payload は kind / recordedAt を持つ 1 観測の JSON (形の真実は append.py)
#
# 環境変数:
#   UX_DATA_BRANCH  (既定 data/ux-observations)
#   UX_DATA_REMOTE  (既定 origin)
#   GIT_COMMIT_NAME / GIT_COMMIT_EMAIL (既定 github-actions[bot])
#
# 終了コード: 0 = push まで完了 / 1 = 前提不足・リトライ上限

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRANCH="${UX_DATA_BRANCH:-data/ux-observations}"
REMOTE="${UX_DATA_REMOTE:-origin}"
COMMIT_NAME="${GIT_COMMIT_NAME:-github-actions[bot]}"
COMMIT_EMAIL="${GIT_COMMIT_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
MAX_ATTEMPTS=3

log() { echo "$@" >&2; }

if [ "$#" -lt 1 ]; then
  log "使い方: $0 <payload.json> [<payload.json>...]"
  exit 1
fi
for payload in "$@"; do
  if [ ! -f "$payload" ]; then
    log "payload がありません: $payload"
    exit 1
  fi
done

# 呼び出し元 checkout の中の git repo で作業する (worktree の親)
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  log "git リポジトリの外から呼ばれています。リポジトリ内で実行してください。"
  exit 1
fi

cleanup() {
  # worktree / 一時ブランチの後始末。失敗しても本体の成否には影響させない
  if [ -n "${WT:-}" ] && [ -d "${WT:-}" ]; then
    git worktree remove --force "$WT" >/dev/null 2>&1 || true
  fi
  if [ -n "${TMP_BRANCH:-}" ]; then
    git branch -D "$TMP_BRANCH" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

attempt=0
while [ "$attempt" -lt "$MAX_ATTEMPTS" ]; do
  attempt=$((attempt + 1))
  cleanup
  WT="$(mktemp -d)"
  rmdir "$WT" # git worktree add が自分で作る
  TMP_BRANCH="tmp-uxdata-$$-$attempt"

  if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
    git fetch --quiet "$REMOTE" "$BRANCH"
    git worktree add --quiet --detach "$WT" FETCH_HEAD
  else
    log "ブランチ $BRANCH がまだありません — orphan で作ります (ADR 0040 D1)"
    git worktree add --quiet --detach "$WT"
    git -C "$WT" checkout --quiet --orphan "$TMP_BRANCH"
    git -C "$WT" rm -r -f -q . 2>/dev/null || true
    cat > "$WT/README.md" <<'EOF'
# data/ux-observations

UX 観測データの蓄積ブランチ (orphan / main と履歴を共有しない)。

- `probes/YYYY-MM.jsonl` — プローブ記録 (kind: `ux-probe-record`)
- `evals/YYYY-MM.jsonl` — 機械計測 (`ux-eval-mech`) と LLM 採点 (`ux-judge-score`)

1 行 = 1 観測。**手で編集しない** (書き込みは `cicd/scripts/ux-data/append-observation.sh` 経由のみ)。
判断の記録は ADR 0040、運用は docs/runbooks/ux-probe-judge.md。
EOF
  fi

  for payload in "$@"; do
    # 終了コードは必ずコマンド単体の直後に取る (PR #157 レビュー指摘と同じ理由)
    set +e
    python3 "$HERE/append.py" "$WT" "$payload" >/dev/null
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      log "追記に失敗しました (exit $rc): $payload"
      exit 1
    fi
  done

  git -C "$WT" add -A
  if git -C "$WT" diff --cached --quiet 2>/dev/null && \
     git -C "$WT" rev-parse --verify HEAD >/dev/null 2>&1; then
    log "変化なし (全 payload が重複スキップ) — push しません"
    exit 0
  fi

  git -C "$WT" -c user.name="$COMMIT_NAME" -c user.email="$COMMIT_EMAIL" \
    commit --quiet -m "ux-data: 観測 $# 件を追記 ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

  if git -C "$WT" push --quiet "$REMOTE" "HEAD:refs/heads/$BRANCH"; then
    log "push しました: $REMOTE $BRANCH (attempt $attempt)"
    exit 0
  fi
  log "push が競合しました — fetch からやり直します (attempt $attempt/$MAX_ATTEMPTS)"
done

log "リトライ上限 ($MAX_ATTEMPTS 回) に達しました。データブランチへの追記に失敗しています。"
exit 1
