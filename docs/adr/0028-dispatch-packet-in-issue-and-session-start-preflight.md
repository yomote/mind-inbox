# 0028. 分配は「起票パケットを Issue 本文に残す」形にし、並行の衝突は SessionStart の事前提示と CI で防ぐ

- Status: Accepted (design-gate #4, 2026-08-09) / 一部 Superseded by [0035](0035-role-split-across-agents-and-actions.md) (2026-08-10 — 起票パケットの置き場所のみ Issue → PR。必須項目は不変)
- Date: 2026-08-09
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: [Issue #159](https://github.com/yomote/mind-inbox/issues/159) — hub-and-spoke ([ADR 0021](0021-parent-session-as-pm-orchestrator.md)) の前提が実行環境で成立していないことが 2026-08-09 に実測された。本 ADR は 0021 を **supersede しない** — 窓口一元化の規約は維持したまま、条項 2 (分配の手段) を置き換え、欠けていた衝突防止を足す。

## Context and Problem Statement

ADR 0021 は「親 (PM) セッションが子セッションへ分配する」ことを既定の運用と定めた。しかし **web から起動したセッションはそれ自身が子セッションとして起動する**ため、分配側になれない。

### 実測 (2026-08-09)

```text
CLAUDE_CODE_CHILD_SESSION=1
CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE=cloud_default
```

claude-code-remote MCP サーバーのツールは、書き込み系だけでなく**読み取り専用まで含めて全滅**する:

| ツール | 種別 | 結果 |
| --- | --- | --- |
| `create_session` / `create_trigger` | 書き込み | `-32003 MCP tool call requires approval` |
| `list_triggers` / `list_environments` | **読み取り専用** | 同上 |

読み取り専用も同じエラーになることから、ツール個別の permission ではなく**サーバー単位の遮断**と判断する (子セッションが孫セッションや定期実行を作れないようにするガード)。

### permission では解けないことを実証した (2026-08-09)

当初は「`permissions.allow` に `mcp__claude-code-remote` を足せば解けるかもしれない」として `.claude/settings.json` に 1 行入れ、切り分けのために残した。その後の検証で**否定された**:

1. 同じ `.claude/settings.json` に登録した SessionStart フックが**実際に発火した** — 設定ファイルが読まれていることの直接の証拠
2. その状態で `list_triggers` を再度呼んだが、**同じ `-32003` で失敗した**

設定は読まれている。それでも通らない。したがって **permission 層の問題ではなく、プラットフォーム側のゲートで確定**。効かないと分かった `permissions.allow` の行は「許可済みなのになぜ動かないのか」という誤読を生むため**削除した**。

この確定により、D1 の「user が 1 クリックで起動する」は暫定回避策ではなく**恒久的な運用形態**である。

### 壊れているのは 2 つ、しかも独立している

| | 内容 | 環境依存 | 2026-08-09 の実害 |
| --- | --- | --- | --- |
| ① 分配できない | 親が子セッションを起動できない | **する** | 並行度ゼロ / 親が実装 (ADR 0021 条項 4 違反) |
| ② 並行作業が衝突する | 誰が起動しても同時に動けば衝突する | **しない** | 保守性 Phase 3 の重複作業 / ADR 番号の二重採番 |

**②は①が直ると悪化する** — 並行度が上がるほど衝突機会が増える。したがって②は①の判定を待たずに解く必要がある。

② が「規約を書けば防げる」ものでないことは実証されている: 当日のセッションは CLAUDE.md と ADR 0021 を読んだ上で `origin/main` を確認せず重複作業をし、ADR 採番も `docs/adr/README.md` の「`ls docs/adr/` で最大番号 +1」という記述どおりに**ローカル基準**で取って衝突させた (debrief #3 の「旧 0015 → 0019」に続く 2 回目)。

## Decision Drivers

- **規律に依存しない** ([ADR 0014](0014-design-comprehension-gate-and-debrief.md) の Driver) — 「気をつける」で防げないことは実証済み
- **環境の可否に設計を依存させない** — ゲートが開いても閉じても同じ運用が成立すること
- **窓口の一元化を壊さない** — ADR 0021 の条項 1・3 (user の窓口は親 1 本 / 子は直接報告しない) は今日も有効に機能していた
- **既存機構の再利用** — needs-human キュー ([ADR 0020](0020-hitl-choice-format-and-needs-human-queue.md)) / Issues = 実行状態 ([ADR 0011](0011-github-projects-as-execution-dashboard.md)) / CI ゲート

## Considered Options

- Option A: **起票パケットを成果物にし、押す人を分岐させる** (採用)
- Option B: 並行セッションを諦め、in-session subagent で並行させる
- Option C: 親が全部直列で実装する (2026-08-09 に実際に起きた状態)
- Option D: ゲートが開くまで何もしない

## Decision Outcome

Chosen option: **"Option A"**。3 点を定める。

### D1. 分配は「起票パケットを Issue 本文に残す」ことで完了とする

ADR 0021 条項 2 の「親が子セッションを起動する」を、**「親が起票パケットを Issue 本文に書く」**に置き換える。パケットの中身は 0021 と同じ 4 点 — (a) 対象 Issue/PR、(b) 完遂条件 (振る舞いで書く / PR merge まで追従)、(c) **触ってはいけないファイル境界**、(d) CLAUDE.md 規約への参照 — に (e) 報告のしかた (user に直接報告せず PR / Issue に残す) を加える。

**誰が起動ボタンを押すかは実装詳細として分岐させる**:

- 環境が許すなら親が `create_session` で起動する
- 許さないなら **user が UI から 1 クリックで起動する** — Issue を指すだけで起動できる状態になっているため、追加の説明は要らない

分岐の前後 (パケットを書く / 子が PR・Issue に残す / 親がライブ状態から集約する) は環境によらず同一。**API 呼び出しという揮発物ではなく、リポジトリに残る成果物を分配の実体にする**ことで、環境の可否から設計を切り離す。

これは ADR 0020 の「人間にしかできない 1 クリックは needs-human に積む」と同型であり、分配も同じ扱いに揃える。

### D2. 並行の衝突は SessionStart の事前提示で防ぐ

セッション開始時に自動で以下を提示する (`.claude/hooks/` の SessionStart フック):

- `origin/main` の最新 sha と、現在のブランチとの差 (何コミット遅れているか)
- **open な PR の一覧** (誰が何を進行中か)

**規約ではなく仕組み**である点が本質。当日のセッションは規約を読んだ上で見落とした。フックは見落としようがない。

フックは**情報を提示するだけ**で、セッションの起動には一切関与しない (D1 とは独立の部品)。

### D3. ADR 採番の衝突は CI で落とす

- `docs/adr/README.md` の採番手順を「`ls docs/adr/` の最大番号 +1」から **「`origin/main` の最大番号 +1」**に改める
- **CI で番号の重複を検出**する。同一番号の ADR が 2 つ存在する、または PR が追加した番号が `origin/main` に既存なら赤にする

手順の記述だけでは 2 回衝突している (0015→0019 / 0026→0027) ため、機械で落とす。

## Positive Consequences

- 分配が環境の可否に依存しなくなる。ゲートが開けば自動化され、閉じたままでも運用は止まらない
- 起票パケットが Issue に残るため、**分配の意図が後から読める** (API 呼び出しは履歴に残らなかった)
- 衝突防止が並行度の増加に先行して入る
- ADR 0021 の窓口一元化はそのまま維持される (置き換えるのは条項 2 のみ)

## Negative Consequences

- ゲートが閉じている間、分配のたびに user の 1 クリックが要る (needs-human と同じコスト)
- SessionStart フックの実行分だけセッション開始が遅くなる (`git fetch` 1 回程度)
- パケットを Issue 本文に書く手間が増える (ただし 2026-08-09 の #154 / #155 で実践済みで、追加コストは小さいことを確認している)

## Pros and Cons of the Options

### Option A: 起票パケットを成果物にする (採用)

- Good, because 環境の可否から設計が切り離される (どちらに転んでも同じ形)
- Good, because 分配の意図がリポジトリに残る
- Bad, because ゲートが閉じている間は user の手が要る

### Option B: in-session subagent で並行させる

- Good, because 環境のゲートを回避できる
- Bad, because subagent は PR の所有・レビュー追従・長時間の作業に向かない (ADR 0019 の judge のような読み取り中心の用途とは要件が違う)
- Bad, because 窓口一元化の利点は得られるが、親のコンテキストを消費し続ける

### Option C: 親が全部直列で実装する

- Bad, because 2026-08-09 に実際に起きた状態そのもの。並行度ゼロ、親のコンテキスト枯渇、ADR 0021 条項 4 (親は開発しない) の違反
- Bad, because 規約が守れない構造を放置することになる

### Option D: ゲートが開くまで何もしない

- Bad, because ② (衝突) はゲートと無関係に発生し、しかもゲートが開くと悪化する

## Links

- 発端: [Issue #159](https://github.com/yomote/mind-inbox/issues/159) (実測記録)
- 補足対象: [ADR 0021](0021-parent-session-as-pm-orchestrator.md) (条項 1・3・4 は維持、条項 2 を置き換え)
- 関連: [0020](0020-hitl-choice-format-and-needs-human-queue.md) (needs-human = 人間の 1 クリック) / [0014](0014-design-comprehension-gate-and-debrief.md) (規律に依存しない) / [0011](0011-github-projects-as-execution-dashboard.md) (Issues = 実行状態) / [0018](0018-runtime-verification-in-the-loop.md) (振る舞いで書く)
- 実践例: [#154](https://github.com/yomote/mind-inbox/issues/154) / [#155](https://github.com/yomote/mind-inbox/issues/155) の Issue 本文 (起票パケットの初例)
