---
name: release-gate
description: リリース PR (main → release) などの節目で、独立 judge (security-reviewer / qa-reviewer / biz-owner-reviewer / release-judge) を新品コンテキストで走らせるリリースゲート。開発リリースレポートを作り、security はスキャナ総動員 + 動的チェック、QA は受け入れの機械検証 (異常系スモークの作成・実行 + 実環境 E2E の結果確認)、ビジネスオーナーは実操作ウォークスルーをやり、release-judge が 4 レポートを突き合わせて Go/No-Go と宛先つき作業指示を出す。リリース PR が開かれたとき、user が「/release-gate」「リリースしていい?」「出荷判定して」等と言ったとき、または不可逆変更を含む deploy の直前に起動。main への機能 PR や日常の dev auto-deploy には差し込まない。判定はレポートまで — merge / deploy は人間。設計背景は ADR 0019。
---

# release-gate

deploy 前に「実装した側」とは別コンテキストの審査役でリリース可否を判定する。実装セッションが自分の変更を GO と言っても意味がない ([ADR 0019](../../../docs/adr/archive/operations/independent-judge-agents-security-qa-release.md)) — 判定は必ず subagent (新品コンテキスト) に出させ、このセッションは**範囲確定と集約だけ**をやる。

## いつ起動するか — リリースイベント = リリース PR (`main → release`)

フルゲートは重い (QA のテスト作成・実行 + セキュリティの動的チェック + 実操作ウォークスルーを含む)。**main へのマージ毎ではなく、`main → release` へマージする節目**で回す:

- **リリース PR (`base: release`, `head: main`) が開かれたとき** — これが正式なリリースイベント。judge の blocker はこの PR のレビュースレッドになり、ブランチ保護「会話の解決を必須」で**未解決のままマージ (= リリース) できない** (運用手順: [Runbook](../../../docs/runbooks/review-agents.md))
- user が `/release-gate`、「リリースしていい?」「出荷判定して」等を明示したとき (リリース PR なしの手動実行も可)
- 不可逆な変更 (スキーマ / 公開 API / 課金) を含む deploy の前 (リリース PR を経ない場合でも)

対象外 (ゲートを差し込まない):

- **main への機能 PR** — ここは CI + PR レビュー judge (ADR 0008) の守備範囲
- dev 環境への日常の自動デプロイ (ADR 0013 の main マージ → auto-deploy)
- docs のみ / 設定微修正のみの deploy

迷ったら「これは節目の版か?」を user に 1 回だけ聞く。user が「毎回やって」と言った場合はそれに従う。

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

### Step 3 — security / QA / ビジネスオーナーの並列起動

**このセッションでは審査しない。** Agent tool で 3 役を**並列**に、新品コンテキストで起動する:

1. `security-reviewer` — 「対象 ref と比較基点 (commit range)」「対象環境」を渡す。スキャナ総動員 + (起動できれば) 動的チェックの判定が返る
2. `qa-reviewer` — 同上に加えて開発リリースレポートを渡す (受け入れマトリクスの突合対象)。**受け入れの機械検証 (異常系スモークの作成・実行 + 実環境 E2E の結果確認) まで**やって QA レポートが返る (qa-rubric Q3 — 新規作成は異常系のみ、正常系の不足は hop 追加提案の finding)
3. `biz-owner-reviewer` — 対象 ref を渡す。**アプリを実際に起動・操作したウォークスルー** (スクショつき違和感レポート) が返る

qa-reviewer がテストを新規作成した場合、そのテストコードの扱い (commit して PR に含めるか) は user に確認する。

### Step 4 — release-judge に集約させる

**4 本のレポート** (開発 / QA / セキュリティ / ビジネスオーナー) 全文 + 対象 ref / 環境を `release-judge` subagent に渡し、`.github/claude/release-rubric.md` のチェックリスト (機能が揃っているか・コンセプト整合・品質シグナル・不可逆変更・rollback 経路・運用整合) を証拠つきで埋めさせ、`🟢 GO / 🟡 CONDITIONAL GO / 🔴 NO-GO` を出させる。

**リリース PR 上で動いている場合**: release-judge の FAIL / blocker 項目を、リリース PR のレビュースレッド (箇所 + 指摘 + 解除条件) として投稿する。ブランチ保護により、スレッドが解決するまでマージできない — これがゲートの強制力。

### Step 5 — user への提示

release-judge のレポートをそのまま提示し、先頭に 1 行で要約を付ける:

```markdown
## Release Gate ({ref} → {env})

{🟢/🟡/🔴} {verdict 1 行}

{release-judge レポート本体: チェックリスト表 / 残リスク / 次のアクション}

<details><summary>開発リリースレポート</summary>...</details>
<details><summary>QA レポート (受け入れマトリクス / テスト実行結果)</summary>...</details>
<details><summary>セキュリティレポート (スキャン実行状況 / findings)</summary>...</details>
<details><summary>ビジネスオーナーレポート (ウォークスルー / 違和感 / 総評)</summary>...</details>
```

- `🟢 GO`: deploy コマンドを提示する (実行は user の指示があってから)
- `🟡 CONDITIONAL GO`: 残リスクを列挙し、**受け入れるかどうかを user に聞く**
- `🔴 NO-GO`: release-judge の**作業指示リスト** (宛先: 実装 / qa-reviewer / security-reviewer / biz-owner-reviewer / 人間) を提示し、user の合意を得てディスパッチする:
  - 実装宛て → このセッション (または実装 Issue 化) で対応
  - qa-reviewer / security-reviewer / biz-owner-reviewer 宛て → 該当 subagent を指示つきで再起動
  - 対応後の**再判定は必ず release-judge を再起動して行う** (自分で「直したから GO」にしない)

## やらないこと

- ❌ このセッション (実装コンテキスト) 自身での審査・判定 — それを避けるのがこの skill の存在理由
- ❌ judge の verdict の上書き・格上げ (🔴 を「軽微なので実質 🟢」等と言い換えない)
- ❌ deploy の自動実行 (🟢 でもボタンは人間)
- ❌ 無人セッション (Routine 等) からの CONDITIONAL GO の自己承認 — 🟡 以下は人間の応答があるまで進めない

## 失敗時の挙動

- subagent が起動できない環境 → その旨を明示し、「ゲートを通していないリリース」であることを user に警告する (このセッションで代行審査して GO を出さない)
- rubric ファイルが無い → user に通知して中断 (このリポジトリ前提の skill)
