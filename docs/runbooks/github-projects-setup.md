# GitHub Projects (実行ダッシュボード) セットアップ

## Trigger

Mind Inbox の実行状態 (今どこまで進み・次に何をやり・どのセッションが何を触るか) を GitHub Projects (v2) の board で見えるようにするとき。初回セットアップ、およびフィールド / Workflow を変更するときに使う。方針の背景は [ADR 0011](../adr/0011-github-projects-as-execution-dashboard.md)。

> **原則**: board は「doc へのポインタ + 状態」だけを持つ。**設計内容は書かない** (それは ADR / design doc の領域)。フィールドは下記の最小構成から増やさない。

## Prerequisites

- リポジトリ `yomote/mind-inbox` の admin / write 権限
- GitHub Projects (v2) — Projects は user/org スコープ。ここでは **user (yomote) の Project** を 1 枚使う
- board 作成・フィールド定義・Workflow 設定は Projects v2 の **web UI 操作** (CLI / MCP からの board 生成はしない)

## Steps

### 1. Project を 1 枚作る

1. `https://github.com/users/yomote/projects` → **New project** → テンプレートは **Table** で開始 (Board ビューは後で追加)。
2. 名前: `Mind Inbox` / 説明: `実行状態のダッシュボード。設計の真実は docs (ADR / design / implementation_plan)。`
3. リポジトリ `yomote/mind-inbox` を Project にリンク (Settings → Manage access / linked repositories)。

### 2. フィールドを最小構成で定義する

既定の `Title` / `Assignees` / `Labels` / `Linked pull requests` に加え、**3 つだけ**追加する。これ以上増やさない。

| フィールド | 型 | 選択肢 | 用途 |
| --- | --- | --- | --- |
| `Status` | Single select (既定) | `Backlog` / `Next` / `In progress` / `In review` / `Done` | 実行状態。PR と自動連動 |
| `Phase` | Single select | `D (done)` / `A: AI Agent` / `B: BFF` / `C: 結線` / `Ops` / `Docs/Test` | `implementation_plan_v1` の Phase と 1:1。doc ロードマップとの唯一の接続点 |
| `Area` | Single select | `frontend` / `bff` / `ai-agent` / `voicevox` / `infra` / `docs` | 並行セッションの担当領域の衝突可視化。既存ラベルと対応 |

- `Status` の選択肢は上記 5 つに揃える (Built-in Workflow がこの名前を参照する)。
- `Phase` は `implementation_plan_v1` の Phase 見出しと文言を合わせる。Phase が増えたら doc を直してから選択肢を足す。

### 3. Built-in Workflows で状態遷移を自動化する

Project → **Settings → Workflows** で以下を有効化 (手で Status を動かさない):

1. **Item added to project** → set `Status` = `Backlog`
2. **Pull request opened / reopened** → set `Status` = `In review`
3. **Pull request merged** → set `Status` = `Done`
4. **Issue closed** → set `Status` = `Done`
5. **(任意) Auto-add** — `yomote/mind-inbox` の新規 Issue を自動で Project に追加 (throwaway PR/Issue は対象外にしたいので、Issue のみ auto-add にする)

### 4. ビューを 2 つ用意する

1. **Board (by Status)** — 日々の「今どこ」ビュー。Group by `Status`。
2. **Table (by Phase)** — ロードマップ照合ビュー。Group by `Phase`、`Area` 列を表示。

### 5. Epic の手書きチェックリストを sub-issue に移す

`implementation_plan_v1` の Phase を追跡する Epic (親 Issue) を立て、Phase A/B/C を **sub-issue** で紐づける。親本文の `- [ ]` 手書き進捗は撤去し、GitHub の sub-issue ロールアップ (親 Issue 上の進捗バー) に任せる。既存 Epic #7 / #14 も同様に、子 Issue を sub-issue 化して「進捗 YYYY-MM-DD 更新」節を撤去していく。

### 6. 運用ルールを CLAUDE.md に反映する

- セッションは着手時に対象 Issue を `In progress` + self-assign にする。
- 着手前に board で **他セッションが同じ `Area` を `In progress` にしていないか**を確認する。
- board に設計内容は書かない (doc へのリンクのみ)。

## Verification

- [ ] Project `Mind Inbox` が存在し、`yomote/mind-inbox` がリンクされている
- [ ] フィールドが `Status` / `Phase` / `Area` の 3 つだけ (余計なフィールドが無い)
- [ ] テスト: 適当な Issue を board に追加 → `Backlog` になる / PR を open → `In review` / merge → `Done` に自動遷移する
- [ ] Phase A/B/C の Issue が board 上にあり `Phase` フィールドが埋まっている
- [ ] Epic 親 Issue で sub-issue の進捗バーが表示される (手書きチェックリストに依存していない)

## Rollback

- Workflow が誤爆する場合: Settings → Workflows で該当ルールを無効化 (board 自体は残す)。
- フィールドを増やしすぎた場合: 追加フィールドを削除し §2 の 3 フィールド構成に戻す。
- Project ごと不要になった場合: Project を Close (Delete ではなく Close にして履歴を残す)。

## Common Issues

### Issue を閉じても Status が Done にならない

- 原因: `Issue closed` Workflow が未設定、または `Status` の選択肢名が `Done` と一致していない。
- 対処: Settings → Workflows を確認し、`Status` 選択肢名を Workflow が参照する文字列に合わせる。

### throwaway PR / Issue まで board に載る

- 原因: Auto-add を PR まで対象にしている。
- 対処: Auto-add を Issue のみに絞る。使い捨て (#40/#41 系) は board に載せず、載ったら手で Archive する。

### Milestone と Phase がずれる

- 原因: Milestone と `Phase` フィールドを二重運用している。
- 対処: ADR 0011 の通り **`Phase` フィールド一本**に寄せる。Milestone は使わない。

## Related

- ADR: [0011 GitHub Projects は実行状態のダッシュボードに徹する](../adr/0011-github-projects-as-execution-dashboard.md)
- ロードマップ: [`docs/design/implementation_plan_v1.md`](../design/implementation_plan_v1.md)
- 関連 Runbook: [`claude-pr-review.md`](./claude-pr-review.md) (PR レビュー Routine)
