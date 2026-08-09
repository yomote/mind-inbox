#!/bin/bash
# SessionStart フック — 並行作業の衝突を「規律」ではなく「見落としようのない提示」で防ぐ。
# 設計: ADR 0028 D2 / 発端: Issue #159
#
# 2026-08-09 に実際に起きた 2 件が対象:
#   1. 別セッションが保守性 Phase 3 を完遂・マージ済みなのに気づかず重複作業した
#      (CLAUDE.md も ADR 0021 も読んだ上で origin/main を見なかった)
#   2. ADR 番号をローカル最大値 +1 で取り、main の採番と衝突した (2 回目の再発)
#
# **情報を提示するだけ**で、セッションの起動やファイル変更は一切しない。
# ネットワーク不通でもセッションを止めない (fetch 失敗時は取得済みの情報だけ出す)。

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || echo .)}" || exit 0
git rev-parse --git-dir > /dev/null 2>&1 || exit 0

# タイムアウト付き。fetch できなくても続行する (オフラインでセッションを止めない)
timeout 30 git fetch origin main --quiet 2>/dev/null
FETCH_OK=$?

MAIN_SHA="$(git rev-parse --short origin/main 2>/dev/null || echo '不明')"
# 切り詰めは python 側で行う。`cut -c` は locale が UTF-8 でないとバイト単位で切り、
# 日本語のコミット件名を多バイト文字の途中で分断して JSON を壊す (実測済み)。
MAIN_SUBJECT="$(git log -1 --format='%s' origin/main 2>/dev/null)"
MAIN_WHEN="$(git log -1 --format='%cr' origin/main 2>/dev/null)"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '不明')"
BEHIND="$(git rev-list --count HEAD..origin/main 2>/dev/null || echo '?')"

# ADR の採番は **origin/main 基準**で取る (ローカル最大値 +1 は 2 回衝突している)
ADR_MAX="$(git ls-tree -r origin/main --name-only docs/adr/ 2>/dev/null \
  | grep -oE '[0-9]{4}' | sort -n | tail -1)"
if [ -n "$ADR_MAX" ]; then
  ADR_NEXT="$(printf '%04d' $((10#$ADR_MAX + 1)))"
else
  ADR_NEXT="不明 (origin/main を取得できず)"
fi

# 直近 24h に更新されたリモートブランチ = 並行作業の目安。
# このリポジトリは squash merge なので**マージ済みが混じる** — 断定材料ではなく確認の合図。
RECENT="$(
  NOW=$(date +%s)
  git for-each-ref --format='%(refname:short)|%(committerdate:unix)|%(committerdate:relative)' \
    refs/remotes/origin 2>/dev/null | while IFS='|' read -r ref ts rel; do
      case "$ref" in origin/main | origin/HEAD) continue ;; esac
      [ -n "$ts" ] && [ $((NOW - ts)) -lt 86400 ] && echo "- ${ref#origin/} (${rel})"
    done | head -10
)"
[ -z "$RECENT" ] && RECENT="- (直近 24h の更新なし)"

WARN=""
[ "$FETCH_OK" -ne 0 ] && WARN="
⚠️ origin への fetch に失敗しました。下の情報は古い可能性があります。"
if [ "$BEHIND" != "?" ] && [ "$BEHIND" -gt 0 ] 2>/dev/null; then
  WARN="${WARN}
⚠️ このブランチは origin/main より **${BEHIND} コミット遅れ**ています。着手前に取り込むか、ブランチを切り直してください。"
fi

CONTEXT="## セッション開始時の状態 (ADR 0028 D2 / 自動提示)
${WARN}

**origin/main**: \`${MAIN_SHA}\` — ${MAIN_SUBJECT} (${MAIN_WHEN})
**現在のブランチ**: \`${BRANCH}\` (main より ${BEHIND} コミット遅れ)

**次に使える ADR 番号: ${ADR_NEXT}** — 採番は必ず origin/main 基準で取ること。
\`ls docs/adr/\` のローカル最大値 +1 は過去 2 回衝突している (0015→0019 / 0026→0027)。

**直近 24h に更新されたブランチ** (並行作業の目安。squash merge のためマージ済みも混じる):
${RECENT}

着手前に、この作業が既に他所で進んでいないか確認すること (2026-08-09 に重複作業の実例あり / Issue #159)。"

# additionalContext でエージェントの文脈に入れる (画面に出すだけでは見落とされる)。
# stdin/stdout をバイト列で扱う — locale が UTF-8 でない環境で日本語が壊れるため
# (実際に C locale で JSON が不正になった)。
python3 -c "
import json, sys
ctx = sys.stdin.buffer.read().decode('utf-8', errors='replace')
# 長い件名は**文字数**で切り詰める (バイト単位で切ると多バイト文字が壊れる)
lines = []
for line in ctx.split('\n'):
    lines.append(line if len(line) <= 200 else line[:200] + '…')
out = json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': '\n'.join(lines),
    }
}, ensure_ascii=False)
sys.stdout.buffer.write(out.encode('utf-8') + b'\n')
" <<< "$CONTEXT"
