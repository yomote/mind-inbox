# 0054. 止まる・空回り・誤った不可能性 — エージェント運用の 3 症状を規約ではなく機構で塞ぐ

- Status: Proposed
- Date: 2026-08-13
- Deciders: yomote (PO) / 窓口 PM セッション
- Related: [ADR 0043](0043-pm-self-driving-mode.md) (PM 自走モード — D1/D3 を本 ADR が実効化) / [ADR 0048](0048-child-sessions-are-usable-again-with-a-one-way-poke-channel.md) (分配の 3 択) / [ADR 0040](0040-project-continuity-three-layers.md) (継続性 3 層) / [ADR 0036](0036-merge-gate-as-required-check-and-pm-cadence.md) (マージの門と WIP 上限) / [ADR 0020](0020-hitl-choice-format-and-needs-human-queue.md) (needs-human キュー) / [ADR 0018](0018-runtime-verification-in-the-loop.md) (振る舞いで検証する)

Technical Story: PO 申告 2026-08-13「連携がうまくいっていない / もっと自律的に / とにかく止まる / 表面上のところで無駄に時間を使う / アクセスできるはずなのに『できない』と言う」。実測は [`docs/reviews/agent-ops-analysis-2026-08-13.md`](../reviews/agent-ops-analysis-2026-08-13.md)。先行: [#378](https://github.com/yomote/mind-inbox/issues/378) / [#351](https://github.com/yomote/mind-inbox/issues/351) / [#348](https://github.com/yomote/mind-inbox/issues/348) / [#356](https://github.com/yomote/mind-inbox/issues/356)

## Context and Problem Statement

実測で分かったことは 3 つある。

1. **止まっているのは作業ではなく「完遂の最後の一歩」** — 人待ちが 23 件あり、そのうち PO にしか押せないのは 5〜7 件。残りは**機構が壊れて人に落ちてきたもの** ([#327](https://github.com/yomote/mind-inbox/issues/327) / [#253](https://github.com/yomote/mind-inbox/issues/253) / [#250](https://github.com/yomote/mind-inbox/issues/250) / [#345](https://github.com/yomote/mind-inbox/issues/345) 等)。両者が同じ `needs-human` ラベルの下に混ざっているため、PO には「宿題 23 件」に見え、エージェントには「PO 待ちなので触れない」に見える
2. **工場が製品を食っている** — open Issue 91 件のうち `stream:factory` 43 + `stream:infra` 23 = 73%、`stream:product` は 18 件 (20%)。main コミットが `apps/` を触った割合は 68% → 33% → 19% (08-11 → 08-13)。ADR は 2 日で 13 本増え、`CLAUDE.md` は 2 日で +27%
3. **「できない」が測定されずに不変記録へ固定される** — `create_session` 不可を前提に体制を組んだ [ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) は 2 日後に前提が崩れていた。`management.azure.com` は元から到達可能なのに 4 個の回避策を作った。Codex の能力はドキュメント 1 ページから誤って一般化した。**現時点で `CLAUDE.md` と ADR に生きている能力の断定に、再測定日が併記されているものは 1 つも無い**

そして [#378](https://github.com/yomote/mind-inbox/issues/378) が決定的な制約を置いている: **窓口 PM の失敗 8 件のうち 5 件は「その日に自分が読み・参照し・他人に説明した規約」を破ったもの**。**9 個目の規約を足しても直らない。**

## Decision Drivers

- **規約を足さない** — 対策は既定値 / CI の門 / 機械が毎日書き換えるデータ のいずれかに落ちること ([#351](https://github.com/yomote/mind-inbox/issues/351) の判定基準「機械が探せる形の 1 文にできるか」を運用側にも適用)
- **在庫を純増させない** — 1 つ足すなら、何を消すかを同時に決める ([#378](https://github.com/yomote/mind-inbox/issues/378) の未解決欄「減らす提案を誰も出していない」)
- **PO の一手を「返信」だけに戻す** (ADR 0043 の driver を継承)
- **一級指標は「dev で触れるようになったもの」** (ADR 0043 D1) — これを人ではなく機械が書けること
- 追加課金ゼロ・長期クレデンシャル増加ゼロ (ADR 0008 / 0031 / 0037 の driver を継承)

## Considered Options

- **Option A: 現状維持** — ADR 0040 / 0043 の実装を完遂させるだけ
- **Option B: 体制を作り直す** — [#356](https://github.com/yomote/mind-inbox/issues/356) の A〜D を全部裁定し、PM 体制の新しい枠組みを作る
- **Option C: 5 つの最小手当て (採用)** — 症状ごとに 1 個ずつ、既存の機構に条件を足すか、既存の Issue の優先度を変えるだけで塞ぐ

## Decision Outcome

Chosen option: **"Option C"**。**新設は 1 つだけ (能力台帳)、残り 4 つは既存の機構への条件追加と優先度変更**。Option B を採らないのは、体制の作り直しがまさに実測で見えた自己増殖ループ (故障 → 仕組みを足す → 故障) の続きになるため。

### D1 — 能力の断定は「測定日つきの台帳」にしか書けない

- **`docs/ops/capabilities.md` を新設**する。1 行 = 1 能力で、列は **能力 / 測定した実行環境 (対話 / 子 / Routine) / 最終測定日 / 結果 / 再現手順 (実際に叩いたツール呼び出し 1 行)**
- **`CLAUDE.md` と ADR 本文に「使えない / 叩けない / 届かない / 不可」と書くときは、台帳の行へのリンクを併記する**。リンクの無い断定を CI が落とす (`npm run test:scripts` に検査を 1 本足す)
- **既存の断定を移送する** — `SendMessage` / `ListAgents` / `fire_trigger` の配送 / `update_trigger` の creator 制限 / `create_trigger` の `source_url` 欠如 / Azure 管理系 API の到達性 など、現在生きているものを初回測定として台帳に落とす
- **当番 PM tick が毎回、最終測定日が 7 日以上前の行を 1 つ以上叩き直す** (ADR 0040 D2 の職務に 1 項目追加。台帳が 7 行程度なら週 1 周する)
- **「測れなかった」は結果として書く** — 台帳の結果欄に `未測定` / `測定不能 (理由)` を許し、空欄を禁止する (CLAUDE.md「取れなかったものを異常なしと書かない」の自己申告版)

> **なぜ ADR ではなく台帳か**: 能力の可否は (実行環境 × 時刻) の関数であり、**有効期限のある観測**である。ADR は不変記録なので、観測を置くと期限が消える。ADR 0033 が 2 日で腐ったのはこの取り違えが原因。

### D2 — `needs-human` は「PO にしか押せないもの」だけに戻す

- **`needs-human` の定義を狭める**: ADR の Accept / 予算・課金 / 外部サービスの設定 / 不可逆な判断 / PO の好みの裁定 — **人間の権限が要るもの**に限る
- **機構が壊れて人に落ちてきたものは `needs-human` を付けない**。既存の `ci-failure` / `P1` として**工場の障害キュー**に置き、当番 tick が「PO の宿題」より先に潰す対象として扱う
- **ダイジェストは 2 区分で出す** — 冒頭の「🙋 あなたの番」は `needs-human` のみ (ADR 0044 D3 のとおり最大 3 件)。その下に **「工場の障害 n 件 (PO の操作は不要)」** を件数だけ
- ラベルの新設はしない。**既存ラベルの意味を絞るだけ**

> これは ADR 0020 の needs-human キューを**縮小**する改訂である。キューが「エージェントが進めない全て」の置き場になった結果、PO 専権の 5〜7 件が 23 件の中に埋もれた。

### D3 — WIP 2 枠のうち 1 枠を `stream:product` 専用にする

- ADR 0043 D3 の WIP 上限 2 は維持。そのうえで **1 枠を `stream:product` 専用**とし、**工場・インフラは同時に 1 本まで**とする
- 例外は 1 つだけ: **プロダクトが dev に届かない原因になっている工場の故障**は product 枠を使ってよい (ADR 0043 D2「メタ作業はプロダクトを止めている時だけ着工可」の機構化)
- 当番 tick と窓口 PM は着工前に in-flight を数え、**product 枠が空のまま factory を 2 本目として着工しない**。ダイジェストに 1 行出す: `product 枠: 空 / #nnn`

### D4 — 一級指標を機械が書けるようにする (`deploy` を割る)

- [#305](https://github.com/yomote/mind-inbox/issues/305) (deploy を provision + 配信の 2 job に割る) を **P2 → P1** に上げ、あわせて **「配信できたか」と「UI E2E が通るか」を別の check にする**
- 現状の `deploy` は 1 つの conclusion に両方を潰しており、直近 30 run が success 0 でありながら**配信自体は成功している** (最新 run では `Provision + deploy (up)` / smoke / golden path はすべて success、赤は `Golden path scenario（UI 込み E2E）` のみ)。**この粗さのために、ADR 0043 D1 の一級指標を人が読み解かないと書けない**
- 完了後、**ダイジェストの 1 行目を配信 job の結論から自動生成する**。ゼロの日はゼロと理由を書く (ADR 0043 D1 の要求そのまま)

### D5 — 在庫を純増させない (撤去を当番の職務に入れる)

- **本 ADR の実装で、機構を最低 1 つ撤去する** — 撤去候補は debrief で PO が選ぶ。純増ゼロを着地条件にする
- **当番 tick のレポートに「撤去候補」を 1 節足す** (新しい仕組みは作らない) — `watchers.json` の監視対象のうち、**直近 30 日に一度も発火痕跡が無いもの / 一度も落ちたことがないもの**を列挙する。「静かに死んでいる自動化」と「そもそも要らなかった自動化」は、どちらも痕跡の欠如として同じ形で出る
- **`CLAUDE.md` の棚卸しを debrief の定例項目にする** — 語数を毎回記録し、増えた分について「機構に落ちたので消せる行はどれか」を 1 回問う

### D6 — 分配の既定を「投げる」に反転する【design-gate 裁定事項】

実測では**道具は動いている**。08-12 の dependabot 7 本は、分配された使い捨てセッションが全件の diff を読み、6 本を実際にビルド・起動して検証し、分配時の見立ての誤り 3 点まで報告して返した。にもかかわらず窓口 PM が起こした子セッションは PO が音を上げるまで 0 本だった ([#378](https://github.com/yomote/mind-inbox/issues/378) 失敗 1)。**足りないのは能力ではなく既定値。**

推奨: **窓口 PM が自分で書いてよいのは「レビュー指摘への対応」と「1〜2 ファイルの修正」だけ**とし ([ADR 0048](0048-child-sessions-are-usable-again-with-a-one-way-poke-channel.md) D1 の再確認)、**それ以外は着工の第一手として「子か subagent か」を必ず一度声に出す**。声に出す場所を規約ではなく **SessionStart フックの出力に 1 行**として持たせる (既存フックへの追記 = 新設ゼロ)。

**この項は [#356](https://github.com/yomote/mind-inbox/issues/356) と同じ design-gate 対象であり、PO の裁定を待つ。** 裁定前に実装しない。

### Positive Consequences

- **「できない」の誤りが最長 7 日で自動的に訂正される**。ADR 0033 型 (誤った不可能性の上に体制を組む) が構造的に起きなくなる
- PO が見る「あなたの番」が 23 件から 5〜7 件になる。**残りは PO の宿題ではなく工場の障害**として、当番が先に潰す対象に変わる
- product 枠があるので、工場の故障が何本重なってもプロダクトの前進が 0 にならない
- ダイジェスト 1 行目が機械で埋まるので、「進んでるようで物ができてない」が人の読解に依存しなくなる
- 新設は台帳 1 ファイル + CI 検査 1 本のみ。D5 で最低 1 つ撤去するので**在庫は純増しない**

### Negative Consequences

- **台帳自体が腐りうる** — 当番 tick が落ちれば再測定も止まる。緩和: 台帳の「最終測定日が 7 日以上前」の行数をダイジェストに出す (欠落が見える形にする)。ただし**これは自己申告であり、機構ではない**
- **CI 検査は語のパターンマッチなので抜けられる** — 「使えない」と書かずに同じ意味を書けば通る。上限は「うっかり」を止めるところまで
- `needs-human` を狭めると、**工場の障害キューに積まれたまま誰も潰さないものが出る**恐れがある。緩和: 48 時間停滞の検出は既存 tick が持っている
- product 枠は**プロダクト側に着工可能な仕事が無い日**に空回りする (現状 `stream:product` 18 件あるので当面は起きない)
- D4 は `deploy` の workflow 改修を伴い、[#262](https://github.com/yomote/mind-inbox/issues/262) の修理と同じファイルを触る。**着工順の調整が要る**

## Pros and Cons of the Options

### Option A: 現状維持 (ADR 0040 / 0043 の完遂だけ)

- Good, because 追加の判断ゼロ。0043 の未実装項目 (ダイジェスト / milestone / claim ref) を消化すれば PM の自走は前進する
- Bad, because 3 症状のどれにも当たらない。0043 は「進む方向」の設計であって、「誤った不可能性」「人待ちの混線」「工場の自己増殖」はいずれも対象外
- Bad, because 0043 D1 の一級指標は**そもそも機械から読めない** (F4) ので、完遂しても指標が人の読解に依存し続ける

### Option B: 体制を作り直す (#356 の A〜D を全部裁定)

- Good, because 役割・分配・通信・並行度が一度に揃い、hub-and-spoke の反転 ([#348](https://github.com/yomote/mind-inbox/issues/348)) が解消する
- Bad, because **これ自体が症状**。08-11〜08-12 の 2 日で ADR が 13 本増えた輪の続きになる。#378 の診断「仕組みを足す反射」に正面から反する
- Bad, because 体制を変えても、能力の断定が腐る問題 (F1) と赤の粗さ (F4) は残る。**体制の下にある計測が壊れているのに、体制だけ差し替えることになる**

### Option C: 5 つの最小手当て (採用)

- Good, because 新設が 1 つで、残りは既存の条件追加・優先度変更。**在庫が純増しない**
- Good, because 5 つとも「振る舞いで検証できる」形に書ける (下記 検証条件)
- Bad, because 体制の論点 ([#356](https://github.com/yomote/mind-inbox/issues/356) / D6) は未決のまま残る。**分配の既定は別途 design-gate で裁定が要る**
- Bad, because D1 の CI 検査と D5 の撤去候補は自己申告に近く、機構としての強度は review-gate より弱い

## 関係 ADR への影響

- **[ADR 0043](0043-pm-self-driving-mode.md)**: supersede しない。D1 (一級指標) を機械可読にし、D3 (WIP 上限 2) に product 枠の条件を足す**実効化**
- **[ADR 0020](0020-hitl-choice-format-and-needs-human-queue.md)**: `needs-human` の定義を**狭める改訂**。選択肢形式・キューという核は不変
- **[ADR 0040](0040-project-continuity-three-layers.md)**: 当番 tick の職務に 2 項目 (能力の再測定 / 撤去候補) を追加。3 層構造は不変
- **[ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md)**: 本 ADR は「0033 が腐った理由」を一般化したものであり、0033 自体は既に [ADR 0048](0048-child-sessions-are-usable-again-with-a-one-way-poke-channel.md) が一部 supersede 済み
- **[ADR 0018](0018-runtime-verification-in-the-loop.md)**: 不変。D1 は「振る舞いで検証する」を**エージェント自身の能力の主張**に適用したもの

## 動作検証 (この ADR が実装されたと言える条件)

1. `CLAUDE.md` に「〜は使えない」を台帳リンク無しで 1 行足すと、`npm run test:scripts` が**落ちる**
2. `docs/ops/capabilities.md` の全行に測定日が入っており、当番 tick のレポートに「今回叩き直した行」が 1 つ以上載る
3. ダイジェストの「🙋 あなたの番」が `needs-human` のみになり、工場の障害が**別の行に件数で**出る。移行時点で `needs-human` は 18 件 → 5〜7 件になる
4. in-flight が factory 2 本の状態で新しい factory を着工しようとすると、当番が着工せず**その理由をダイジェストに書く**
5. UI E2E だけが落ちた日に、ダイジェストの 1 行目が「dev に届いた: あり (`sha`)」になる (今日の実データで言えば `3c2236d`)
6. 本 ADR の実装後、機構の在庫 (workflow / skill / Runbook / subagent の合計) が実装前**以下**である

## Links

- 実測: [`docs/reviews/agent-ops-analysis-2026-08-13.md`](../reviews/agent-ops-analysis-2026-08-13.md)
- 先行分析: [#378](https://github.com/yomote/mind-inbox/issues/378) (窓口 PM 自己点検 8 件) / [#351](https://github.com/yomote/mind-inbox/issues/351) (レビュー指摘 62 件の T1-T4 分類) / [#348](https://github.com/yomote/mind-inbox/issues/348) (役割分担) / [#356](https://github.com/yomote/mind-inbox/issues/356) (体制の再設計)
- 人待ちの実例: [#327](https://github.com/yomote/mind-inbox/issues/327) / [#253](https://github.com/yomote/mind-inbox/issues/253) / [#250](https://github.com/yomote/mind-inbox/issues/250) / [#345](https://github.com/yomote/mind-inbox/issues/345) / [#352](https://github.com/yomote/mind-inbox/issues/352)
- 赤の粗さ: [#305](https://github.com/yomote/mind-inbox/issues/305) (deploy の分割) / [#381](https://github.com/yomote/mind-inbox/issues/381) (guard の緑が腐る) / [#352](https://github.com/yomote/mind-inbox/issues/352) (沈黙と指摘なしが同じ)
