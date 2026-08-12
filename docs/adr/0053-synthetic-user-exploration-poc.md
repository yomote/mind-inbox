# 0053. 合成ユーザーによる探索テストは「採点が繋がってから」「週 1 手動 1 回」から始める — 発見件数ではなく再現性を成功条件にする

- Status: Accepted (design-gate, 2026-08-12)
- Date: 2026-08-12
- Deciders: omoteforlab (2026-08-12 の design-gate で承認)
- Consulted: —
- Informed: —

Technical Story: [#304](https://github.com/yomote/mind-inbox/issues/304) が「構想の記録」として残した合成ユーザー探索ループの、**次段階として同 Issue 自身が定義した「小規模 PoC の設計の裁定」**。

## Context and Problem Statement

[#304](https://github.com/yomote/mind-inbox/issues/304) は、現在の 1 日 1 回・固定シナリオ 1 本の UX 観測を、多様なペルソナによる探索的走行へ発展させる構想を記録した。ただし同 Issue は明示的に **「これは構想の記録であり、今すぐ実装しない」** と書き、次段階を「実装完了ではなく、小規模 PoC の**設計**が裁定されること」と定義している。本 ADR がその裁定である。

設計にあたって、既存のループを実測したところ **段が 1 つ断線していた**。

[ADR 0022](0022-autonomous-ux-improvement-loop.md) の 4 段 (観測 → 評価 → 改善 → 裁定) のうち、**評価 (LLM 採点) が実在の担当に接続されていない** ([#354](https://github.com/yomote/mind-inbox/issues/354))。[ADR 0037](0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md) D1 は「日次 tick が `ux-reviewer` を起動する」と宣言したが、その時点で当番 tick はまだ存在せず、後に作られた当番 Routine ([ADR 0040](0040-project-continuity-three-layers.md) D2) の中身は PR 掃き出し・レビュー追従・needs-human 集計だった。**同じ「日次 tick」という名前で、中身が別の仕事だった。** データブランチ上の `ux-judge-score` は 2026-08-10T15:42 の 1 件きりである。

したがって問題は「探索層をどう作るか」だけではない。**断線したループの上に観測を 15 倍積んでよいか**、という順序の問題を含む。

## Decision Drivers

- **#304 の着手条件を守る** — 重複排除・再現確認の評価ゲートを設計してから走らせる。大量実行を先にしない
- **発見件数を成果にしない** — #304 が自ら「最初の成功条件は発見件数ではない」と書いている。数が出ると仕事をした気になるが、再現できない発見は負債になる
- **合成ユーザーを実ユーザーと同一視しない** (#304 の境界)
- **既存の部品を増やさない** — judge / rubric / データブランチ / 追記ヘルパーは既にある ([ADR 0019](0019-independent-judge-agents-security-qa-release.md) / [0041](0041-ux-observations-on-git-data-branch.md))。PoC のために二本目を作らない
- **コストと副作用に上限を置く** ([ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) の予算前提を崩さない)
- **沈黙と正常を区別する** (CLAUDE.md の最頻事故) — 走行が 0 件で終わったことを「異常なし」と読めないようにする

## Considered Options

- Option A: #304 の構想をそのまま実装する (7 ペルソナ × 3 走行を自動で毎日)
- Option B: **採点の接続を Phase 0 に置き、5 ペルソナ × 3 走行を週 1・手動起動 1 回から始める** (採用)
- Option C: 工場の混雑が収束するまで PoC 自体を着工しない

## Decision Outcome

Chosen option: **"Option B"**。

Option A は #304 自身の着手条件 (評価ゲートの設計・上限の決定) を満たさないまま走ることになる。Option C は正しいが、**設計を裁定することと着工することは別**であり、#304 の次段階は前者である。本 ADR は設計を確定させ、着工の順序に Phase 0 を置く。

### 決定の内訳

- **D1 対象環境は dev のみ。** [ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md) の常設環境を使い、prod 相当を新設しない。走行経路は既存の golden-path-monitor と同じ (Playwright で実 UI を操作)

- **D2 最初のペルソナは 5 種。** 選定基準は「`ux-rubric.md` の U1〜U5 が**差を検出できる**か」であって、多様さそのものではない:

  | # | ペルソナ | 突く観点 |
  | --- | --- | --- |
  | 1 | 初回利用者 | baseline (他との比較基準) |
  | 2 | 疲れて短文・曖昧な入力をする | U1 深掘りの問い / U4 具体化の促し |
  | 3 | 長話から複数の困りごとを出す | **Problem 中心 2 層モデル ([ADR 0007](0007-problem-centric-two-layer-domain-model.md)) の分離能力**を直接突く |
  | 4 | 途中で訂正・撤回する | 状態の巻き戻し |
  | 5 | 境界入力 (絵文字 / 長文 / 改行 / 音声認識ミス) | U5 応答長の適切さ / 頑健性 |

  **除外したもの**と理由:

  - **再訪して過去の Problem を再燃させる利用者** — dev に永続化が繋がっていない (実測: どの deploy 経路も `enableCosmos` を渡しておらず既定 `false`)。会話が揮発するため再訪を演じても「忘れているのは仕様」になり、発見にならない。**永続化を dev へ接続した時点で本項は再裁定する** ([ADR 0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md))
  - **連打・更新・途中離脱を行う利用者** — 負荷・耐障害の性質であり、会話品質の rubric では採点できない。別枠

- **D3 操作役と評価役を分ける。** 操作役 (走行エージェント) は **会話を生成するだけで、発見を書かない**。採点は既存の `ux-reviewer` を**新品コンテキストの subagent** として起動し、`.github/claude/ux-rubric.md` を真実とする ([ADR 0019](0019-independent-judge-agents-security-qa-release.md) / [ADR 0022](0022-autonomous-ux-improvement-loop.md) の分離をそのまま使い、judge を新設しない)。

  **さらに再現ゲートを置く** — 発見を直接 Issue 化しない。**同一 commit SHA で 2 回目を走らせ、再現したものだけ**を Issue 候補にする (#304 の着手条件「発見を直接 Issue 化せず、重複排除・再現確認する評価ゲート」)。再現走行は**発見が出たペルソナだけ**に限る

- **D4 データ分離は「アプリ側は不要・記録側は kind で分ける」。** dev に永続化が繋がっていないため、走行が作る会話は揮発する。したがって合成ユーザー専用アカウントや実行後削除は現時点では不要。**永続化を接続した時点でこの判断は再裁定が要る** (#304 の境界「本番相当環境で動かす場合もアカウント・データ・分析軸を実ユーザーから分離する」は生きている)。

  記録は既存のデータブランチ `data/ux-observations` に **新しい kind `ux-explore-record`** で積み、`cicd/scripts/ux-data/append.py` を通す ([ADR 0041](0041-ux-observations-on-git-data-branch.md) D2/D3 の枠にそのまま乗る)。**kind を既存と混ぜない** — 混ぜると「探索は動いているが毎朝のプローブが死んだ」を検出できなくなる (0041 D6 が kind 分離で塞いだ穴を自分で開け直すことになる)。

  **全レコードに `synthetic: true` を必須フィールドとして持たせる** — 合成ユーザーの結果を実ユーザーの行動データと同一視しないという #304 の境界を、運用の心がけではなくスキーマで担保する

- **D5 観測スキーマは既存 `ux-probe-record` を継承する。** 新形式を作らない。#304 が列挙した項目のうち既存に無いものだけを足す: `persona` / 走行種別 (`golden` | `property` | `exploratory`) / 迷った地点 / 発見と重大度 / 再現可否 / 既知 Issue との重複判定

- **D6 上限は「週 1 回 × 15 会話」から始める。** 5 ペルソナ × 3 走行 = 15 会話を 1 回分とし、**毎日にしない** ([ADR 0027](0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) の「改善の起動は週 2 回まで」と同じ発想 — 探索は通常運転ではなく起動するもの)。

  **副作用の禁止事項**: 外部通知・課金操作・データ削除への経路を**作らない**。探索走行は PR を作らない (書き込みはデータブランチへの追記のみ)。予算アラート 月 ¥3,000 ([ADR 0013](0013-standing-low-cost-dev-env-with-auto-deploy.md)) を最終防波堤として維持する

- **D7 成功条件は再現性、停止条件は 3 つ。** 成功条件は #304 の定義をそのまま採る:

  > 同一 commit SHA で再実行したとき、発見事項を人間または別エージェントが再現し、判断できる。

  **発見件数は成功条件にしない。** 停止条件は (1) 予算超過、(2) 再現率が低すぎる (ノイズ源になっている)、(3) 既存ループ (#354 の採点) が赤のまま

- **D8 着工は 3 段階。Phase 0 を飛ばさない。**

  | Phase | 中身 | 抜けてよい条件 |
  | --- | --- | --- |
  | **0** | [#354](https://github.com/yomote/mind-inbox/issues/354) を閉じる (LLM 採点を実在の担当へ接続する) — **接続経路は #354 の案 A (PO が web UI で当番 Routine のプロンプトに 1 行足す)**。エージェントが専用 Routine を新設する案 D は下記のとおり実測で否決された | データブランチに `ux-judge-score` が**新しく積まれること**を実測。「載せた」で終えない |
  | **1** | 5 ペルソナ × 3 走行を `workflow_dispatch` で**手動 1 回**。自動化しない | `explores/YYYY-MM.jsonl` に 15 行積まれること |
  | **2** | 同一 commit SHA で再走し、再現率を数値で出す | 成功条件 (D7) の判定。**ここで初めて Routine 化を裁定する** |

  Phase 0 を飛ばすと、**記録だけが 15 倍に積み、採点されないまま残る**。しかも watchers.json は「探索は動いている」と緑を出すため、止まっていることが見えにくくなる (2026-08-12 の design-gate で PO が指摘)

  **Phase 0 の経路について — 案 D (エージェントが専用 Routine を新設) は実測で否決 (2026-08-12)。** design-gate 直後に案 D を試したところ、`create_trigger` で作った Routine は**リポジトリを掴めない**ことが分かった:

  | 項目 | web UI 製 (`PMルーティン`) | エージェント製 (`create_trigger`) |
  | --- | --- | --- |
  | `sources` (リポジトリ) | あり | **無い** — `source_url` を渡す口が無い |
  | モデル | `claude-opus-5` | 指定不可。`claude-sonnet-5` に落ちる |
  | MCP connector | あり | 付かない (作成時に warning) |

  発火はする (`fire_trigger` で新セッションが起動することも確認) が、**repo を掴めないセッションに Runbook を読ませることはできない**。同じ形で PR レビューの代役 judge Routine も空撃ちしており (指示先の「巡回手順」節が PR #361 のブランチにしかなく main に無い状態で 6 時間ごとに発火)、2 本とも PO 裁定で削除した。

  したがって **`create_trigger` は [ADR 0048](0048-child-sessions-are-usable-again-with-a-one-way-poke-channel.md) D6 の用途 (既に repo を持つ相手セッションへメッセージを届ける) 専用**とし、仕事をさせる Routine は web UI から作る (CLAUDE.md に規約として記載)

## 守るべき資源への到達経路 (全数)

| 資源 | 到達経路 | ガード |
| --- | --- | --- |
| Azure OpenAI (課金) | ①探索走行 15 会話 ②再現走行 ③採点 | 週 1 上限 (D6) / 予算アラート 月 ¥3,000 |
| dev のアプリデータ | 走行が生成する会話 | 永続化未接続のため揮発 (D4)。**接続時に再裁定** |
| リポジトリ write | データブランチ `data/ux-observations` への追記のみ | `append-observation.sh` を通す。**探索は PR を作らない** |
| 外部通知 / 課金操作 / データ削除 | **経路なし** | 作らない (D6) |

## 動作検証 (「設定したか」ではなく振る舞いで / [ADR 0018](0018-runtime-verification-in-the-loop.md))

1. 走行後、`explores/YYYY-MM.jsonl` に `kind: "ux-explore-record"` が **15 行**積まれる。**0 行を「異常なし」と読まない** — 走行が失敗したら run を落とすか、`未検証: 理由` を残す
2. 同一 commit SHA で再走し、1 回目の発見のうち再現したものの**割合を数値で出す**
3. `watchers.json` に探索の trace を 1 行足す — CLAUDE.md の「自動化を足したら watchers.json に 1 行足す。足せないなら作らない」

## Positive Consequences

- **断線 (#354) が探索の前提条件として可視化される** — 「探索を足したい」という動機が、既存ループの修理を先に引く形になる
- 発見が再現ゲートを通るため、**再現しないノイズが Issue 台帳を太らせない**
- 記録が既存のデータブランチ・既存ヘルパーに乗るので、蓄積・監視・トレンドの経路を新設しない
- `synthetic: true` により、将来実ユーザーのデータが混ざったときに**機械的に分離できる**

## Negative Consequences

- **ペルソナ 5 種は「rubric が採点できるもの」に寄っている** — rubric の外にある体験の問題 (導線・情報設計・見た目) は、この PoC では拾えない。Goodhart のリスクは [ADR 0022](0022-autonomous-ux-improvement-loop.md) と同じで、PO の抜き打ち監査で受ける
- **週 1・手動 1 回は遅い。** 探索の価値は反復回数に比例するが、初期は再現性の確認を優先する。Phase 2 の裁定まで速度は上げない
- **再訪ペルソナが欠けるため、Problem の継続性 (v1 の核) は当面テストできない** — 永続化の接続待ち
- kind が 1 つ増え、watchers.json の監視対象も 1 つ増える (運用の面積が広がる)

## Pros and Cons of the Options

### Option A: #304 の構想をそのまま実装する (7 ペルソナ × 3 走行を自動で毎日)

- Good, because 反復回数が最大になり、発見の総量は最も多くなる
- Bad, because **#304 自身の着手条件を満たしていない** (評価ゲート未設計 / 上限未決定 / データ分離未決定)
- Bad, because 採点が断線したまま (#354) 観測だけが 15 倍積む
- Bad, because 再現確認が無いと、発見が Issue 台帳を太らせるだけで終わる

### Option B: 採点の接続を Phase 0 に置き、5 × 3 を週 1・手動 1 回から (採用)

- Good, because #304 の着手条件と完了条件をそのまま満たす
- Good, because 既存の judge / rubric / データブランチをそのまま使い、部品を増やさない
- Good, because 成功条件が再現性なので、**ノイズを量産して「成果が出た」と誤認する経路が塞がる**
- Bad, because 立ち上がりが遅い。Phase 2 の裁定まで自動化されない
- Bad, because 段取りが 3 段になり、途中で止まると中途半端な資産が残る

### Option C: 工場の混雑が収束するまで PoC 自体を着工しない

- Good, because #304 の着手条件「開発工場と open PR の混雑が収束している」に最も忠実
- Good, because 判断コストがゼロ
- Bad, because **設計の裁定と着工を混同している** — #304 の次段階は設計の裁定であり、それは混雑と独立に進められる
- Bad, because 混雑の収束は外部要因 (#345 / #327 / #331) 待ちで、いつになるか読めない

## Links

- Issue: [#304](https://github.com/yomote/mind-inbox/issues/304) (構想の記録 — 本 ADR はその次段階) / [#354](https://github.com/yomote/mind-inbox/issues/354) (Phase 0 の対象) / [#127](https://github.com/yomote/mind-inbox/issues/127) (採点の蓄積先だった Issue)
- ADR: [0022](0022-autonomous-ux-improvement-loop.md) (UX 改善ループ 4 段 — 本 ADR は観測段を拡張) / [0027](0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) (A/B と改変境界 — 上限の考え方を継承) / [0041](0041-ux-observations-on-git-data-branch.md) (データブランチ — 記録先) / [0037](0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md) D1 (断線の出どころ) / [0019](0019-independent-judge-agents-security-qa-release.md) (独立 judge) / [0007](0007-problem-centric-two-layer-domain-model.md) (ペルソナ 3 が突く対象) / [0030](0030-persistence-on-cosmos-db-single-store-behind-bff.md) (永続化 — D2/D4 の再裁定条件)
- Runbook: [ux-probe-judge.md](../runbooks/ux-probe-judge.md)
- 経緯: 2026-08-12 の design-gate (PO 承認)
