# 0033. 子セッションを起動できない環境では、親が subagent で実装を回す (ADR 0021 条項の改訂)

- Status: Proposed
- Date: 2026-08-09
- Deciders: omoteforlab (方向は 2026-08-09 の対話で選択肢形式により選択。Accept は debrief で)
- Consulted: —
- Informed: —

Technical Story: 2026-08-09、PM セッションが「あなたの番」を 3 件出したところ、PO から **「そんなの自動で自分でできんじゃないの? 私は面倒くさいんです」** という指摘を受けた。

> **採番の補足**: `origin/main` の最大値は 0031 だが、未マージの PR #170 が 0032 を claim しているため 0033 を取った。`adr-number-guard` は「PR ↔ base」しか見ないためこのケースを捕まえられない ([#175](https://github.com/yomote/mind-inbox/issues/175))。

## Context and Problem Statement

[ADR 0021](0021-parent-session-as-pm-orchestrator.md) は hub-and-spoke を定め、その帰結として **「親は開発しない — プロダクトコード・テスト・IaC の変更は必ず子へ分配する」** を規約にした。狙いは親のコンテキストを集約品質のために守ることで、これ自体は正しい。

しかし [ADR 0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) が実証したとおり、**この実行環境では親が子セッションを起動できない** — `claude-code-remote` MCP が丸ごと承認ゲートの内側にあり、`create_session` は `-32003 requires approval` で弾かれる (2026-08-09 に `set_session_title` / `create_session` の両方で再実測)。ADR 0028 D1 は「押すボタンが親の API 呼び出しか user の 1 クリックかは実装詳細」として user のクリックに寄せた。

その結果、**「親は開発しない」という規約が、作業 1 件ごとに user のクリックを生む装置になっている**。ADR 0021 は親のコンテキストを守るために書かれたのに、実際には user の手間を増やす方向に効いてしまった。PO の「面倒くさい」はこの構造への正当な指摘である。

## Decision Drivers

- **user のクリックを減らす** — 承認ゲートで塞がれた経路の穴埋めを user にさせない
- **親のコンテキストを守る** — ADR 0021 の元の狙い。実装の詳細で親を埋めない
- **作業の追跡可能性** — 誰が何をしたかが GitHub に残ること (ADR 0011 / 0028 と同じ原則)
- **環境が変わったら戻せること** — 子セッションが起動できる環境では元の形に戻せる

## Considered Options

- Option A: 現状維持 (作業 1 件ごとに user が子セッションを立てる)
- Option B: **親が subagent に実装させ、親が PR まで持つ**
- Option C: 親が自分で直接実装する
- Option D: GitHub Actions + `claude-code-action` で実装させる

## Decision Outcome

Chosen option: **"Option B"**。user のクリックが 0 になり、かつ subagent は別コンテキストなので親の集約品質も比較的保たれる。

**ADR 0021 は supersede せず、条項の一部だけを置き換える** (ADR 0028 D1 が条項 2 のみ置き換えたのと同じ形)。窓口一元化 (条項 1) / GitHub をライブの真実とする (条項 3・4) / 使い捨てローテーション (条項 7) は維持する。

### 決定の内訳

- **D1 「親は開発しない」を「親は自分でキーボードを持たない」に改める。** 実装は **subagent (`isolation: "worktree"` で git worktree を分ける)** に回し、親は**指示・レビュー・PR 作成・集約**を持つ。親が直接ファイルを書くのはプロセス docs (ADR / journal / CLAUDE.md / Runbook) に限る
- **D2 起票パケットは維持する** ([ADR 0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) D1)。**subagent への指示文がそのままパケットになる**形にし、Issue 本文にも残す — 別のセッションや将来の自分が拾えるようにするため。「対象 Issue / 完遂条件 / **触ってはいけないファイル境界** / CLAUDE.md 参照」を必ず含める
- **D3 子セッションを起動できる環境では、従来どおり子に出してよい。** 判定は「`create_session` が通るか」であり、通るなら子の方が優れる (新品コンテキスト・自分の PR・真の並行)
- **D4 セッション名の規約は親の `[PM]` 接頭辞のみ維持し、子の命名規約は削除する。** 親は user が一覧で窓口を見つけるために要るが、子の命名は subagent 方式では意味を持たない
- **D5 親のローテーション閾値を下げる。** subagent は別コンテキストだが、その**報告と PR レビューは親に積もる**。ADR 0021 条項 7 の「節目 or コンテキスト劣化」に加え、**実装を数件回したら交代を検討する**

### Positive Consequences

- **user のクリックが 0 になる** — 承認ゲートの穴埋めを user にさせない、という本来あるべき形に戻る
- subagent は隔離 worktree で動くので、親の作業ファイルと衝突しない
- 親が指示文を書く時点で境界とスコープが言語化されるため、起票パケットの品質は落ちない
- 環境が変われば D3 で元に戻せる — この ADR は「劣化した環境での運用形態」であり、思想の転換ではない

### Negative Consequences

- **PR の責任が親に集まる。** 子セッションなら自分で PR を出して CI を追ったが、subagent は PR を持たないので親が引き受ける
- **並行度が下がる。** 子セッションは真に並行だが、subagent は親の同時実行数に縛られる
- **subagent は user と対話できない。** 途中で判断が要ると止まるので、親が拾って必要なら user へ上げる必要がある
- **親のコンテキスト消費は増える。** ADR 0021 が守ろうとしたものが部分的に削られる (D5 で緩和)
- 「親は開発しない」という**言葉の分かりやすさが失われる** — 「自分でキーボードを持たない」は境界が微妙で、解釈のブレが起きうる

## Pros and Cons of the Options

### Option A: 現状維持 (user が子セッションを立てる)

- Good, because 子は新品コンテキストで自分の PR を持ち、真に並行できる
- Good, because ADR 0021 の思想がそのまま保たれる
- Bad, because **作業 1 件ごとに user のクリックが要る**。PO が明示的に「面倒」と言っており、これは運用が続かない兆候
- Bad, because クリック待ちの間、作業が止まる

### Option B: 親が subagent に実装させる (採用)

- Good, because user のクリックが 0
- Good, because subagent は別コンテキストなので、親が直接書くより劣化が小さい
- Good, because worktree 隔離で親の作業と衝突しない
- Bad, because PR の面倒を親が見ることになり、親のコンテキストが削られる
- Bad, because subagent が user に直接聞けない

### Option C: 親が直接実装する

- Good, because 最速。中間層が無い
- Bad, because **親のコンテキストが実装で埋まる**。ADR 0021 が避けたかったものそのもの
- Bad, because 集約品質が落ちた親は PM として機能しない

### Option D: GitHub Actions + `claude-code-action`

- Good, because 親のコンテキストを一切消費しない
- Bad, because **`ANTHROPIC_API_KEY` によるメーター課金が原理的に避けられない**。[ADR 0008](0008-pr-review-via-cloud-routine.md) が同じ理由で撤去した判断を崩す
- Bad, because 対話的な軌道修正ができない

## 動作検証

1. subagent が worktree 隔離で動き、親の作業ファイルと衝突しない (初回は #165 の実装で実測する)
2. **user のクリックが 0 で実装 → PR まで到達する**
3. 環境が変わって `create_session` が通るようになったら、D3 に従って子セッションへ戻せる

## Links

- 発端: 2026-08-09 の PO 指摘「そんなの自動で自分でできんじゃないの? 私は面倒くさいんです」
- 初回適用: [#165](https://github.com/yomote/mind-inbox/issues/165) (永続化)
- 関連 ADR: [0021](0021-parent-session-as-pm-orchestrator.md) (条項 2 と「親は開発しない」を本 ADR が置き換え / 条項 6 の子の命名を削除) / [0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) (起票パケット — D2 で維持) / [0008](0008-pr-review-via-cloud-routine.md) (Option D を却下する根拠) / [0031](0031-agent-reaches-outside-via-github-actions.md) (同じ「ゲートの穴を仕組みで埋める」系譜)
- 採番の穴: [#175](https://github.com/yomote/mind-inbox/issues/175)
