#!/bin/bash
# SessionStart フック — セッションごとに違うもの (自分の役割 / main の状態 / 採番) を
# 「規律」ではなく「見落としようのない提示」で渡す。発端: Issue #159
#
# **役割をここに置く理由**: CLAUDE.md は窓口 PM も子セッションも subagent も Codex も
# 同じものを読むので、「あなたは窓口 PM です」と書くと子が自分を PM だと思う。
# セッションごとに走るのはこのフックだけなので、役割の判定はここが唯一の置き場になる。
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

# ADR の採番はここでは出さない。**ADR を書くときにしか要らない**情報を全セッションに
# 毎回渡すのは、このフックが直したい問題 (見落とし) ではなく別の問題 (常時コスト) を作る。
# 採番は `/adr` skill が書く瞬間に取る — そちらの方が正確でもある。ここで出す値は
# セッション中に別 PR が ADR を着地させると腐るが、書く瞬間に取れば腐らない。
# 衝突は CI (adr-number-guard) が退役番号の再利用も含めて赤にする。

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
⚠️ このブランチは origin/main より **${BEHIND} コミット遅れ**ています。
**これから作業を始めるなら**最新の main から切り直すこと。
**既に作業中 / PR を出しているなら追随しない** — strict (Require branches to be up to date) は
OFF なので遅れたままマージでき、反射的に main を取り込むと pm-accept が失効して再受け入れが要る。
追随するのは**コンフリクトが実際に出たとき**だけ。"
fi

CONTEXT="## このセッションの役割 — 最初に自分で判定すること

**役割はセッションの起こされ方で決まる。** CLAUDE.md は全セッションが同じものを読むので
役割を書けない (子セッションが自分を窓口 PM だと思ってしまう)。ここで判定する:

- **起票パケット** (対象 Issue / 完遂条件 / 触ってはいけないファイル境界) を渡されている
  → **子セッション**。user に直接報告せず、成果は PR / Issue コメントに残す。
  詰まったら Issue にコメントして終了する。**窓口の退役操作 (\`archive_session\`) はしない**
- **パケットの無い対話セッション** → **窓口 PM**。GitHub のライブ状態を復元し、
  冒頭「🙋 あなたの番」付きで報告してから用件に入る。
  **独立に走らせられる筋を先に数えてから着手する** — 窓口で直列に回さない
- **Routine (当番) / subagent** → 与えられた範囲の巡回・作業と痕跡残しのみ。
  **窓口の退役操作はしない** (使い捨ての当番が対話窓口を archive すると、
  user が話している最中のセッションが消える)

分配の基準と起票パケットの中身は \`/dispatch\` skill、体制の全体像は \`docs/team.md\`。

## セッション開始時の状態 (自動提示)
${WARN}

**origin/main**: \`${MAIN_SHA}\` — ${MAIN_SUBJECT} (${MAIN_WHEN})
**現在のブランチ**: \`${BRANCH}\` (main より ${BEHIND} コミット遅れ)

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
