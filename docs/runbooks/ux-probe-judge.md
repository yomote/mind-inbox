# UX プローブと UX 評価の運用 (#123 M0/M1)

## 分担 (ADR 0037 — 旧 ux-judge Routine の後継)

評価は 2 系統に分かれる。**Routine は使わない** (生死が見えない — ADR 0035 D1):

| 系統                                            | 担い手                                                                          | いつ                                              | 痕跡 (データブランチの行) |
| ----------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------- | ------------------------- |
| **機械計測** (レイテンシ統計 / 往復数 / 警告数) | [`.github/workflows/ux-eval.yml`](../../.github/workflows/ux-eval.yml)          | 毎朝 08:20 JST 頃 (自動)                          | `kind: "ux-eval-mech"`    |
| **LLM 採点** (rubric 評価)                      | **PM セッションの日次 tick** が subagent `ux-reviewer` を新品コンテキストで起動 | PM が回っている日 (expect 50h — 週末スキップ許容) | `kind: "ux-judge-score"`  |

ux-eval.yml は記録の鮮度も見る — **26 時間以内にプローブ記録が無ければ run が赤くなる**
(古い記録を今日の計測として積まない)。抽出ロジックは `cicd/scripts/ux-eval/ux_eval.py`。

## 蓄積の形 — データブランチ `data/ux-observations` (ADR 0041)

観測は Issue コメントではなく **git のデータブランチに JSONL で蓄積する**
(2026-08-11 PO 裁定 / [#197](https://github.com/yomote/mind-inbox/issues/197)。
旧方式は [ADR 0029](../adr/0029-probe-record-transport-via-issue-comment.md))。

- ブランチ: `data/ux-observations` (orphan — main と履歴を共有しない。無ければ書き込みが作る)
- ファイル: `probes/YYYY-MM.jsonl` (プローブ記録) / `evals/YYYY-MM.jsonl` (機械計測 + LLM 採点)
- 1 行 = 1 観測。全行が `kind` と `recordedAt` (ISO8601 UTC — 鮮度判定・月振り分けの基準) を持つ
- **手で編集しない**。書き込みは必ず `cicd/scripts/ux-data/append-observation.sh` 経由
  (ファイル振り分け・重複スキップは `append.py`、git の orphan 作成・push リトライは sh 側)
- 誤った観測は行を消さず**訂正の観測を追記する** (時系列の改ざんを避ける — git 履歴が監査ログ)

## Trigger

- 毎朝の golden-path-monitor 実行後、UX プローブ記録 (相談 4 往復の会話 + レイテンシ) を採点したいとき (PM tick の LLM 採点)
- プローブを手動で回したいとき (プロンプト変更後の確認等)
- スコアの時系列トレンドを見たいとき

## Prerequisites

- git の fetch/push ができること — **人間の手元・Actions runner・agent セッションのいずれも可**
  (git は agent セッションからも通る。`gh` は agent では使えないが、この運用ではもう不要)
- 採点は Claude セッションから subagent `ux-reviewer` を起動できること
- 背景理解: [ADR 0022](../adr/0022-autonomous-ux-improvement-loop.md) / [ADR 0037](../adr/0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md) / [ADR 0041](../adr/0041-ux-observations-on-git-data-branch.md) / rubric は `.github/claude/ux-rubric.md` (**真実**)

## データの流れ (どこに何が残るか)

| データ                                        | 置き場                                                                                         | 保持       |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------- | ---------- |
| 会話全文 + 区間レイテンシ (`ux-probe-record`) | **データブランチの `probes/YYYY-MM.jsonl`** (1 run = 1 行) — **採点が読む先**                  | 永続       |
| 同上 (障害調査用の一次情報)                   | golden-path-monitor の artifact `ux-probe-<run_id>` — 人間が `gh` で取る                       | 90 日      |
| レイテンシ閾値超過 (#120)                     | 同 run の warning annotation + step summary                                                    | run と同じ |
| 機械計測 (`ux-eval-mech`)                     | **データブランチの `evals/YYYY-MM.jsonl`** (1 run = 1 行。ux-eval.yml が毎朝追記)              | 永続       |
| judge 採点 (`ux-judge-score` + レポート全文)  | **データブランチの `evals/YYYY-MM.jsonl`** (1 採点 = 1 行。`report` フィールドに全文入り)      | 永続       |
| トレンドの可視化                              | [ステータスページ](https://yomote.github.io/mind-inbox/status/) の「UX トレンド」節 (毎回生成) | 生成物     |

artifact を併存させる理由は変わらず — 人間が障害調査で生の記録を掘る経路
([ADR 0029](../adr/0029-probe-record-transport-via-issue-comment.md) の経緯)。採点が読む「正」はデータブランチ側。

## Steps

**機械計測は無人** (ux-eval.yml が毎朝自動)。**LLM 採点は PM セッションの日次 tick** が下の 2〜4 を回す ([ADR 0037](../adr/0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md))。以下は **PM tick が実行する手順であり、同時に手動フォールバックの手順**でもある。**human / agent で経路の違いは無い** (git だけで完結する — ADR 0041 D4)。

1. プローブを回す (毎朝 07:00 JST は自動。手動なら):

   ```bash
   gh workflow run golden-path-monitor.yml
   gh run watch "$(gh run list -w golden-path-monitor.yml -L 1 --json databaseId -q '.[0].databaseId')"
   ```

   (agent セッションでは `gh` の代わりに GitHub MCP `actions_run_trigger`)

2. 記録 JSON を取得する — データブランチから最新のプローブ記録を読む:

   ```bash
   git fetch origin data/ux-observations
   mkdir -p /tmp/ux-probe
   git show "$(git rev-parse FETCH_HEAD):probes/$(date -u +%Y-%m).jsonl" \
     | tail -1 | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["record"], ensure_ascii=False))' \
     > /tmp/ux-probe/probe.json
   PROBE_JSON="$(cicd/scripts/ux-probe/inspect-probe-artifact.py /tmp/ux-probe)"
   ```

   月初 (当月ファイルがまだ無い朝) は前月の `probes/YYYY-MM.jsonl` を読む。
   `inspect-probe-artifact.py` の終了コード: `3` = 記録が無い / `4` = turns 0 件 /
   `1` = 記録が壊れている。`3` と `4` は「採点する材料がない」ので、その朝は採点を
   スキップして終わる (Common Issues 参照)。

3. UX judge で採点する — **新品コンテキストの subagent** として起動する
   (実装セッション内で直接採点しない — 前提の混入を防ぐのが独立 judge の価値, ADR 0019/0022):

   > Task(ux-reviewer): `$PROBE_JSON` を採点して

   レポートをファイルに保存する (例: `/tmp/ux-judge-report.md`)。

4. 採点を検証してデータブランチへ追記する:

   ```bash
   cicd/scripts/ux-probe/post-judge-score.sh /tmp/ux-judge-report.md
   ```

   **検証に落ちたら追記しない** — 蓄積は時系列データで、壊れた 1 件が混ざるとトレンド判断が
   そのぶん狂う。`validate-judge-score.py` が見るのは形式だけでなく、**total / max が scores と
   整合しているか**、**verdict が rubric の閾値と一致するか**、**UNKNOWN に理由が付いているか**まで。
   検証に落ちたときの終了コード: `2` = 採点ブロックが無い (judge が rubric の出力ルール 3 に
   従っていない) / `3` = 内容が不整合。いずれもレポートは残るので、原因を見て judge を再実行する。
   検証を通ると採点 JSON + レポート全文 (`report` フィールド) が `evals/YYYY-MM.jsonl` に
   1 行で追記され、push まで自動で行われる。

5. トレンドを見る — まず [ステータスページ](https://yomote.github.io/mind-inbox/status/) の
   「UX トレンド」節 (send→表示 avg/max の折れ線 + 警告 + LLM 採点)。生データを触るなら:

   ```bash
   git fetch origin data/ux-observations
   git show "$(git rev-parse FETCH_HEAD):evals/$(date -u +%Y-%m).jsonl" \
     | jq -r 'select(.kind == "ux-judge-score")
         | "\(.scoredAt)\t\(.scenarioId)\t\(.total)/\(.max)\t\(.verdict)\tUNKNOWN:\(.unknowns | length)"'
   ```

### 旧 Routine (廃止 — ADR 0037)

- `ux-judge — 毎朝の UX 採点 (ADR 0027 D1)` / ID `trig_01B2tk2Z8kRrsnHAwSgRJQcX` (2026-08-09 登録, [#156](https://github.com/yomote/mind-inbox/issues/156)) は **ADR 0035 D1 / 0037 で廃止**。claude.ai 側の Routine 実体の削除は needs-human (エージェントからは叩けない)
- 削除されるまで発火しても、旧経路 (#127 コメント) に書けるだけでデータブランチの蓄積は壊れない — ただし移行 (ADR 0041) 後はその投稿は誰にも読まれない

## 過去データの移行と旧 Issue のクローズ (one-shot / ADR 0041 D7)

Issue コメント時代の蓄積 (#162 / #127) をデータブランチへ取り込む手順。**実装 PR の
マージ後に 1 回だけ**行う (実行は PM または人間):

1. `ux-data-migrate` workflow を回す — Actions UI から `Run workflow`、または
   `gh workflow run ux-data-migrate.yml` (agent は GitHub MCP `actions_run_trigger`)。
   #162 / #127 の全コメントを読み、`kind` 付き JSON ブロックを `recordedAt` = コメント
   created_at の観測として追記する。**再実行しても安全** (同一観測はスキップされる)
2. 確認: `data/ux-observations` ブランチができ、`probes/` `evals/` の行数が旧コメント数と
   合っていること。ステータスページの UX トレンドが描かれていること
3. 翌朝の golden-path-monitor / ux-eval が**データブランチに**追記していることを確認する
   (#162 / #127 にはもうコメントが増えない)
4. **#162 / #127 をクローズする** — 最後に「蓄積は `data/ux-observations` へ移行した
   (ADR 0041 / #197)。以後このスレッドは読まれない」とコメントし、`state_reason: completed`
   で閉じる
5. 後片付け (任意): `probe-record-comment.py` の `format` / `extract` サブコマンドは
   旧コメント形式のレガシー。移行完了後は削除してよい (別 PR)

## Verification

- [ ] golden-path-monitor の run に artifact `ux-probe-<run_id>` があり、JSON の `turns` が 4 件ある
- [ ] **人手を介さず** `data/ux-observations` の `probes/YYYY-MM.jsonl` に行が増えている — 次の monitor 実行 (07:00 JST) で確認。**未検証**
- [ ] レイテンシ閾値超過があれば run の Annotations に warning が出ている
- [ ] **人手を介さず** `evals/YYYY-MM.jsonl` に機械計測 (`ux-eval-mech`) の行が増えている — ux-eval.yml の run (08:20 JST) で確認。**未検証**
- [ ] 記録が 26 時間以上古い朝に ux-eval.yml の run が**赤くなる** (静かに古い記録を積まない)
- [ ] PM tick の日に `evals/YYYY-MM.jsonl` に採点 (`ux-judge-score`) の行が増えている。**未検証**
- [ ] ステータスページの「UX トレンド」節にグラフが出て、データブランチが取れないときは「未検証」表示になる。**未検証**

## Rollback

- 蓄積は JSONL の追記のみで破壊的操作なし。誤った観測は行を編集・削除せず、**訂正の観測を
  追記する** (時系列の改ざんを避ける。git 履歴が監査ログとして残る)
- シナリオを変えた場合 (`ux-probe.spec.ts` の `SCENARIO.id` 更新) はスコアの断絶点になる —
  `scenarioId` が行ごとに残るので集計時に分離できる (区切りの明示が要るなら ADR / journal に書く)
- データブランチが壊れた場合 (手編集など): 旧 #162 / #127 のコメントが凍結されたまま残って
  いれば `ux-data-migrate` の再実行で再構築できる (重複はスキップされる)

## Common Issues

### artifact `ux-probe-<run_id>` が無い

- 原因: プローブ手前 (curl 版 golden-path / 結線カナリア) で fail してプローブ未到達、
  または AZURE\_\* variables 未設定で全体スキップ
- 対処: run の step summary / NG 行でホップを特定。プローブ自体の問題ではない

### 記録はあるが turns が 4 件未満

- 原因: 途中の往復で対話が壊れた (応答空 / stub / タイムアウト)。記録は 1 往復ごとに
  書き出すので、壊れる直前までは残っている
- 対処: 残った turns と E2E ログで壊れたホップを切り分け。採点は完了分のみで可 (judge に明示)

### ux-eval.yml の run が赤い

- 原因: 26 時間以内のプローブ記録が `probes/` に無い (プローブ手前で fail / 追記失敗)、
  データブランチが無い (初期移行前)、または追記 push の失敗。
  **古い記録を今日の計測として積まないための意図した赤** (ADR 0037)
- 対処: golden-path-monitor の同朝 run とデータブランチの最新行 (`git log data/ux-observations`)
  を確認。記録が復旧すれば次の run で緑に戻り、report-failure が立てた Issue も自動で閉じる

### append-observation.sh が「リトライ上限」で落ちる

- 原因: データブランチへの同時 push が 3 回連続で競合 (通常は起きない — 書き込みは
  朝の 2 本 + PM tick で時間帯が分かれている)
- 対処: 再実行すれば合流する。頻発するなら書き込みタイミングの重なりを疑う

### judge のスコアが信用できない気がする

- 原因: 生成側と judge が同系モデル (ADR 0022 の既知リスク)
- 対処: PO の抜き打ち監査 — `probes/` の生の会話 (record) を直接読み、乖離があれば rubric を直す
  (rubric 改定は PO の専権)。M1.5 較正実験 (#123 コメント) で一致率を測る

## Related

- ADR: [0040 データブランチ蓄積](../adr/0041-ux-observations-on-git-data-branch.md) / [0022 UX 自律改善ループ](../adr/0022-autonomous-ux-improvement-loop.md) / [0037 定期評価の分担](../adr/0037-scheduled-evals-split-mechanical-actions-llm-pm-tick.md) / [0029 旧: Issue コメント運搬](../adr/0029-probe-record-transport-via-issue-comment.md) / [0019 独立 judge](../adr/0019-independent-judge-agents-security-qa-release.md) / [0018 動作検証](../adr/0018-runtime-verification-in-the-loop.md)
- rubric: `.github/claude/ux-rubric.md` / subagent: `.claude/agents/ux-reviewer.md`
- プローブ実装: `apps/frontend/e2e-live/ux-probe.spec.ts` / workflow: `.github/workflows/golden-path-monitor.yml` / `.github/workflows/ux-eval.yml` (機械計測: `cicd/scripts/ux-eval/ux_eval.py`) / `.github/workflows/ux-data-migrate.yml` (過去データ移行)
- 蓄積ヘルパー: `cicd/scripts/ux-data/` (append.py / append-observation.sh / migrate-issue-comments.py)
- epic: [#123](https://github.com/yomote/mind-inbox/issues/123) / 蓄積先の検討: [#197](https://github.com/yomote/mind-inbox/issues/197) / 旧蓄積: [#162](https://github.com/yomote/mind-inbox/issues/162) / [#127](https://github.com/yomote/mind-inbox/issues/127)
