# 0019. セキュリティ / QA / リリース判定を実装コンテキストから分離した独立 judge エージェントにする

- Status: Proposed
- Date: 2026-08-06
- Deciders: omoteforlab (承認待ち)
- Consulted: —
- Informed: —

Technical Story: `claude/security-review-agent-9avoqw` ブランチ。「実装する側は必ずリリースする側に流れる」という user の問題提起から。

## Context and Problem Statement

現在のループは「実装セッション → pr-readiness (自己チェック) → PR レビュー Routine ([ADR 0008](0008-pr-review-via-cloud-routine.md)) → CI → 人間 merge」で回っている。しかし **セキュリティ深掘り・QA (受け入れ/シナリオ視点)・リリース Go/No-Go 判定** は、単一 judge の rubric の一部 (軸 B の 1 行) か、実装者自身の自己申告に混ざっている。

問題は **incentive の混在**: 実装者 (とそのコンテキストを引き継いだエージェント) は「出荷する」方向にバイアスがかかる。同一コンテキストで「自分の変更を疑え」と言っても、実装時の前提・正当化をそのまま引き継ぐため、監査として機能しにくい。人間の組織で QA / セキュリティ / リリース判定が開発と別ロールなのと同じ分離を、エージェントループにも入れる必要がある。

## Decision Drivers

- **役割とコンテキストの完全分離** — 審査役は実装セッションの会話・前提を一切引き継がず、diff と真実ソースだけから判断する
- **バイアスの構造的抑制** — 「出荷したい」側と「止める」側を別エージェントにし、プロンプトレベルでも敵対的スタンス (default NO-GO) を明示する
- **rubric-as-truth の踏襲** — 審査基準は ADR 0008 と同じく repo 内の rubric に置き、観点変更を PR で管理する
- **追加課金の回避** — サブスク枠 (subagent / skill / Routine) で完結させる
- **人間の最終判断を残す** — judge は「止める・指摘する」まで。merge / deploy のボタンは人間

## Considered Options

- Option A: 既存 PR レビュー Routine の rubric に観点を追記 (単一 judge 拡張)
- Option B: **役割別 rubric + 別コンテキスト subagent + `release-gate` skill** (役割ごとに独立エージェント)
- Option C: 役割ごとに独立した Routine を追加 (PR トリガーで 3 セッション)
- Option D: 外部 SaaS (CodeQL / Snyk / CodeRabbit 等) に委譲

## Decision Outcome

Chosen option: **"Option B"**。役割ごとに (1) 審査基準 rubric (`.github/claude/{security,qa,release}-rubric.md`)、(2) Claude Code の **subagent 定義** (`.claude/agents/*.md`) を置く。subagent は**常に新しいコンテキストで起動し、呼び出し元の会話を引き継がない**ため、「別のコンテキストで完全に別の役割」という要件を仕組みとして満たす。リリース判定は `/release-gate` skill が (1) 開発リリースレポート (事実の列挙のみ) を作り、(2) security / QA を並列起動し、(3) release-judge に 3 レポートを突合させて Go/No-Go を出す。最終判断 (deploy 実行) は人間が行う。**フルゲートは毎デプロイではなく節目で回す**: リリースイベントは**リリース PR (`main → release`)** として表現し (後述)、main への機能 PR や dev への日常 auto-deploy (ADR 0013) には差し込まない (そこは CI + PR レビュー judge の守備範囲)。

各役割の分担:

| 役割 | いつ走るか | やること | やらないこと |
| --- | --- | --- | --- |
| PR レビュー judge (既存, ADR 0008) | PR opened / synchronize | 戦略 doc 整合・一般バグ・PR テンプレ整合 | セキュリティ深掘り (security judge に委譲) |
| security-reviewer | PR 時 (レビュー Routine から subagent 起動) + release-gate 時 | **環境で使える脆弱性スキャナを総動員** (npm audit / pip-audit / osv-scanner / gitleaks / semgrep / bandit / trivy 等の SAST/SCA/secrets) し、起動できるなら**動的チェック** (外部通信の観察・未認証アクセス実測) も実施。結果を rubric に照らして triage + 攻撃面の目視追跡 | コードスタイル・テスト設計・コード修正 |
| qa-reviewer | release-gate 時 (大きめ PR では任意) | 受け入れマトリクス (欲しかった機能が揃っているか) の作成 + **ゴールデンパス・UI 挙動・ユーザビリティ観点のシナリオテスト (L3 E2E) の作成・実行**。**L3 レイヤの所有者** | 実装者 unit の再レビュー・CI 重複・ビジュアルの美的評価 (デザイナー領域)・**プロダクトコードの変更** (触るのはテストコードのみ) |
| biz-owner-reviewer | release-gate 時 | **ビジネスオーナーとして UI を実操作** (stub 起動 + Playwright ウォークスルー、スクショつき): 文言・導線・期待とのズレ・コンセプト体現・「普通に考えておかしいよね」の違和感を報告 | コードレビュー・アサーション的な仕様突合 (QA の担当)・コード変更 |
| release-judge | release-gate 時 (リリース PR) | **4 レポート (開発リリースレポート / QA / セキュリティ / ビジネスオーナー) + CI のレイヤ別結果の突合**: 機能が揃っているか・**コンセプトデックとの整合 (企画観点)**・テスト/QA が実際に行われたか・不可逆変更・rollback 経路。FAIL/UNKNOWN は**宛先つき作業指示リスト**に変換して返す | コード詳細の再監査 (レポートが無ければ UNKNOWN、デフォルト NO-GO)・指示の実行 |

### リリースイベントの受け口: リリース PR (`main → release`)

「main へのマージ毎」ではなく「main から release ラインへ昇格する節目」をゲート対象にする。その節目を GitHub 上のイベントとして表現するため、**常設の `release` ブランチを置き、リリース = `main → release` の PR (リリース PR) を開くこと**と定義する:

- **既存機構の再利用**: PR イベントなので、フルゲートを Routine (ADR 0008 と同じ仕組み) で自動起動できる。tag / GitHub Release / workflow_dispatch 起点だと Routine のトリガーに乗らず、別の実行基盤が要る
- **強制力**: judge の blocker をリリース PR のレビュースレッドとして残せば、`release` ブランチ保護「会話の解決を必須」で**未解決のままマージ (= リリース) できない**。ADR 0008 と同型のゲートが release 粒度でも効く
- **履歴**: 「何をいつリリース判定したか」がリリース PR として GitHub に残る (journal と別に機械可読)
- 日常の dev auto-deploy (ADR 0013, main マージ) はこれまで通りゲートなし。`release` へのマージを stg/prod 昇格などの「きっちりした版」の起点として使う

### Positive Consequences

- 実装コンテキストと審査コンテキストが構造的に分離され、「リリースする側に流れる」バイアスを仕組みで抑える
- 観点が rubric として repo 管理下に入り、変更が PR / レビュー対象になる (ADR 0008 と同型)
- subagent はどのセッション (開発 / Routine / release-gate) からでも同じ基準・新品コンテキストで起動できる
- 追加課金なし (サブスク枠内)

### Negative Consequences

- judge が増える分、サブスクのレート枠・実行時間を消費する (release-gate は deploy 前のみ起動して抑制。特に QA はテスト作成・実行まで行うため最も重い)
- qa-reviewer は「judge は書かない」原則の例外 (テストコード限定で Write/Edit を持つ)。プロダクトコード不変更は rubric + agent 定義の二重指示で担保するが、決定論的な強制ではない
- security のスキャンはセッション環境にツールが無いと grep 代替 + UNKNOWN になる (UNKNOWN は release-judge 側で GO を阻む設計なので、静かには劣化しない)
- subagent の独立性は「コンテキスト非共有」であって、モデル自体は同一 — 同型の見落としは共有し得る (CodeQL 等の CI 恒久組み込みで補完余地)
- release-judge の NO-GO は助言であり、deploy スクリプトを機械的にブロックはしない (強制力は人間の運用。必要になったら CI ゲート化を別 ADR で)

## Pros and Cons of the Options

### Option A: 単一 judge 拡張

既存 review-rubric に軸を足すだけ。

- Good, because 追加の仕組みが不要で最小コスト
- Bad, because 1 コンテキストに全役割が混在し、観点が薄まる (今まさに起きている問題の延長)
- Bad, because リリース判定は PR 粒度のイベントではなく、PR レビュー Routine に乗らない

### Option B: 役割別 rubric + subagent + release-gate skill (採用)

- Good, because subagent = 新品コンテキスト保証。役割ごとに敵対的スタンスをプロンプトで固定できる
- Good, because rubric-as-truth を踏襲し、既存ループ (Routine / skill) にそのまま接続できる
- Bad, because rubric / agent 定義のメンテ対象が 3 セット増える

### Option C: 役割ごとに独立 Routine

- Good, because セッションが物理的に完全分離する
- Bad, because Routine 設定は web UI 管理でリポジトリ外の手動設定が役割数ぶん増える (ADR 0008 の Negative を倍加)
- Bad, because トリガーが GitHub イベント限定で、deploy 前のリリース判定に合わない

### Option D: 外部 SaaS

- Good, because 決定論的な SAST /依存スキャンは LLM より再現性が高い
- Bad, because 審査基準が自前 rubric でなくなる / 無料枠・課金の管理が増える
- Bad, because QA (このプロダクトの要件・UI 仕様との突合) は外部ツールでは代替できない

将来、CodeQL 等の無償 CI スキャンを security judge の**補完**として足すのは本 ADR と矛盾しない。

## Links

- 審査基準: [`security-rubric.md`](../../.github/claude/security-rubric.md) / [`qa-rubric.md`](../../.github/claude/qa-rubric.md) / [`biz-owner-rubric.md`](../../.github/claude/biz-owner-rubric.md) / [`release-rubric.md`](../../.github/claude/release-rubric.md)
- Runbook: [`docs/runbooks/review-agents.md`](../runbooks/review-agents.md)
- 関連 ADR: [0008 PR レビュー Routine](0008-pr-review-via-cloud-routine.md) / [0014 理解ゲート+デブリーフ](0014-design-comprehension-gate-and-debrief.md)
- Subagents: <https://code.claude.com/docs/en/sub-agents>
