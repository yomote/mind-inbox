# 0018. user 宛て依頼は needs-human Issue に一元化し、PO 受信箱 (/po-inbox) で集計する

- Status: Proposed
- Date: 2026-08-07
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: `claude/loop-engineering-bottleneck-v1gg9y` ブランチでの検討。ループエンジニアリング (Claude 駆動開発) の運用が回り始めた結果、**user (PO) の応答がループのクリティカルパスになった**が、「自分が何を頼まれているか / どこを見るべきか」を一覧できる場所が無い課題への対処。

## Context and Problem Statement

ADR 0014 で「承認をどこに挟むか」は構造化されたが、**「user 待ち」の項目そのものは 6 箇所に散在している**:

1. **design-gate の承認待ち** — 同期セッション内で発生し、セッションが上限・中断で切れると依頼ごと消える
2. **Proposed ADR キュー** — `docs/adr/` 内の `Status: Proposed`
3. **無人セッションが積む質問 Issue** — ADR 0014 は「Issue に積む」とだけ定め、ラベル規約が無い (現に #89/#90 が `needs-human` ラベルをアドホックに使い始めている)
4. **Open PR** — レビュー・マージ待ち
5. **debrief の溜まり** — journal マーカー以降のマージ PR と Proposed ADR から導出される「そろそろゼミ」シグナル
6. **journal の持ち越し** — 最新エントリの未消化項目

user は複数セッションを並行で回しており、セッション上限で文脈が切れるたびに「何が自分待ちだったか」が失われる。結果、依頼が放置されてループが止まる (= PO がボトルネック化する)。**「私に何が来ているか」を 1 画面で見る仕組み**をどう作るかを決める必要がある。

## Decision Drivers

- **user の応答時間がループのクリティカルパス** — 依頼の「発見コスト」を最小化する。探させない
- **「真実は 1 か所」ドクトリン維持** — 新しい状態置き場・二重管理を作らない (ADR 0011 の教訓)
- **規律でなく仕組み** (ADR 0014 と同じ Driver) — チャットで頼んだだけの依頼はセッションと共に消える。消えない形式を既定にする
- **優先度が見える** — 全件フラットな一覧は結局読まれない。「ループを止めているもの」が先頭に来ること
- **運用コスト最小** — 個人開発。ツール・課金・シークレットを増やさない

## Considered Options

- Option A: **書き込み規約 (`needs-human` Issue) + 読み取りは集計ビュー (`/po-inbox` skill)**
- Option B: 全依頼を Issue にミラーする (Proposed ADR も含めすべて Issue 化)
- Option C: GitHub Projects に「user 待ちレーン」を作る
- Option D: 定期レポートのプッシュ配信のみ (オンデマンドビューなし)

## Decision Outcome

Chosen option: **"Option A"**。書き込み側と読み取り側の 2 層で構成する。

### 1. 書き込み規約 — 非同期の user 宛て依頼は `needs-human` Issue に積む

エージェントが user に**非同期で**何かを求めるとき (判断・web UI 等の手作業・確認) は、チャットで言うだけで終わらせず **GitHub Issue に積む**:

- ラベル `needs-human` + タイトル接頭辞 `[needs-human]` (既に #89/#90 で始まっている運用の正式化)
- 本文の冒頭に 2 行を必須にする: **種別** (判断 / 作業 / 確認) と **「放置されると何が止まるか」1 文** (`/status` の「無いと何が静かに通るか」と同じ思想)
- ループを止めている依頼には `P1` を付ける (既存の優先度ラベルを流用)
- 依頼が果たされたら、対応した側 (user またはエージェント) が close する
- ADR 0014 の「無人セッションは質問を Issue に積む」はこの規約に統合される (ラベル無しの質問 Issue を作らない)

**ミラー禁止**: Proposed ADR・debrief の溜まり・journal 持ち越しは Issue 化**しない**。真実は docs 側にあり、読み取りビューが都度拾う。Issue にするのは「Issue にしか置き場が無い依頼」だけ。

### 2. 読み取りビュー — `/po-inbox` skill が毎回ライブ集計する

skill は状態を持たず (ADR 0011 / `/status` と同型)、起動ごとに全ソースを引いて 1 画面にまとめる:

| ソース | 引き方 |
| --- | --- |
| `needs-human` Open Issue | GitHub (ラベルフィルタ) |
| Open PR (レビュー・マージ待ち) | GitHub |
| Proposed ADR | `docs/adr/` を grep |
| debrief の溜まり | journal マーカー以降のマージ PR 数 + Proposed ADR 数 → 閾値で開催提案 |
| journal 持ち越し | `docs/debrief/journal.md` 最新エントリ |

出力は優先度 4 段 (🔴 ループ停止中 → 🟠 放置すると腐る → 🟡 まとめて非同期で可 → ⚪ 背景) に並べ、**「次の一手」を 1 つ名指す**。`/status` が「プロジェクトはどこまで進んだか」に答えるのと対で、`/po-inbox` は「**何があなたを待っているか**」に答える。

**スコープ外 (後続判断)**: 定期 Routine で `/po-inbox` を回し、件数が 0 でないときだけプッシュ通知する案。Routine 登録は user の操作・継続的な通知という不可逆寄りの変更なので、`needs-human` Issue として積んで user の判断を仰ぐ (この規約自体のドッグフーディング)。

Option B は Proposed ADR の Status と Issue の状態が二重管理になり、手書きチェックリストがドリフトした ADR 0011 の教訓を繰り返す。Option C は Projects の責務 (実行状態のダッシュボード) に「user 宛て依頼」という別軸を混ぜ、しかも board 操作は web UI 手動で自動化できない。Option D はプッシュだけだと「今、何が自分待ちか」というオンデマンドの問いに答えられない — pull のビューが先で、push はその上に載せる後続。

### Positive Consequences

- 「自分待ちは何か」が 1 コマンドで出る。セッションが切れても依頼は GitHub に残る
- 新しい状態置き場を作らない — 散在した 6 ソースは各自の真実の場所に留まり、ビューが集計するだけ
- 優先度 4 段 + 次の一手 1 つで、「どこを見るべきか」の判断自体を仕組みに移す
- 書き込み規約は既に始まっていた運用 (#89/#90) の追認なので導入コストがほぼゼロ

### Negative Consequences

- 依頼側の規律に依存する — エージェントが Issue に積み忘れた依頼はビューに出ない (CLAUDE.md と skill への明文化で緩和。ただし規律ゼロにはできない)
- `needs-human` の粒度が荒れると受信箱がノイズ化する (「放置されると何が止まるか」1 文の必須化で足切り)
- ビューは pull 型なので、user が開かなければ気づかない (push は Routine 後続判断でカバー予定)
- skill がソースを 1 つ引き損ねると「無い」と誤報する — 件数の整合チェックを出力に含めて緩和

## Pros and Cons of the Options

### Option A: 書き込み規約 + 集計ビュー (採用)

- Good, because 真実の置き場を増やさず、ビューだけを追加する (ADR 0011 と同じ構図)
- Good, because Issue 規約は既存運用の正式化で、GitHub 内で完結する
- Bad, because 依頼を Issue に積む規律への依存が残る

### Option B: 全依頼を Issue にミラー

- Good, because 「GitHub の needs-human だけ見ればよい」単純さ
- Bad, because Proposed ADR の Status と Issue の open/close が二重管理になりドリフトする
- Bad, because ミラー Issue の作成・同期そのものが新しい運用負荷になる

### Option C: Projects に user 待ちレーン

- Good, because 既存の board に載り、見る場所が増えない
- Bad, because Projects の責務 (実行状態) と混線する (ADR 0011 の線引きを壊す)
- Bad, because board 操作は web UI 手動で、エージェントから自動化できない

### Option D: 定期プッシュ配信のみ

- Good, because user が見に行かなくても届く
- Bad, because 「今、自分待ちは何か」のオンデマンドの問いに答えられない
- Bad, because 配信間隔の外で発生した依頼の発見が遅れる。pull ビューの代替にならない

## Links

- 関連 ADR: [0011](0011-github-projects-as-execution-dashboard.md) (真実の所在の分担 — 同じ構図) / [0014](0014-design-comprehension-gate-and-debrief.md) (承認の構造化 — 本 ADR はその「依頼の発見」側) / [0008](0008-pr-review-via-cloud-routine.md) (プロセス系判断の系譜)
- skill: [`po-inbox`](../../.claude/skills/po-inbox/SKILL.md)
- 先行事例: Issue #89 / #90 (`needs-human` のアドホック運用)
