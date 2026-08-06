---
name: release-gate
description: deploy の前に独立 judge 3 役 (security-reviewer / qa-reviewer / release-judge) を新品コンテキストで走らせるリリースゲート。開発リリースレポートを作り、security はスキャンツール併用の審査、QA は受け入れテスト (L3) の作成・実行までやり、release-judge が 3 レポートを突き合わせて Go/No-Go を出す。user が「/release-gate」「リリースしていい?」「デプロイ前チェック」「出荷判定して」等と言ったとき、または deploy-*.sh を実行する直前に起動。判定はレポートまで — deploy の実行は人間。設計背景は ADR 0015。
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

### Step 2 — 開発リリースレポートの作成

release-judge への入力 1 本目。commit range の実データ (git log / マージ PR / 紐づく Issue) から**事実だけ**をまとめる:

```markdown
## 開発リリースレポート ({基点}..{ref})
- 入る機能 / 変更: {Issue# / PR# と 1 行説明の一覧}
- やらなかったこと・落としたスコープ: {明示。無ければ「なし」}
- 実装者テストの状況: {追加/変更されたテストと CI の最新結果}
- 既知の懸念: {あれば}
```

**このレポートに「リリースして良いと思う」等の自己判定は書かない** (それは judge の仕事)。スコープ縮小を隠さず書く — release-judge は無言のスコープ縮小を FAIL にする。

### Step 3 — security / QA の並列起動

**このセッションでは審査しない。** Agent tool で 2 役を**並列**に、新品コンテキストで起動する:

1. `security-reviewer` — 「対象 ref と比較基点 (commit range)」「対象環境」を渡す。スキャンツールを回した上での判定が返る
2. `qa-reviewer` — 同上に加えて開発リリースレポートを渡す (受け入れマトリクスの突合対象)。**受け入れテスト (L3) の作成・実行まで**やって QA レポートが返る

qa-reviewer がテストを新規作成した場合、そのテストコードの扱い (commit して PR に含めるか) は user に確認する。

### Step 4 — release-judge に集約させる

**3 本のレポート** (開発 / QA / セキュリティ) 全文 + 対象 ref / 環境を `release-judge` subagent に渡し、`.github/claude/release-rubric.md` のチェックリスト (機能が揃っているか・品質シグナル・不可逆変更・rollback 経路・運用整合) を証拠つきで埋めさせ、`🟢 GO / 🟡 CONDITIONAL GO / 🔴 NO-GO` を出させる。

### Step 5 — user への提示

release-judge のレポートをそのまま提示し、先頭に 1 行で要約を付ける:

```markdown
## Release Gate ({ref} → {env})

{🟢/🟡/🔴} {verdict 1 行}

{release-judge レポート本体: チェックリスト表 / 残リスク / 次のアクション}

<details><summary>開発リリースレポート</summary>...</details>
<details><summary>QA レポート (受け入れマトリクス / テスト実行結果)</summary>...</details>
<details><summary>セキュリティレポート (スキャン実行状況 / findings)</summary>...</details>
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
