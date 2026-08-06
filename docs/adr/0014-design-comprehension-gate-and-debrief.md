# 0014. 設計理解ゲートとゼミ型デブリーフで、user の意思決定・学習をループに組み込む

- Status: Proposed
- Date: 2026-08-06
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: `claude/loop-engineering-design-ifddb6` ブランチでの検討。ループエンジニアリング (Claude 駆動開発) の運用開始後、「実装は進むが user の技術理解と意思決定が置き去りになる」課題への対処。

## Context and Problem Statement

Claude 駆動のループで実装が進む一方、**「なぜこのフレームワークか」「なぜこの設計か」という判断の理解と、user 自身の意思決定がループに挟まらない**。毎回同期対話で確認するとループが止まり、確認しないと「欲しいものができているか」「アーキテクチャを自分が理解できているか」が検証されないまま蓄積する。user は技術学習の場としてもこのリポジトリを使いたい。理解・承認・学習をループのどこに・どう挟むかを決める必要がある。

## Decision Drivers

- **user の意思決定が構造的に挟まる** — 「気づいたら決まっていた」を無くす。承認は形骸化させない
- **技術学習が実物ベースで起きる** — 別教材でなく、自リポジトリのコードと判断を教材にする
- **ループの稼働を止めない** — 同期点 (user を待って止まる箇所) は意図した最小限に絞る
- **規律に依存しない** — user がサボっても未承認の判断が「静かに流れて消える」ことがなく、キューとして見え続ける
- **「真実は 1 か所」ドクトリン維持** — 判断=ADR / 状態=GitHub の既存分担に新しい置き場所を増やさない

## Considered Options

- Option A: **同期ゲート (design-gate) + 非同期ゼミ (debrief) + Proposed ADR キュー + オンデマンド可視化**
- Option B: 毎回の同期対話 (すべての判断をその場で説明・承認)
- Option C: 完全自律 (対話なし。docs を残し、user が任意で読む)
- Option D: リポジトリ外の学習チャネル (別途教材・勉強会形式)

## Decision Outcome

Chosen option: **"Option A"**。理解・承認・学習を 4 つの部品に分解し、**同期点は「設計 → 実装の境界」1 か所だけに絞る**。それ以外はすべて非同期またはオンデマンドにしてループの稼働を保つ。

| 部品 | タイミング | 同期性 | 実装 |
| --- | --- | --- | --- |
| **設計理解ゲート** — 設計を可視化して提示し、user の理解確認と承認を取ってから実装に入る | 設計 → 実装の境界 (必須) | 同期 | skill [`design-gate`](../../.claude/skills/design-gate/SKILL.md) |
| **ゼミ型デブリーフ** — 前回以降のマージ PR / Proposed ADR をまとめて解説し、理解度を対話で確認し、ADR を Accept/Reject する | マージが溜まったら随時 | 非同期 (まとめて) | skill [`debrief`](../../.claude/skills/debrief/SKILL.md) |
| **即席可視化** — 「あれなんだっけ?」に真実ソースから図解で即答する | いつでも | オンデマンド | skill [`explain`](../../.claude/skills/explain/SKILL.md) |
| **HTML ステータスレポート** — 開発状況を 1 枚の HTML レポートに可視化する | いつでも | オンデマンド | skill [`status`](../../.claude/skills/status/SKILL.md) の HTML モード |

支える規律は 2 つ:

1. **エージェント起案の ADR は必ず `Status: Proposed` で入れる**。Accepted へ遷移させるのは user のみ (design-gate または debrief の場で)。ループは Proposed の判断を前提に実装を進めてよい (コードは可逆)。これにより「判断の記録」と「判断の承認」が分離され、未承認の判断は Proposed キューとして見え続ける
2. **無人セッション (Routine 等、user が応答できない場) では**、不可逆な判断 (one-way door: DB スキーマ破壊的変更 / 外部サービス・課金追加 / 公開 API の形 / データ削除) はゲートを通せないため実装に入らず質問を Issue に積む。可逆な判断 (two-way door) は Proposed ADR を書いて進み、次の debrief で追認を受ける

セッション記録と「前回以降」の起点マーカーは [`docs/debrief/journal.md`](../debrief/journal.md) に置く (決定と学びの累積ログ = docs の領分)。

Option B は今まさに起きている問題 (ループが止まる) で Driver 3 に反する。Option C は Driver 1/2 に反する — docs は読まれなければ承認でも学習でもない。Option D は学習が実物から乖離し Driver 2/5 に反する。

### Positive Consequences

- user の承認が「設計 → 実装」の境界に構造的に入り、「欲しいものと違うものができる」リスクが早期に潰れる
- 未承認の判断が Proposed ADR として可視化され、サボっても消えない (規律でなく仕組み)
- 学習が自リポジトリのライブなコード・判断を教材にするので、勉強と意思決定が同じセッションで済む
- 解説・可視化は毎回ライブの真実ソースから生成するので陳腐化しない (skill に内容をハードコードしない)
- ループの同期点が 1 か所に限定され、それ以外の稼働は止まらない

### Negative Consequences

- design-gate は意図的な同期点であり、そこではループが user を待って止まる (user 要望による受け入れ済みトレードオフ)
- journal の更新は skill の手順に依存する (手順から漏れるとマーカーがずれる)
- 理解度確認の問いはモデル生成であり、形式的なクイズに堕ちるリスクがある (「答え合わせ」でなく「理解の穴の発見」を目的に置いて緩和)
- Proposed ADR が溜まりすぎると debrief が重くなる (溜まったら早めに開催する運用でカバー)

## Pros and Cons of the Options

### Option A: 同期ゲート + 非同期ゼミ + Proposed キュー (採用)

同期点を設計→実装の境界 1 か所に絞り、残りを非同期化する。

- Good, because 意思決定・学習・稼働維持の 3 つを両立する
- Good, because ADR 0008 (レビューの別軸分離) と同じ発想で、既存ドクトリンに素直に乗る
- Bad, because skill 3 つ + 規律 2 つと部品が多く、運用の習熟が要る

### Option B: 毎回の同期対話

すべての判断をその場で user に説明し承認を取る。

- Good, because 理解と承認の粒度が最も細かい
- Bad, because ループが判断のたびに止まる (現に起きている課題そのもの)

### Option C: 完全自律

対話なしで進め、docs を残すのみ。

- Good, because ループの稼働が最大化される
- Bad, because 承認が挟まらず「欲しいものと違う」が終盤まで発覚しない
- Bad, because 読まれない docs は学習にならない

### Option D: リポジトリ外の学習チャネル

技術学習を別教材・別セッションで行う。

- Good, because 体系的な学習カリキュラムを組める
- Bad, because 自リポジトリの実判断と乖離し、意思決定に接続しない

## Links

- 関連 ADR: [0008](0008-pr-review-via-cloud-routine.md) (レビューの別軸分離 — 同型の発想) / [0011](0011-github-projects-as-execution-dashboard.md) (真実の所在の分担)
- 記録: [`docs/debrief/journal.md`](../debrief/journal.md)
- skills: [`design-gate`](../../.claude/skills/design-gate/SKILL.md) / [`debrief`](../../.claude/skills/debrief/SKILL.md) / [`explain`](../../.claude/skills/explain/SKILL.md) / [`status`](../../.claude/skills/status/SKILL.md)
