# 0011. GitHub Projects は実行状態のダッシュボードに徹し、設計の真実は docs に置く

- Status: Accepted (debrief #1, 2026-08-06) — 一部改訂: 実行ダッシュボードの描画面を Projects board から status ページの戦況図へ移すことを決定 ([ADR 0044](0044-stream-lanes-as-the-project-map.md), 2026-08-11。**描画の実装は #289 待ちで、それまで戦況図を見る手段は `/status` skill のみ**)。「Issues が実行状態の真実 / board に設計を書かない」の核は維持
- Date: 2026-07-19
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: 計画的な開発運用の検討 (`claude/mainvein-box-planning-ub2adw` ブランチ)。ロードマップが doc にしか無く、Epic 進捗が Issue 本文の手書きチェックリストでドリフトしていた課題への対処。

## Context and Problem Statement

Mind Inbox はすでに **「真実は 1 か所」ドクトリン**が効いている (判断=ADR / UI=MDX / API=OpenAPI / 手順=Runbook / ロードマップ=`implementation_plan_v1`)。一方で **実行状態 (今どこまで進み・次に何をやり・どのセッションが何を触るか) は Issue 本文の手書きチェックリストに閉じており**、Epic #7 / #14 の「進捗 YYYY-MM-DD 更新」は更新が止まってドリフトする。`implementation_plan_v1` の Phase A/B/C は doc にしか無く Issue 化されていないため、Phase D 完了 (PR #44) の次が「板」の上で空白になる。並行して複数の Claude セッションで開発を進める構想 (#23) に対し、担当領域の衝突を見る実行ビューも無い。GitHub の計画機能をどこまで・どう使うかを決める必要がある。

## Decision Drivers

- **既存ドクトリンとの整合** — 「真実は 1 か所」を壊さない。設計内容を二重管理にしない
- **進捗の自動追従** — 手書きチェックリストのドリフトをなくす (更新忘れが起きない仕組み)
- **並行開発の衝突回避** — 複数セッションが「今 In progress の Issue / 触っている領域」を一目で見られる
- **ロードマップと実行の接続** — `implementation_plan_v1` の Phase と board の状態が対応する
- **運用コストの最小化** — 個人開発。ツールを増やして管理負荷を上げない

## Considered Options

- Option A: **GitHub Projects (v2) を実行ダッシュボードに使い、設計の真実は docs に残す**
- Option B: 現状維持 (Issue 本文の手書きチェックリスト)
- Option C: GitHub Milestones でロードマップを追跡する
- Option D: 外部 PM ツール (Notion / Linear 等) を導入する

## Decision Outcome

Chosen option: **"Option A"**。GitHub Projects (v2) を **実行状態のダッシュボード**として 1 枚だけ立て、設計の「なぜ / 何を」は従来どおり docs (ADR / design / implementation_plan) に置く。責務は既存の「ADR=判断 / Runbook=手順」分割と同じ発想で切る:

| 問い                                      | 真実の所在                                | 性質         |
| ----------------------------------------- | ----------------------------------------- | ------------ |
| なぜ / 何を (why / what)                  | docs (ADR / design / implementation_plan) | 不変・累積   |
| いつ / 誰が / 今どこ (when / who / state) | GitHub Issues + Projects                  | 揮発・ライブ |

**Projects には設計内容を書かない** — board のアイテムは「doc へのポインタ + 状態」だけを持つ。フィールドは最小構成 (`Status` / `Phase` / `Area`)、`Phase` フィールドが `implementation_plan_v1` の Phase と 1:1 で対応する唯一の接続点。状態遷移は Built-in Workflows で自動化し (PR open→In review / merged→Done / issue closed→Done)、Epic 進捗は手書きチェックリストをやめて **sub-issue の自動ロールアップ**に移す。

Option B は既にドリフトしており Driver 2 に反する。Option C は Milestone が「期日付きの単一グルーピング」で、`Phase` フィールドと二重管理になり Driver 1/5 に反する (Phase フィールド一本に寄せる)。Option D は個人開発に対して過剰で、GitHub (Issue / PR / レビュー) の外に状態が漏れて Driver 1/5 に反する。

具体的なフィールド定義・Built-in Workflow の設定手順は Runbook [`github-projects-setup.md`](../runbooks/github-projects-setup.md) に置く (board 自体の作成は Projects v2 の web UI 操作のため)。

### Positive Consequences

- 進捗が PR / Issue の状態から自動で追従し、手書きチェックリストのドリフトが消える
- `Phase` フィールドで doc ロードマップと実行状態が接続され、「次にやる Phase」が板で見える
- `Area` フィールドで並行セッションの担当領域の衝突が可視化される
- 設計の真実は docs のまま。Projects はビューであって source of truth ではない (既存ドクトリン維持)
- 新しいツール・シークレット・課金を増やさない (GitHub 内で完結)

### Negative Consequences

- Projects v2 の board 作成・フィールド定義・Workflow 設定は **web UI 操作**で、リポジトリ管理外の手動設定が残る (Runbook で手順を固定化して緩和)
- 「設計は docs / 状態は Projects」の線引きを運用者が守る規律が要る (board に設計を書き始めると二重管理に戻る)
- sub-issue / Projects の仕様変更に追従が必要 (GitHub 側の進化に依存)

## Pros and Cons of the Options

### Option A: GitHub Projects を実行ダッシュボードに使う (採用)

board は状態のみ、設計は docs。`Phase` フィールドで `implementation_plan_v1` と接続。

- Good, because 「真実は 1 か所」を壊さず、状態だけを板に持たせられる
- Good, because Built-in Workflow と sub-issue ロールアップで進捗が自動追従する
- Good, because 並行開発の担当領域を `Area` フィールドで可視化できる
- Bad, because board / フィールド / Workflow の設定が web UI 側に残る

### Option B: 現状維持 (手書きチェックリスト)

Epic Issue 本文に `- [ ]` を並べ、手で更新する。

- Good, because 追加設定が要らない
- Bad, because 更新が止まってドリフトする (実際に #7/#14 で発生)
- Bad, because 並行開発の実行ビューが無い

### Option C: GitHub Milestones

期日付きのグルーピングで Issue を束ねる。

- Good, because GitHub 標準機能で軽量
- Bad, because `Phase` フィールドと二重管理になる
- Bad, because 期日ベースで、Phase (依存順) の表現に合わない

### Option D: 外部 PM ツール (Notion / Linear 等)

多機能な PM ツールに状態を持たせる。

- Good, because ロードマップ / ボード / ドキュメントが一体化した高機能 UI
- Bad, because 個人開発に対して過剰で管理負荷が上がる
- Bad, because 状態が GitHub (PR / Issue / レビュー) の外に漏れ、真実が分散する

## Links

- Runbook: [`docs/runbooks/github-projects-setup.md`](../runbooks/github-projects-setup.md)
- ロードマップ: [`docs/design/archive/implementation_plan_v1.md`](../design/archive/implementation_plan_v1.md) (archive — v1 完了済み)
- 関連 ADR: [0008](0008-pr-review-via-cloud-routine.md) (PR レビュー Routine — 同じくプロセス系の判断)
- GitHub Projects: <https://docs.github.com/en/issues/planning-and-tracking-with-projects>
