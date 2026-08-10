# 0035. 開発ループの役割を分け、それぞれを「生死が見える場所」に置く

- Status: Proposed
- Date: 2026-08-10
- Deciders: yomote (PO) / 実装セッション
- Related: [ADR 0008](0008-pr-review-via-cloud-routine.md) (PR レビュー Routine) / [ADR 0019](0019-independent-judge-agents-security-qa-release.md) (独立 judge) / [ADR 0021](0021-parent-session-as-pm-orchestrator.md) (hub-and-spoke) / [ADR 0031](0031-agent-reaches-outside-via-github-actions.md) (外の事実は Actions 経由) / [ADR 0032](0032-use-case-acceptance-tests-against-real-wiring.md) (L3-real)

Technical Story: 2026-08-10 の対話 (報告会 #7)。PO の「自動化の仕掛かり中が多すぎて把握できない」から始まり、**無人で回るはずの仕組みが 4 本すべて沈黙していた**ことが判明した。

## Context and Problem Statement

この日、実測で以下が分かった。

| 仕組み | 実績 |
| --- | --- |
| PR レビュー Routine | 同日の PR 8 本に **1 件も反応なし** |
| cd-watchdog Routine | deploy / golden-path の赤 5 回に無反応 (最後の起票は 8/8) |
| ux-judge Routine | **一度も投稿していない** |
| maint-check Routine | 今朝初動作。10 件検出したが**そのまま放置** |

共通点は **claude.ai 側にいて、実行履歴がリポジトリに残らない**こと。加えて「異常がなければ何も残さない」設計だったため、**沈黙と正常が同じ見え方になっていた**。

さらに [ADR 0008](0008-pr-review-via-cloud-routine.md) は Negative Consequences に「開発と**同じレート枠・日次実行上限を共有**する (課金ではなく拒否)」と予告しており、**開発を速く回すほど無人の仕組みが飢える**構造だった可能性が高い (未確定。#194 の確認待ち)。

同時に、役割の重なりも問題になった。**実装した本人がレビューもする**形になっており、[ADR 0019](0019-independent-judge-agents-security-qa-release.md) の「実装者と judge を分ける」が PR レベルでは機能していなかった。

## Decision Drivers

- **止まったことに気づけること** — 今日の全障害に共通する根本
- **実装者とレビュアーを分ける** (ADR 0019 の原則を PR レベルにも適用)
- **追加課金を増やさない** (ADR 0008 の driver を継承)
- **長期クレデンシャルを増やさない** (ADR 0009 / 0031 の driver を継承)
- **PO が問題を「見つける」側に回らなくて済むこと**

## Decision Outcome

**役割を 6 つに分け、それぞれを「生死が見える場所」に置く。**

| 役割 | 担い手 | 起動 | 生死の見え方 |
| --- | --- | --- | --- |
| **PO** | 人間 | — | — |
| **PM** | Claude Code セッション | PO の一言 / 定期 | 対話そのもの |
| **実装** | **Claude Code** | PM が Issue を選んで着手 | PR |
| **技術レビュー** | **Codex** (`@codex review`) | PR で自動 | PR にコメントが付く |
| **セキュリティレビュー** | **Codex** (`@codex security review`) | 節目で PM が指名 | 同上 |
| **受け入れレビュー** | **PM (Claude)** | PR ごと | PR にコメントが付く |
| **QA** | qa-reviewer subagent | **節目で PM が判断して回す** (毎回ではない) | 状況ページに実施痕跡 |
| **監視** | **GitHub Actions** | イベント / 定期 | run 履歴が必ず残る |

### 決定の内訳

- **D1 Routine をゼロにする。** claude.ai 側の定期実行は生死が見えないので、無人の見張りには使わない。`ux-judge` → `ux-eval`、`maint-check` → `debt-check` として Actions へ移す
- **D2 cd-watchdog は廃止する。** 別の見張りを置くのではなく、**落ちた workflow 自身が Issue を立てる**。見張りが黙る問題が原理的に消える (黙っている = 落ちていない)
- **D3 レビューは 2 種類に分ける。** 「**やってほしいことがそこにあるか**」(意図との一致) は意図を持つ PM が見る。「**コードとして正しいか / 危なくないか**」は意図を知らない別モデルが見る。前者は意図を知らないと判定できず、後者は知らない方がよい
- **D4 実装は Claude Code、技術レビューは Codex。** 実装者とレビュアーが別のモデル系統になる (同じモデルは同じ盲点を持つ)。加えて Codex は**枠が別プール**なので、開発量に影響されない
- **D5 Claude → Codex は PR コメント経由で呼ぶ。** 直接呼ぶには API キーか `auth.json` が要り、ADR 0008 / 0009 の driver を折る。**GitHub は両者が認証情報なしで書き込める唯一の共有媒体**であり、迂回策ではなく構造上そこしかない
- **D6 QA とセキュリティは「毎回」ではなく「節目」で回す。ただし回した痕跡を残す。** 現状 release-gate はリリース PR でしか起動せず、リリース PR は**過去 0 件**のため一度も使われていない。トリガーを「ユーザーに見える振る舞いが変わったとき」に変え、判断は PM が持つ
- **D7 Issue と PR の役割を分ける。** **Issue = 解きたい問題 / PR = 1 つの解の単位**。作業指示は PR に書く。1 Issue が複数 PR に分かれても破綻しない ([ADR 0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) の起票パケットを PR 側へ移す)

### Positive Consequences

- 無人の仕組みの生死が、状況ページから見えるようになる
- 実装とレビューがモデル系統ごと分かれ、独立性が上がる
- **実装を外に出さない代わりに、レビューを外に出すことで Claude の枠消費が下がる** — 枠の食い合いが原因なら、それ自体が対処になる
- 追加課金ゼロ・長期クレデンシャル増加ゼロを維持

### Negative Consequences

- **Codex は GitHub の Issue から自動起動できない** (Linear / Slack / PR / アプリのみ)。Issue 駆動の自動化には PR を先に作る必要がある
- Codex の枠は「週に数回の集中セッション程度」とされ、**全 PR の自動レビューには足りない可能性**がある。機能 PR に絞る運用が要る
- claude.ai の Routine 設定は引き続きリポジトリ管理外。**移行が終わるまでは二重管理**
- **レビューを待つ仕組みがまだ無い** — 現状 CI が緑になり次第マージしており、レビューが付いても誰も待っていない。ここを決めないと Codex を入れても結果が変わらない (本 ADR では未決)

## Considered Options

- **A: 現状維持 (Routine 中心)** — 生死が見えない問題が残る。今日の実測で 4/4 が沈黙しており、不採用
- **B: Codex を実装の主役にする** — 能力面での否定材料は無い。ただし **GitHub Issue からの自動起動経路が無い**ため、Issue 駆動の着手が人手になる。将来 Linear を使うなら再検討の価値あり
- **C: 採用案 (実装 Claude / レビュー Codex / 監視 Actions)** — 起動経路が既存の流れに素直で、独立性も満たす
- **D: Codex CLI を Actions から直接叩く** — API キー (従量課金) か `auth.json` (公開リポジトリで公式に非推奨) が要る。driver 2 つを折るため不採用

## 動作検証 (この ADR が実装されたと言える条件)

1. 状況ページに Routine が 0 本、Actions が N 本並び、**すべて 🟢 か 🔴 で判定できる** (❓ が無い)
2. `deploy` を意図的に落としたとき、**その run 自身が Issue を立てる**
3. 機能 PR に `@codex review` の結果が付き、**PM の受け入れレビューと別々のコメントとして残る**
4. QA を回した PR で、**回した痕跡が状況ページに出る**

## 未決 (次の design-gate へ)

- **レビューを待つ仕組み** (ブランチ保護 / マージ手順への組み込み)
- Codex の指摘の質 — 最初の 3〜5 本で「Claude が見落としたものを拾えたか」を分類して測る
- `codex mcp` 等による直接接続の可否 (認証が要る見込みで結論は変わらないが未確認)
