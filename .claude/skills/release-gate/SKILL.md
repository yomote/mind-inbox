---
name: release-gate
description: deploy の前に独立 judge 3 役 (security-reviewer / qa-reviewer / release-judge) を新品コンテキストで走らせ、Go/No-Go を集約する「リリースゲート」。user が「/release-gate」「リリースしていい?」「デプロイ前チェック」「出荷判定して」等と言ったとき、または deploy-*.sh を実行する直前に起動。判定はレポートまで — deploy の実行は人間。設計背景は ADR 0015。
---

# release-gate

deploy 前に「実装した側」とは別コンテキストの審査役でリリース可否を判定する。実装セッションが自分の変更を GO と言っても意味がない ([ADR 0015](../../../docs/adr/0015-independent-judge-agents-security-qa-release.md)) — 判定は必ず subagent (新品コンテキスト) に出させ、このセッションは**範囲確定と集約だけ**をやる。

## いつ起動するか

- user が `/release-gate`、「リリースしていい?」「デプロイ前チェック」等を言ったとき
- `cicd/scripts/deploy/deploy-*.sh` を実行する直前 (user の依頼が deploy でも、ゲートを先に通すことを提案する)

## 手順

### Step 1 — リリース範囲の確定

「何を」「どこへ」出すかを固定する:

```bash
git fetch origin main
git log --oneline -15 origin/main   # リリース対象 ref の確認
```

- 対象 ref: 通常は `origin/main` の HEAD。指定があればそれ
- 比較基点: 前回リリース (直近の deploy タグ / 前回 gate 通過 commit)。特定できなければ user に確認するか、「基点不明」として judge にそのまま渡す (誤魔化して狭めない)
- 対象環境: dev / stg / prod

### Step 2 — 独立 judge の並列起動

**このセッションでは審査しない。** Agent tool で 2 役を**並列**に、新品コンテキストで起動する:

1. `security-reviewer` — プロンプトに「対象 ref と比較基点 (commit range)」「対象環境」を渡す
2. `qa-reviewer` — 同上

それぞれ rubric (`.github/claude/security-rubric.md` / `qa-rubric.md`) に従ったレポートを返してくる。

### Step 3 — release-judge に集約させる

2 役のレポート全文 + 対象 ref / 環境を `release-judge` subagent に渡し、`.github/claude/release-rubric.md` のチェックリスト (CI 状態・不可逆変更・rollback 経路・運用整合) を証拠つきで埋めさせ、`🟢 GO / 🟡 CONDITIONAL GO / 🔴 NO-GO` を出させる。

### Step 4 — user への提示

release-judge のレポートをそのまま提示し、先頭に 1 行で要約を付ける:

```markdown
## Release Gate ({ref} → {env})

{🟢/🟡/🔴} {verdict 1 行}

{release-judge レポート本体: チェックリスト表 / 残リスク / 次のアクション}

<details><summary>security-reviewer レポート</summary>...</details>
<details><summary>qa-reviewer レポート</summary>...</details>
```

- `🟢 GO`: deploy コマンドを提示する (実行は user の指示があってから)
- `🟡 CONDITIONAL GO`: 残リスクを列挙し、**受け入れるかどうかを user に聞く**
- `🔴 NO-GO`: 解除条件を列挙する。解除作業をこのセッションでやった場合、**再判定は必ず judge を再起動して行う** (自分で「直したから GO」にしない)

## やらないこと

- ❌ このセッション (実装コンテキスト) 自身での審査・判定 — それを避けるのがこの skill の存在理由
- ❌ judge の verdict の上書き・格上げ (🔴 を「軽微なので実質 🟢」等と言い換えない)
- ❌ deploy の自動実行 (🟢 でもボタンは人間)
- ❌ 無人セッション (Routine 等) からの CONDITIONAL GO の自己承認 — 🟡 以下は人間の応答があるまで進めない

## 失敗時の挙動

- subagent が起動できない環境 → その旨を明示し、「ゲートを通していないリリース」であることを user に警告する (このセッションで代行審査して GO を出さない)
- rubric ファイルが無い → user に通知して中断 (このリポジトリ前提の skill)
