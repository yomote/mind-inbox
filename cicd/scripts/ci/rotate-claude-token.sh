#!/usr/bin/env bash
set -euo pipefail

# CLAUDE_CODE_OAUTH_TOKEN (claude-review judge 用) のローテーションをワンコマンド化する。
#
# トークンは定期失効するため、claude-token-health ワークフローが失効を検知して
# Issue を立てる。その Issue が来たらこのスクリプトを叩く。
#
# 完全自動化はできない: claude setup-token のブラウザ OAuth 認可 (Pro/Max ログイン)
# だけは人手が要る。このスクリプトはそれ以外 (Secret 更新・検知 Issue クローズ) を担う。
#
# Usage:
#   cicd/scripts/ci/rotate-claude-token.sh
#   REPO=owner/name cicd/scripts/ci/rotate-claude-token.sh   # 対象リポジトリを上書き

REPO="${REPO:-yomote/mind-inbox}"
SECRET_NAME="CLAUDE_CODE_OAUTH_TOKEN"

command -v gh >/dev/null     || { echo "error: gh CLI が必要です" >&2; exit 1; }
command -v claude >/dev/null || { echo "error: claude CLI が必要です" >&2; exit 1; }

echo "==> [1/3] Claude OAuth トークンを発行します (ブラウザで Pro/Max 認可)"
echo "    認可後にターミナルへ表示されるトークンをコピーしてください。"
echo
# 認可フローを起動。出力フォーマットに依存せず人がコピペする方式にして堅牢にする。
claude setup-token || true

echo
echo "==> [2/3] 発行されたトークンを貼り付けてください (入力は画面に表示されません):"
read -rs TOKEN
echo
[ -n "${TOKEN}" ] || { echo "error: トークンが空です" >&2; exit 1; }

# argv / シェル履歴にトークンを残さないため stdin 経由で渡す。
printf '%s' "${TOKEN}" | gh secret set "${SECRET_NAME}" --repo "${REPO}"
echo "    ✅ ${SECRET_NAME} を ${REPO} に更新しました"

echo
echo "==> [3/3] 失効検知 Issue があればクローズします"
issue_num="$(gh issue list --repo "${REPO}" --label "claude-token-rotation" --state open \
  --json number --jq '.[0].number' 2>/dev/null || true)"
if [ -n "${issue_num}" ] && [ "${issue_num}" != "null" ]; then
  gh issue close "${issue_num}" --repo "${REPO}" \
    --comment "rotate-claude-token.sh によりトークンを更新しました。" >/dev/null
  echo "    ✅ Issue #${issue_num} をクローズしました"
else
  echo "    (オープン中の検知 Issue はありません)"
fi

echo
echo "完了。次の PR で claude-review が新しいトークンで走ります。"
