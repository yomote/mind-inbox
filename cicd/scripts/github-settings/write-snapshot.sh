#!/usr/bin/env bash
# 観測した GitHub 設定 (事実) をデータブランチ data/github-settings に置いて push する。
#
# 蓄積先をデータブランチにするのは ADR 0041 と同じ流儀 (Issue コメントに state を
# 溜めない)。**意図 = main の cicd/github/settings.yml / 事実 = このブランチ** と
# 分けることで、`git log -p` が「いつ設定が変わったか」の記録そのものになる。
#
# 規律:
#   - **同じパスを上書きする** (追記しない)。変化した回だけコミットが立つので、
#     履歴がそのまま「変更の記録」になる
#   - **内容が同じなら push しない** — 点検のたびにコミットを立てると、履歴が
#     ノイズだらけになって肝心の変化が読めなくなる。点検した事実は run 履歴が持つ
#   - **push に使う権限は GITHUB_TOKEN** (checkout が仕込んだ credential)。
#     device-code で得た特権トークンは**設定の読み書きにだけ使い、git に触らせない**
#   - ブランチが無ければ orphan で作る (main と履歴を共有しない)
#   - push が競合したら fetch からやり直す (最大 3 回)
#
# 使い方:
#   write-snapshot.sh <snapshot.json> <owner/repo>
#
# 環境変数:
#   SETTINGS_DATA_BRANCH (既定 data/github-settings)
#   SETTINGS_DATA_REMOTE (既定 origin)
#   GIT_COMMIT_NAME / GIT_COMMIT_EMAIL (既定 github-actions[bot])
#
# 終了コード: 0 = push まで完了 / 変化なし、1 = 前提不足・リトライ上限

set -euo pipefail

BRANCH="${SETTINGS_DATA_BRANCH:-data/github-settings}"
REMOTE="${SETTINGS_DATA_REMOTE:-origin}"
COMMIT_NAME="${GIT_COMMIT_NAME:-github-actions[bot]}"
COMMIT_EMAIL="${GIT_COMMIT_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
MAX_ATTEMPTS=3

log() { echo "$@" >&2; }

# hooks を無効化する (#466 と同型 / PR #475 の代役レビューで検出)。**commit だけでなく、
# このスクリプトが撃つすべての git に効かせる** — worktree は呼び出し元 repo の config を
# 共有するので、core.hooksPath が絶対パスで入っている checkout では `git worktree add` の
# post-checkout や (将来 .husky/pre-push を足せば) push でも親のフックが発火する。
# データブランチ側に lint-staged 等の設定は無いため必ず落ち、GitHub 設定ドリフトの
# 唯一の記録が古いまま静かに止まる (点検 run は緑で終わるので誰にも見えない)。
# 見えなくなるもの: このブランチ相手の git では hook が一切走らない。
# data/github-settings は snapshot の JSON だけで main のコードを含まないので問題ない。
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=core.hooksPath
export GIT_CONFIG_VALUE_0=/dev/null

if [ "$#" -ne 2 ]; then
  log "使い方: $0 <snapshot.json> <owner/repo>"
  exit 1
fi

SNAPSHOT="$1"
REPO_SLUG="$2"

if [ ! -f "$SNAPSHOT" ]; then
  log "スナップショットがありません: $SNAPSHOT"
  exit 1
fi
case "$REPO_SLUG" in
  */*) : ;;
  *) log "owner/repo の形ではありません: $REPO_SLUG"; exit 1 ;;
esac
# 事故防止: 予期しないパス片 (.. や空要素) をそのままファイルパスにしない
case "$REPO_SLUG" in
  *..*|*//*|/*|*/) log "owner/repo が不正です: $REPO_SLUG"; exit 1 ;;
esac

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  log "git リポジトリの外から呼ばれています。"
  exit 1
fi

cleanup() {
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
  TMP_BRANCH="tmp-ghsettings-$$-$attempt"

  if git ls-remote --exit-code --heads "$REMOTE" "$BRANCH" >/dev/null 2>&1; then
    git fetch --quiet "$REMOTE" "$BRANCH"
    git worktree add --quiet --detach "$WT" FETCH_HEAD
  else
    log "ブランチ $BRANCH がまだありません — orphan で作ります"
    git worktree add --quiet --detach "$WT"
    git -C "$WT" checkout --quiet --orphan "$TMP_BRANCH"
    git -C "$WT" rm -r -f -q . 2>/dev/null || true
    cat > "$WT/README.md" <<'EOF'
# data/github-settings

**観測された GitHub 設定 (事実) の置き場**。orphan ブランチ (main と履歴を共有しない)。

- `snapshots/<owner>/<repo>.json` — 最後に観測した設定。点検のたびに上書きされ、
  **内容が変わった回だけコミットが立つ**。`git log -p` が「いつ何が変わったか」の記録

あるべき姿 (意図) は main の `cicd/github/settings.yml`。差分の出し方と適用手順は
`docs/runbooks/github-settings.md`。

**手で編集しない** (書き込みは `cicd/scripts/github-settings/write-snapshot.sh` 経由のみ)。
秘密の値は入れない — 保存するのは設定の形だけで、トークンも個人名も含まない。
EOF
  fi

  DEST="$WT/snapshots/$REPO_SLUG.json"
  mkdir -p "$(dirname "$DEST")"
  cp "$SNAPSHOT" "$DEST"

  git -C "$WT" add -A
  if git -C "$WT" diff --cached --quiet 2>/dev/null && \
     git -C "$WT" rev-parse --verify HEAD >/dev/null 2>&1; then
    log "変化なし — push しません (点検した事実は run 履歴が持つ)"
    exit 0
  fi

  # hooks の無効化はスクリプト冒頭の GIT_CONFIG_* で全 git に効かせている (#466 と同型)
  git -C "$WT" -c user.name="$COMMIT_NAME" -c user.email="$COMMIT_EMAIL" \
    commit --quiet -m "github-settings: $REPO_SLUG の観測を更新"

  if git -C "$WT" push --quiet "$REMOTE" "HEAD:refs/heads/$BRANCH"; then
    log "push しました: $REMOTE $BRANCH (attempt $attempt)"
    exit 0
  fi
  log "push が競合しました — fetch からやり直します (attempt $attempt/$MAX_ATTEMPTS)"
done

log "リトライ上限 ($MAX_ATTEMPTS 回) に達しました。データブランチへの書き込みに失敗しています。"
exit 1
