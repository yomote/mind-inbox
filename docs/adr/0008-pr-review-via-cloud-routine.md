# 0008. PR レビューは Claude Code on the web の Routine で行う (API キー Actions / 管理版 Code Review を採らない)

- Status: Accepted
- Date: 2026-06-27
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: `claude/code-review-workflow` ブランチでの検討 (#34 ほか)。当初 API キー方式の GitHub Actions を実装したが、検討の末に撤去し Routine へ切替えた。

## Context and Problem Statement

開発を Claude Code 駆動で進めるなかで、**開発セッションとは別軸の「審査役 (LLM-as-a-judge)」**による PR レビューを自動化したい。狙いは「指摘 → 修正 → 再チェック → 解決」のループを、**人間の規律に依存せず仕組みとして回す**こと。制約は、個人運用の public リポジトリ・Claude Pro/Max 契約で、**追加の従量課金を発生させたくない**点。どの実行基盤でレビューを回すかを決める必要がある。

## Decision Drivers

- **追加課金の回避** — サブスク枠で完結させ、メーター課金を発生させない
- **開発との分離** — 開発セッションと混在させず、独立したレビューセッションで走る
- **規律に依存しない強制力** — 指摘に対応するまでマージできないゲートがある
- **審査基準のリポジトリ管理** — 観点を single source of truth として repo 内に置く (rubric-as-truth)
- **秘密情報の最小化** — 管理する API キー / トークンを増やさない

## Considered Options

- Option A: **Claude Code on the web の Routine** (GitHub `pull_request` トリガー)
- Option B: GitHub Actions + `anthropics/claude-code-action` (`ANTHROPIC_API_KEY`)
- Option C: 管理版 Code Review (Anthropic managed / `@claude review`)
- Option D: CodeRabbit 等のサードパーティ AI レビュー App

## Decision Outcome

Chosen option: **"Option A" (Routine)**。

Routine は GitHub イベントで**自動起動する Claude Code クラウドセッション**で、(1) **サブスク枠で動き追加課金が無い**、(2) 開発とは別セッションで走る、(3) 起動したセッションは**管理された GitHub 接続経由で PAT なしのまま** review スレッドの resolve / PR の merge まで到達できる (実測で確認済み)、(4) 審査基準を `.github/claude/review-rubric.md` に集約できる、という点で全 driver を満たす。強制力はブランチ保護 **"Require conversation resolution before merging"** で担保し、**merge は人間が押す** (judge は resolve まで) ことで最後の歯止めを残す。

Option B は当初実装したが、`ANTHROPIC_API_KEY` による**メーター課金が原理的に避けられず** Driver 1 に反するため撤去した (OAuth トークン方式はローカル認可と定期失効の運用負荷で不採用)。Option C は「修正したら自動 resolve」を**決定論的に**行える反面、**Team/Enterprise 限定かつ 1 レビュー $15〜25 の従量**で個人運用に合わない。Option D は public リポジトリ無料枠でこのループ専用に作られているが、**Claude / 自前基準ではなくなる**。

### Positive Consequences

- 追加課金ゼロ (サブスク枠)。上限超過時も課金ではなく実行が拒否されるだけ
- 開発セッションと分離した独立レビュー
- **PAT 不要** — 管理 GitHub 接続で resolve / merge まで到達 (Routine セッションの自己申告で確認)
- 審査基準が `.github/claude/review-rubric.md` に集約され、観点変更が容易
- 「会話の解決を必須」+ 人間 merge により、規律に頼らずゲートが効き、最終判断は人が持つ
- 再レビュー時に直ったスレッドを自動 Resolve し、ループが閉じる (rubric の収束ルール)

### Negative Consequences

- **research preview** であり、仕様 / 制限 / API が変わり得る
- 開発と**同じレート枠・日次実行上限を共有**する (課金ではなく拒否)
- Routine 自体の設定は claude.ai/code/routines の **web UI で行い、リポジトリ管理外**の手動設定が残る
- 自動 Resolve は**モデル判断**であり決定論ではない (最終 merge を人間に残すことで担保)
- Routine は `@claude review` コメントでの再実行に非対応 → 再レビューは `synchronize` トリガー依存

## Pros and Cons of the Options

### Option A: Routine (採用)

GitHub イベントで起動する Claude Code クラウドセッション。審査基準は repo 内 `review-rubric.md`。

- Good, because サブスク枠で追加課金が無い
- Good, because PAT 不要で resolve / merge まで到達できる (管理 GitHub 接続)
- Good, because 審査基準が repo 管理下 (rubric-as-truth)
- Bad, because research preview / レート枠共有 / 設定が web UI 側に残る

### Option B: GitHub Actions + claude-code-action (API キー)

`pull_request` で AI アクションを呼ぶ。ワークフローは repo 内で完結し自由度は高い。

- Good, because 設定が完全に repo 内 (workflow YAML) で完結
- Bad, because `ANTHROPIC_API_KEY` による従量課金が避けられない (本ドライバに反する)
- Bad, because Secret 管理 / OIDC 権限など運用要素が増える

### Option C: 管理版 Code Review

Anthropic 管理の多エージェントレビュー。push で auto-resolve まで決定論的に行う。

- Good, because 「修正したら自動 resolve」を決定論的に実現、専用 check run も出る
- Bad, because Team / Enterprise 限定
- Bad, because 1 レビュー $15〜25 の従量課金 (個人運用に不適)

### Option D: CodeRabbit 等の App

public リポジトリ無料枠で incremental review + 解決まで行う専用 App。

- Good, because 公開リポジトリ無料枠でこのループ専用に作られている
- Bad, because レビュアーが Claude / 自前基準ではなくなる

## Links

- Runbook: [`docs/runbooks/claude-pr-review.md`](../runbooks/claude-pr-review.md)
- 審査基準: [`.github/claude/review-rubric.md`](../../.github/claude/review-rubric.md)
- Routines: <https://code.claude.com/docs/en/routines>
- Code Review (管理版): <https://code.claude.com/docs/en/code-review>
