# プロジェクト継続性を 3 層 (機構化された完遂 / 当番 PM / 窓口 PM) で保証する

- Status: Proposed
- Date: 2026-08-11
- Deciders: PO (yomote) / PM セッション

## Context and Problem Statement

プロジェクトの進行が「PM セッションが生きているか」に依存しており、静かに止まる事故が続いた:

- 2026-08-10: PR #230 が全 check 🟢 のまま一晩放置 (セッション内 CronCreate がコンテナ回収と共に消えた)
- 2026-08-11: 🟢 のまま未マージの PR 4 本 (#238/#240/#247/#220) の滞留を **PO が発見** (auto-merge の不発 / #253)

構造的な原因は 4 つ重なっている:

1. **webhook は CI 成功・マージ遷移を配信しない** — 「静かに詰まる」状態はイベント駆動の PM に届かない
2. **セッション内の定期起床 (CronCreate) はコンテナ回収で消える** — 夜間の空白を守れない
3. **GitHub auto-merge は GITHUB_TOKEN 起点の status では発火しない** (#253) — 「緑になったら merge」の主経路が構造的に不発
4. **スケジュール起動系 MCP (`create_trigger` 等) は実行環境の承認ゲートで停止する** (2026-08-11 実測: 2 回とも `requires approval`) — エージェント単独では常設タイマーを作れない

PO の要求: 「プロジェクトが止まっているのを私が見に行って発見する」状態を仕組みで無くす。日次サマリではなく **PM として動き続ける**こと。セッションが揮発するのは構わないが、引き継ぎが成立していること。

## Considered Options

1. **3 層構造** — A: 完遂とストール検知を Actions に機構化 / B: 当番 PM (Routine で毎回新セッション) / C: 窓口 PM (現行ハブ)
2. 永続セッション束縛の Routine — 現 PM セッションに定時発火
3. webhook 駆動のみ強化 (現状維持 + マージ機構化だけ)
4. PO の巡回頻度を上げる

## Decision Outcome

**Option 1 (3 層構造) を採用。** 2026-08-11 に PO が選択肢形式で承認 (頻度: 1 日 3 回 / 通知: GitHub + push)。

### D1 — 完遂とストール検知は GitHub Actions (セッション不要・LLM 不要)

- **マージ執行** (#253): review-gate workflow は success status を貼った直後、**auto-merge が有効化されている PR** に対して自分でマージ API を叩く。405 (他 check 未完など) は黙ってスキップ。30 分毎の advisory sweep も同じマージ試行を行う (取りこぼしの下限保証 ≤30 分)
- **ストール検知**: 同じ sweep が機械判定できるストールを検知する — 「全 required check 🟢 なのに未マージ 2h 超 (auto-merge 未武装含む)」「needs-human / Proposed ADR が 48h 超停滞」。検知したら該当 Issue / PR にコメントを残し、人間待ちのものは **@yomote メンションで通知** (GitHub 通知)
- **通知は冪等にする**: 閾値超過は解消まで 30 分毎の sweep で再検知され続けるため、既存 advisory と同じ二段防御 (per-PR concurrency + 投稿直前のマーカー再フェッチ確認) を必須とし、時限系は「同一対象への再通知は前回から 24h 以上空ける」クールダウンを置く — 同じコメントと @メンションが sweep のたびに積まれる事態を設計段階で排除する
- auto-merge の**有効化**は引き続き PM の受け入れ意思表示。機構は有効化済みの PR しかマージしない (対象を勝手に広げない)

### D2 — 当番 PM は Routine (毎回新セッション・1 日 3 回)

- claude.ai の Routine が **JST 08:00 / 13:00 / 18:00 に毎回新しいセッションを起こす** (`create_new_session_on_fire`)。どのセッションの生死にも依存しない
- **本項は [ADR 0035](0035-role-split-across-agents-and-actions.md) D1 の「Routine をゼロにする」を一部 supersede する** — LLM 判断を要する定期実務 (当番 PM) に限り Routine を再導入する。0035 が Routine を却下した理由は「生死がリポジトリから見えない (沈黙と正常が区別できない)」だった。本 ADR では当番レポートを Issue コメントとして**毎回** (異常ゼロでも) 残し、その**欠落自体を watchers.json の監視対象にする**ことでこの却下理由を解消する。機械計測を Actions に置く 0035 D1 の本体は維持する
- 当番セッションは CLAUDE.md の規約 (常設承認 / resolve ルール / hub-and-spoke) に従って**実務を回す**: 滞留 PR のマージ、レビュー指摘への対応、自動起票 Issue の診断、needs-human / Proposed ADR の集計
- **実装の分配基準は [ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) (2026-08-10 改訂) の窓口 PM と同一**: レビュー指摘への対応・1〜2 ファイルの修正は当番が直接 push してよく、新規モジュール / 複数ファイル / 調査を伴う作業は subagent (`isolation: "worktree"`) に出す。これは [ADR 0021](0021-parent-session-as-pm-orchestrator.md) D8 の新たな supersede ではなく、0033 が既に改訂した運用を当番セッションにも適用するもの
- **引き継ぎは GitHub に書いて消える**: 巡回レポートを常設 Issue にコメントで残す (冒頭に「🙋 あなたの番」)。異常ゼロでも必ず書く (沈黙と正常を区別する)。Routine の完了 push 通知を PO のスマホに飛ばす
- 当番がやらないこと: リリース PR の merge / deploy、needs-human・保留 PR への操作、design-gate 級の設計判断 (Issue に積んで窓口 PM に回す)
- **作成は PO の 1 回の操作が必要** (承認ゲートの実測により)。web UI から作成し、以後はサーバー側で永続

### D3 — 窓口 PM (現行 [PM] ハブ) は対話に特化

- PO との対話・design-gate・裁定・分配は従来どおり窓口 PM (ADR 0021 のローテーション運用を維持)
- 起床時の運用ルールを追加: **イベントで起きたら、そのイベントの処理に加えて、前回スキャンから 30 分以上経っていればキュー全体 (open PR / needs-human / 進行中エージェント) を見渡す**

### 責務の分界

| 層 | 生死 | 担当 | 止まったら |
| --- | --- | --- | --- |
| A: Actions | サーバー側 (常時) | 機械的な完遂・検知・通知 | status page / watchers.json が検知 |
| B: 当番 PM | 発火ごとに新品 | 判断を伴う実務 | レポートコメントの欠落で検知可能 |
| C: 窓口 PM | 揮発 (ローテーション) | PO との対話・設計判断 | A/B が下限を守る |

## Consequences

### Positive

- 「全部 🟢 なのに止まっている」が構造的に消える (最悪 30 分でマージ、最悪 1 巡回で実務が回る)
- 人間待ちが**通知**になる — PO の巡回が不要になる
- PM セッションの揮発が問題でなくなる (連続性は GitHub の状態が持つ)

### Negative / Risks

- セッション起動コストが 1 日 3 回分増える (足りなければ頻度を上げる方が、常駐より経済的という判断)
- 当番 PM と窓口 PM の二重対応リスク — GitHub のライブ状態を唯一の真実とし、当番は「何を拾ったか」を必ずコメントに残すことで緩和
- Routine の作成・張り替えは人間の操作 (needs-human #254)。無効化されても気づける仕組みとして、当番レポートの欠落を status page の監視対象に足す (watchers.json)

## Links

- 発端の実害: #230 / 2026-08-11 の滞留 4 本
- auto-merge 不発の原因調査: #253
- Routine 作成の人間宿題: #254
- 関連 ADR: [0021](0021-parent-session-as-pm-orchestrator.md) (hub-and-spoke — D8 は [0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) の改訂を当番にも適用、本 ADR での追加変更なし) / [0031](0031-agent-reaches-outside-via-github-actions.md) (外部到達は Actions 経由) / [0035](0035-role-split-across-agents-and-actions.md) (**D1「Routine ゼロ」を本 ADR D2 が一部 supersede** — Accept 時に 0035 の Status 行へ注記する) / [0036](0036-merge-gate-as-required-check-and-pm-cadence.md) (マージの門と PM リズム)
