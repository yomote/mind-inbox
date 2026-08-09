# 0028. 分配は「起票パケットを Issue 本文に残す」形にし、並行の衝突は SessionStart の事前提示と CI で防ぐ

- Status: Accepted (design-gate #4, 2026-08-09)
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

### 訂正: 遮断は「恒久」ではなく「セッション単位」だった (briefing #4, 2026-08-09)

> 本 ADR は当初、上記の実測から「D1 の『user が 1 クリックで起動する』は暫定回避策ではなく**恒久的な運用形態**である」と結論していた。**この一文を撤回する。** 同日の briefing #4 セッションで反例が出たため。
>
> **維持される点**: 本 ADR の「**サーバー単位の遮断**」という読みは正しい。briefing #4 で同一サーバーに対し読み取り・書き込みの両方を投げ、どちらも `-32003` だった。
>
> **撤回する点**: そこから導いた「**恒久的な運用形態**」。稼働中の Routine が反例になっている。
>
> | 確認項目 | briefing #4 セッションでの実測 |
> | --- | --- |
> | `list_triggers` (読み取り) を **UUID 名の登録** (`mcp__bf7c680d-…`) へ | `-32003 requires approval` |
> | `create_trigger` (書き込み) を **同じ登録**へ | `-32003 requires approval` |
> | `list_triggers` を **旧名の登録** (`mcp__Claude_Code_Remote__…`) へ | **成功** (Routine 6 件を取得) |
> | 稼働中 Routine の作成経路 | `cd-watchdog` / `ux-judge` とも `created_via: meta_mcp` = **エージェントが登録済み** |
>
> **さらに絞り込めた (同 briefing 内の追試)**: 上の「旧名なら通る」も、正しくは**時点の差**だった。後からサブエージェントに同じ 2 つを投げさせたところ、旧名は `-32003` ではなく **`No such tool available`** (ツール自体が消えている) を返した。同じセッション内で、Gmail が `mcp__Gmail__*` → `mcp__09495523-…__*` へ貼り替わった通知が届いていたことと合わせると、**セッション途中で MCP サーバーが UUID 名へ再登録され、その前後で可否が変わった**と読むのが最も整合する。
>
> | 時点 | 呼んだ名前 | 結果 |
> | --- | --- | --- |
> | 再登録**前** | `mcp__Claude_Code_Remote__list_triggers` | **成功** |
> | 再登録**後** | `mcp__Claude_Code_Remote__list_triggers` | `No such tool available` |
> | 再登録**後** | `mcp__bf7c680d-…__list_triggers` / `__create_trigger` | どちらも `-32003` |
> | 再登録**後** (サブエージェントから) | 同上 | どちらも `-32003` |
>
> つまり**「サブエージェントなら通る」も誤り**であり、可否を分けていたのは呼び出し元でもツール種別でもなく**サーバー登録名が変わったこと**だった。これは「許可文字列の名前不一致」説を**強める** — ホスト側の許可リストが旧名 (`claude-code-remote`) を指しているなら、UUID 名への貼り替えで一致しなくなり、全ツールが承認待ちに落ちる。
>
> 補足: `claude mcp list` は「**設定済み MCP サーバーなし**」を返し、`~/.claude.json` にも `.mcp.json` にも登録が無い。**これらのサーバーは設定由来ではなくホストが実行時に注入している**。公式ドキュメントの permission 記述はユーザーが `claude mcp add` したサーバーを前提にしているため、注入サーバーに `permissions.allow` がそのまま効くかは**ドキュメントの保証外**である。
>
> 同じ MCP サーバーが**2 つの名前で登録されており、ゲートは登録単位でかかっている**。ツールの種別 (読み/書き) は関係ない。したがって:
>
> - **「エージェントは Routine を登録できない」は環境の恒久的性質ではない** — `created_via: meta_mcp` の Routine が現に 3 本動いている以上、通る経路は存在する
> - `-32003` は「拒否」ではなく「**承認が要る**」。承認 UI を出せないセッション形態では、プロンプトの代わりにこのエラーが返る
>
> ### 進行中の実験: 許可文字列がサーバー名と一致していなかった説 (briefing #4 で設置)
>
> 本 ADR は `permissions.allow` に `mcp__claude-code-remote` を入れて効かなかったことを「permission 層では解けない」証拠としたが、**実際の登録名は `mcp__bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a` であり、許可した文字列がサーバー名と一致していなかった**。記法自体は正しかった (公式ドキュメントの `permissions.allow` は `mcp__<server>` / `mcp__<server>__*` / `mcp__<server>__<tool>` の 3 形式を認める) ので、**名前だけが理由で効かなかった可能性がある**。
>
> UUID の安定性は間接証拠から「安定する」と判断した:
>
> - Gmail のツール名 `mcp__09495523-9ab7-4fc9-8d56-ac6bddc39b49__*` の UUID は、`list_triggers` の応答に含まれる Gmail コネクタの `connector_uuid` と**完全一致**する → UUID 部分は**コネクタ識別子**であり、セッションごとの乱数ではない
> - `bf7c680d-5fdc-**5**ef4-…` は第 3 グループが `5` 始まり = **UUIDv5 (名前空間 + 名前から決定的に導出)**。ランダムな v4 ではないため、同じサーバー名からは同じ値になるはず
>
> **実験の設置**: `.claude/settings.json` の `permissions.allow` に `mcp__bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a` を追加した。設定変更は実行中セッションには反映されない (briefing #4 セッションで追加後に `list_triggers` を呼んだが `-32003` のまま。公式ドキュメントは `.mcp.json` について「新しいセッションが必要」と明記、settings の反映タイミングは記載なし) ため、**判定は次のセッションで行う**。
>
> | 次セッションで `mcp__bf7c680d-…__list_triggers` を呼んだ結果 | 意味 | やること |
> | --- | --- | --- |
> | **成功** | 原因は許可文字列の名前不一致だった。permission 層で解ける | 本 ADR の「permission では解けない」節を訂正し、書き込み系も許可するか PO に諮る |
> | **`-32003` のまま** | permission 層では解けない (本 ADR の元の結論が正しい) | **`.claude/settings.json` の当該行を削除する** — 効かない許可は「許可済みなのになぜ動かないのか」という誤読を生むため (本 ADR が一度削除したのと同じ理由) |
>
> **どちらに転んでも D1 は変えない** — 「環境の可否に設計を依存させない」ことの価値は変わらないため。
>
> 教訓 (エージェント側): briefing #4 は当初この訂正を「読み取りは通るが書き込みは落ちる」と書いた。**2 つの異なる登録に投げた結果を並べて比較していた**のが原因で、比較の条件を揃えないまま結論を出した。ADR 起案時の誤り (1 セッションの観測を環境全体の性質と同一視) と同じ系統の失敗が、訂正する側でも起きた。
>
> **D1〜D3 の決定は変更しない。** D1 はもともと「押すのが親の API 呼び出しか user の 1 クリックかは実装詳細として分岐させる」設計で、どちらに転んでも成立するよう作られている。今回の訂正で変わるのは根拠の強さだけであり、むしろ「環境の可否に設計を依存させない」という Decision Driver が正しかったことの傍証になった。
>
> 残る制約 (briefing #4 で判明): MCP の `create_trigger` が作れるのは cron / 一回限りの Routine だけで、**GitHub event 起点 (PR opened 等) の Routine は web UI でしか作れない**。event 起点を要求する宿題 (#90) は引き続き人間の 1 クリックが要る。

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
