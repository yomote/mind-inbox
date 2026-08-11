# 0037. 定期評価を「機械計測 = Actions」と「LLM 採点 = PM tick」に分ける

- Status: Accepted (briefing #8 2026-08-11 で PO 承認)
- Date: 2026-08-10
- Deciders: yomote (PM 決定: 2026-08-10) / 実装セッション
- Related: [ADR 0035](0035-role-split-across-agents-and-actions.md) (D1: Routine をゼロにする) / [ADR 0031](0031-agent-reaches-outside-via-github-actions.md) (外の事実は Actions 経由) / [ADR 0022](0022-autonomous-ux-improvement-loop.md) (UX 自律改善ループ — 目的と 3 段構成は不変)

Technical Story: [#147](https://github.com/yomote/mind-inbox/issues/147) (週次健全性チェック) / [#127](https://github.com/yomote/mind-inbox/issues/127) (UX スコアボード) / [#162](https://github.com/yomote/mind-inbox/issues/162) (プローブ記録) / [#195](https://github.com/yomote/mind-inbox/issues/195) (maint-check の実出力例)

## Context and Problem Statement

[ADR 0035](0035-role-split-across-agents-and-actions.md) D1 は「claude.ai の Routine は生死が見えないので無人の見張りに使わない」と決めた。残っていたのが定期チェック 2 本 — `ux-judge` (毎朝のプローブ記録採点) と `maint-check` (週次の健全性チェック) — で、どちらも **LLM の判断を含む**。

ここに構造的な矛盾がある。生死を見えるようにするなら GitHub Actions に置きたいが、**LLM を Actions で走らせる経路は全部却下済み**:

- API キー (`ANTHROPIC_API_KEY`) は従量課金 — [ADR 0008](0008-pr-review-via-cloud-routine.md) が「追加課金の回避」を driver として却下した経路そのもの
- OAuth トークン / `auth.json` を CI に置くのは長期クレデンシャルの保存 — [ADR 0009](0009-on-demand-cd-via-github-actions-oidc.md) の「静的シークレットを保存しない」と [ADR 0035](0035-role-split-across-agents-and-actions.md) D5 (「CI に auth.json を置く」だけは却下) に反する

つまり「Routine のまま (生死が見えない)」と「Actions に LLM ごと移す (課金 or 秘密が増える)」のどちらも取れない。**仕事を分解して置き場所を変える**必要がある。

## Decision Drivers

- **止まったことに気づけること** — Routine 4 本全滅 (2026-08-10 実測) の再発防止。ADR 0035 の根本 driver
- **追加課金を増やさない** — ADR 0008 の driver を継承
- **長期クレデンシャルを増やさない** — ADR 0009 / 0035 D5 の driver を継承
- **痕跡が機械可読でリポジトリ (GitHub) に残ること** — watchers.json で監視できない自動化は作らない (CLAUDE.md)
- **静かに嘘をつかないこと** — 機械化でカバー範囲が狭まるなら、狭まった分を明示する (silent caps 禁止)

## Considered Options

- Option A: **Routine のまま維持する**
- Option B: **Actions + `ANTHROPIC_API_KEY` (claude-code-action 等) で LLM ごと移す**
- Option C: **Actions に OAuth トークン / `auth.json` を置いて LLM ごと移す**
- Option D: **機械で判定できる部分だけ Actions に移し、LLM 採点は PM セッションの日次 tick が subagent で実施する** (採用)

## Decision Outcome

Chosen option: **"Option D"**。LLM を無人経路に置く手段が全部却下されている以上、**判断の種類で置き場所を分ける** — 「機械で計算できること + 生死の可視化」は Actions (run 履歴が必ず残る)、「LLM の判断」は人が回している PM セッションの中 (痕跡は Issue コメントで残し、watchers.json の traces 欄で監視する)。

### 決定の内訳

- **D1 `ux-judge` Routine → `ux-eval.yml` (毎朝 08:20 JST 頃 + workflow_dispatch) + PM tick 採点に分割する。**
  - Actions 側 (`.github/workflows/ux-eval.yml`): 記録 Issue [#162](https://github.com/yomote/mind-inbox/issues/162) の最新プローブ記録コメントを読み、**鮮度を確かめ (26 時間以内に記録が無ければ run を赤にする)**、記録 JSON から機械計測 (区間レイテンシ統計 / 往復数 / 警告・エラー数) を抽出してスコアボード Issue [#127](https://github.com/yomote/mind-inbox/issues/127) に `kind: "ux-eval-mech"` のコメントとして積む。抽出は `cicd/scripts/ux-eval/ux_eval.py` (純粋関数 + L1 テスト)
  - LLM 側: rubric 採点 (`kind: "ux-judge-score"`) は **PM セッションの日次 tick が subagent `ux-reviewer` を新品コンテキストで起動して実施**し、従来どおり #127 にコメントで積む (投稿前の `validate-judge-score.py` 検証も従来どおり)。手順は [Runbook](../runbooks/ux-probe-judge.md)
- **D2 `maint-check` Routine → `debt-check.yml` (週次・月曜 06:00 JST 頃 + workflow_dispatch) に移す。**
  - 機械検出できる負債だけを検出する: docs 内の壊れた相対リンク / placeholder のままのテスト script (`cicd/scripts/debt-check/detect.py`)。検出があれば `[debt-check] YYYY-MM-DD: 検出 N 件` の Issue を起票 (既存 open の `[debt-check]` Issue があればコメント追記)。0 件なら起票せず run 緑のみ
  - **カバーできていない領域 (意味的な docs 陳腐化 / デッドコード / 依存の逆流 など旧 maint-check の LLM 部分) は、run のログと Issue 本文に毎回明示する**。「0 件 = 健全」と読ませない
- **D3 両 workflow に `.github/actions/report-failure` を配線する** (ADR 0035 D2 の自己通報パターン)。落ちたら自分で Issue を立て、緑に戻れば自分で閉じる
- **D4 watchers.json を移行後の姿にする。** routines 欄から `ux-judge` / `maint-check` / PR レビュー Routine の 3 エントリを削除し (**routines 0 本 = ADR 0035 動作検証 1 の達成**)、workflows 欄に `ux-eval.yml` / `debt-check.yml` を追加、traces 欄に「LLM 採点 (PM tick → #127 コメント, expect 50 時間 — 週末スキップ許容)」を追加する。PR レビューの痕跡は `review-gate.yml` (required check) が持つ
- **D5 claude.ai 側の Routine 実体の削除は needs-human に積む** (エージェントからは叩けない — [ADR 0031](0031-agent-reaches-outside-via-github-actions.md) 補足)。削除されるまでは二重管理だが、Routine が発火しても投稿は同じ Issue に積まれるだけで壊れない

### Positive Consequences

- 定期評価の生死が Actions の run 履歴と traces 監視で判定できるようになり、状況ページの routines 欄が空になる (ADR 0035 動作検証 1)
- 機械計測は LLM の気まぐれと無関係に毎朝積まれる — レイテンシのトレンドは採点が止まっても途切れない
- 追加課金ゼロ・長期クレデンシャル増加ゼロを維持
- 鮮度チェックにより「古い記録を今日の計測として積む」嘘が構造的に消える

### Negative Consequences

- **LLM 採点は PM セッションが回っている日しか積まれない** — expect 50 時間 (週末スキップ許容) を超えたら状況ページが赤で教える、という間接的な監視になる。Actions のような run 履歴は残らない
- **#127 の trace は kind を区別できない** — 状況ページの trace 判定 (`build.py`) はコメントの最終時刻しか見ないため、機械計測 (`ux-eval-mech`) が毎朝積まれると **LLM 採点が止まっていても trace が緑に見える**。既知の穴として watchers.json の note に明記した。塞ぐには build.py に kind フィルタを足す必要がある (別 PR)
- 機械検出できる負債の範囲は旧 maint-check の想定より狭い。狭い分は毎回明示するが、明示を読む人がいなければ意味的な陳腐化は積もる (debrief で拾う前提)

## Pros and Cons of the Options

### Option A: Routine のまま維持する

- Good, because 何も作らなくてよい
- Bad, because 生死が見えない。2026-08-10 の実測で `ux-judge` は**一度も投稿しないまま沈黙**、`maint-check` は初動作で検出 10 件を放置していた (ADR 0035)
- Bad, because ADR 0035 D1 (Routine ゼロ) と正面衝突する

### Option B: Actions + `ANTHROPIC_API_KEY`

- Good, because 実装が最も素直で、生死も run 履歴で見える
- Bad, because **メーター課金が発生する**。ADR 0008 が同じ理由で却下した経路 (driver「追加課金の回避」を折る)
- Bad, because サブスク枠と別の支払いが増え、コスト上限の管理対象が増える

### Option C: Actions + OAuth トークン / `auth.json`

- Good, because 追加課金はない
- Bad, because **長期クレデンシャルを CI に保存する**。ADR 0009 の「静的シークレットを保存しない」と ADR 0035 D5 (公開リポジトリで公式に非推奨) に反する
- Bad, because 漏洩時の被害がアカウント全体に及ぶ

### Option D: 機械計測 = Actions / LLM 採点 = PM tick (採用)

- Good, because driver を全部満たす (課金ゼロ / 秘密ゼロ / 生死可視 / 痕跡が機械可読)
- Good, because 機械計測と LLM 採点が別コメントになり、**どちらが止まっているかを切り分けられる**
- Bad, because LLM 採点の実施が PM セッションの運用 (人が毎日開くこと) に依存する
- Bad, because 評価が 2 か所 (workflow + PM tick) に分かれ、全体像の把握に ADR 1 枚 (これ) が要る

## 動作検証 (この ADR が実装されたと言える条件)

1. `ux-eval.yml` の run が毎朝残り、#127 に `ux-eval-mech` のコメントが 1 run = 1 件積まれる
2. 記録が 26 時間以上古いとき、`ux-eval.yml` の run が**赤くなる** (静かに古い記録を積まない)
3. `debt-check.yml` が週次で走り、検出 > 0 のときだけ `[debt-check]` Issue が立つ / 追記される。0 件の run のログにもカバー外領域が出ている
4. watchers.json の routines 欄が空で、状況ページの判定が 🟢/🔴/🟡 だけで構成される (ADR 0035 動作検証 1)

## Links

- Issue: [#147](https://github.com/yomote/mind-inbox/issues/147) / [#127](https://github.com/yomote/mind-inbox/issues/127) / [#162](https://github.com/yomote/mind-inbox/issues/162) / [#195](https://github.com/yomote/mind-inbox/issues/195)
- 関連 ADR: [0008](0008-pr-review-via-cloud-routine.md) (課金却下の原点) / [0009](0009-on-demand-cd-via-github-actions-oidc.md) (no stored secret) / [0022](0022-autonomous-ux-improvement-loop.md) (ループの目的は不変) / [0029](0029-probe-record-transport-via-issue-comment.md) (記録の運搬) / [0031](0031-agent-reaches-outside-via-github-actions.md) / [0035](0035-role-split-across-agents-and-actions.md)
- Runbook: [ux-probe-judge](../runbooks/ux-probe-judge.md)
