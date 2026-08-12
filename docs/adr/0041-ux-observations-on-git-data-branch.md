# 0041. UX 観測データの蓄積先を Issue コメントから git データブランチへ移す

- Status: Accepted (2026-08-12, debrief にて PO 裁定。選択自体は 2026-08-11 に #197 のコメントで実施済み)
- Date: 2026-08-11
- Deciders: omoteforlab (蓄積先の方式は 2026-08-11 の対話セッションで選択肢形式により裁定 — [#197 コメント](https://github.com/yomote/mind-inbox/issues/197)) / 実装セッション
- Consulted: —
- Informed: —

Technical Story: [#197](https://github.com/yomote/mind-inbox/issues/197) (蓄積先の検討 Issue)。発端は報告会 #6 での PO の違和感「Issue にどんどん溜まっていくのは、運用としてこれでいいのかよく分からない」。

## Context and Problem Statement

UX 観測のデータは現在 3 種類とも Issue コメントに蓄積している ([ADR 0029](0029-probe-record-transport-via-issue-comment.md) / [ADR 0037](0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md)):

- プローブ記録 `ux-probe-record` → [#162](https://github.com/yomote/mind-inbox/issues/162) (golden-path-monitor が毎朝投稿)
- 機械計測 `ux-eval-mech` → [#127](https://github.com/yomote/mind-inbox/issues/127) (ux-eval.yml が毎朝投稿)
- LLM 採点 `ux-judge-score` → [#127](https://github.com/yomote/mind-inbox/issues/127) (PM tick が投稿)

この方式は無人ループを最初に成立させた点で正しかったが、構造的な匂いが #197 に整理されている:

- **正しさが人間の行儀に依存する** — 「このスレッドに返信しないでください」はお願いであって制約ではない。誰かが一言返信すれば「最新コメント = 最新記録」の前提が崩れる
- **時系列データなのに改ざんを検知できない** — コメントは後から編集・削除できる
- **読み出しが脆い** — コメント本文から正規表現で JSON を切り出し、ページングも自前。M2 の閾値割れ・前回比劣化の機械判定が始まると、この脆さは書き込みより読み出しで効いてくる
- **「閉じない Issue」をデータストアにするのは Issue の姿として不自然** (2026-08-11 PO 指摘)

一方、ADR 0029 が「リポジトリへの commit 蓄積」を却下した理由は実測で古くなった: **git の push は agent セッションから普通に動く** (2026-08-09 実測 — `gh` を殺すゲートウェイの 403 も artifact を殺した egress 制約も git には効かない)。

## Decision Drivers

- **正しさを規約ではなく構造で保証する** — 雑談が混ざらない・改ざんが履歴に残る・スキーマを普通のファイルとして読める
- **agent だけでループが閉じ続ける** (ADR 0029 の driver を継承) — 読み書きとも agent セッション / Actions から到達できること
- **「動いたら痕跡がリポジトリに残る」原則** (CLAUDE.md) — 蓄積の生死を watchers.json で監視できること
- **main の履歴を汚さない** — bot コミットが PR の追いつき競争を悪化させない
- **追加課金・長期クレデンシャルを増やさない** (ADR 0037 の driver を継承)
- **悪化が一目で分かる** — 蓄積したデータからトレンドを人間が見られること (M2 の前段)

## Considered Options

- Option A: 現状維持 (Issue コメント蓄積 / ADR 0029 のまま)
- Option B: main ブランチへ直接 commit で蓄積する
- Option C: **専用データブランチ (orphan) に JSONL で蓄積し、ステータスページにトレンドを描く** (採用)
- Option D: 外部ストレージ (Azure Blob / Cosmos 等) に蓄積する

## Decision Outcome

Chosen option: **"Option C"**。2026-08-11 に PO が選択肢形式で裁定 ([#197 コメント](https://github.com/yomote/mind-inbox/issues/197))。

### 決定の内訳

- **D1 蓄積先はデータブランチ `data/ux-observations`** — orphan ブランチ (main と履歴を共有しない)。無ければ書き込み側の共通ヘルパーが orphan で作る
- **D2 ファイルは月別 JSONL** — `probes/YYYY-MM.jsonl` (kind: `ux-probe-record`) と `evals/YYYY-MM.jsonl` (kind: `ux-eval-mech` / `ux-judge-score`)。1 行 = 1 観測。全行が共通で `kind` と `recordedAt` (ISO8601 UTC / 鮮度判定と月振り分けの基準) を持つ
- **D3 追記は共通ヘルパー 1 本に集約** — `cicd/scripts/ux-data/append.py` (振り分け + 検証 + 同一観測の重複スキップ) と `cicd/scripts/ux-data/append-observation.sh` (fetch → 追記 → commit → push、ブランチが無ければ orphan 作成、push 競合はリトライ)。golden-path-monitor / ux-eval.yml / PM tick の 3 経路とも同じヘルパーを通る
- **D4 LLM 採点も同じデータブランチへ** — PM tick は `post-judge-score.sh` (検証 → 追記に改修) を使う。git push は agent セッションから動くため、gh が使えない環境でも人間と同じスクリプトで投稿できる (従来の「agent は MCP で代替」の分岐が消える)
- **D5 読み出し (ux_eval.py の鮮度・評価済み判定) はデータブランチの JSONL から行う** — コメント本文の正規表現切り出しを廃止する
- **D6 ステータスページに UX トレンド節を足す** — データブランチを取得して send→表示 avg/max の時系列 (直近 2 週間)・警告数・LLM 採点を描く。あわせて watchers.json の trace を kind 別のデータブランチ参照に変え、**「機械計測だけ動いて LLM 採点が止まった」を検出できない穴 (ADR 0037 Negative Consequences) を塞ぐ**
- **D7 過去データは one-shot の移行スクリプトで #162 / #127 のコメントから JSONL に取り込む** (`workflow_dispatch` で実行)。移行完了後、#162 / #127 は蓄積先としての役目を終えてクローズする (手順は [Runbook](../runbooks/ux-probe-judge.md))
- **D8 monitor / ux-eval に `contents: write` を付与する** — ADR 0029 時点の却下理由のうち権限面は「`issues: write` を渡したのと重さとして大差ない」(#197) と再評価した

### Positive Consequences

- 蓄積の正しさが人間の行儀に依存しない (ブランチに書けるのは書き込み経路だけ、改ざんは git 履歴に残る)
- 読み出しが「ファイルを読む」になり、正規表現・ページング・コメント順序への依存が消える
- 蓄積が普通の差分で追え、`git log` がそのまま監査ログになる
- LLM 採点の投稿経路が human / agent で 1 本化される (post-judge-score.sh)
- kind 別の生死監視が可能になり、ADR 0037 の既知の穴 (LLM 採点だけ止まっても緑) が塞がる
- トレンドがステータスページで常時見える (悪化に気づく場所が Issue のコメント列から 1 枚のグラフになる)

### Negative Consequences

- workflow に `contents: write` が増える (対象はデータブランチだけだが、権限自体はリポジトリ単位)
- データブランチへの毎朝の bot コミットが増える (main とは隔離されるので PR 追いつき競争には影響しない)
- 書き込みの同時実行は push リトライで解決する設計 — リトライ上限を超えるとその 1 件は落ちる (workflow の warning / report-failure で気づく)
- 会話全文がリポジトリのブランチに永続する。プローブは合成シナリオなので機密性の問題は無いが、実ユーザーの発話を扱うようになったら再判断が要る (ADR 0029 と同じ留保)

## Pros and Cons of the Options

### Option A: 現状維持 (Issue コメント)

- Good, because 実装済みで動いている
- Bad, because 正しさが「人間が返信しない」というお願いに依存する
- Bad, because 時系列データの改ざん・削除を検知できない
- Bad, because M2 (機械判定) が近づくほど正規表現読み出しの脆さが効く
- Bad, because PO が「Issue の姿として不自然」と裁定した

### Option B: main へ直接 commit

- Good, because ブランチ管理が要らず、checkout 済みの main からそのまま読める
- Bad, because **bot コミットが PR の追いつき競争を悪化させる実害が既にある** (2026-08-11、PR #243 が bot コミットとの競争で 3 周した)。毎朝 2〜3 コミット増はこれを常態化させる
- Bad, because main の履歴が観測データで埋まり、人間の変更履歴が読みにくくなる

### Option C: 専用データブランチ + ステータスページ描画 (採用)

- Good, because driver を全部満たす (構造で保証 / agent 到達可 / 痕跡が残る / main を汚さない / 課金・秘密ゼロ)
- Good, because orphan ブランチなので main の履歴・PR 運用と完全に隔離される
- Bad, because ブランチの取得 (fetch) が読み出し側に 1 手増える
- Bad, because 同時書き込みの競合処理 (リトライ) を自前で持つ必要がある

### Option D: 外部ストレージ (Azure Blob / Cosmos 等)

- Good, because データストアとしての性質 (クエリ・保持) は最も素直
- Bad, because agent セッションの egress 制約に正面衝突する (artifact を殺したのと同じ壁 / #160)
- Bad, because 待機課金・認証経路・IaC が増える。1 日数 KB のデータに対して過剰

## 動作検証 (この ADR が実装されたと言える条件)

1. golden-path-monitor の run 後、`data/ux-observations` の `probes/YYYY-MM.jsonl` に 1 行増えている (Issue #162 にはもう投稿されない)
2. ux-eval.yml の run 後、`evals/YYYY-MM.jsonl` に `ux-eval-mech` の行が増えている。鮮度切れ・評価済みの朝は run が赤くなる (従来の性質を維持)
3. PM tick の採点が `evals/YYYY-MM.jsonl` に `ux-judge-score` の行として積まれる
4. ステータスページに UX トレンド節が出て、データブランチが取得できない時は「未検証」と明示される (緑に見せない)
5. 移行スクリプトで #162 / #127 の既存コメントが JSONL に取り込まれ、再実行しても重複しない

## Links

- Issue: [#197](https://github.com/yomote/mind-inbox/issues/197) (裁定) / [#162](https://github.com/yomote/mind-inbox/issues/162) / [#127](https://github.com/yomote/mind-inbox/issues/127) / [#123](https://github.com/yomote/mind-inbox/issues/123) (epic)
- 関連 ADR: [0029](0029-probe-record-transport-via-issue-comment.md) (現行の運搬・蓄積 — 本 ADR の Accept 時に蓄積先の部分を Supersede する) / [0037](0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md) (機械計測と LLM 採点の分担 — 分担自体は不変、置き場だけ変わる) / [0031](0031-agent-reaches-outside-via-github-actions.md) (外界到達は Actions 経由) / [0035](0035-role-split-across-agents-and-actions.md) (生死が見える場所に置く)
- Runbook: [ux-probe-judge](../runbooks/ux-probe-judge.md) / [status-page](../runbooks/status-page.md)
