# UX プローブと UX judge の運用 (#123 M0/M1)

## Trigger

- 毎朝の golden-path-monitor 実行後、UX プローブ記録 (相談 4 往復の会話 + レイテンシ) を採点したいとき
- プローブを手動で回したいとき (プロンプト変更後の確認等)
- スコアの時系列トレンドを見たいとき

## Prerequisites

- `gh` CLI (repo read + issues write) — **無い環境では GitHub MCP で代替する** (下記「gh が無い環境でのフォールバック」)
- 採点は Claude セッションから subagent `ux-reviewer` を起動できること
- 背景理解: [ADR 0022](../adr/0022-autonomous-ux-improvement-loop.md) / rubric は `.github/claude/ux-rubric.md` (**真実**)

## データの流れ (どこに何が残るか)

| データ                                                    | 置き場                                                                                                          | 保持       |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ---------- |
| 会話全文 + 区間レイテンシ (JSON, `ux-probe-conversation`) | golden-path-monitor の artifact `ux-probe-<run_id>`                                                             | 90 日      |
| レイテンシ閾値超過 (#120)                                 | 同 run の warning annotation + step summary                                                                     | run と同じ |
| judge 採点 (`ux-judge-score`)                             | **スコアボード Issue [#127](https://github.com/yomote/mind-inbox/issues/127) のコメント** (1 採点 = 1 コメント) | 永続       |

リポジトリへの commit 蓄積は不採用 (monitor に `contents: write` を渡す必要 + 毎朝のノイズ commit を避けた)。

## Steps

**既定は無人** (#154 / [ADR 0027](../adr/0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) D1) — 毎朝の Routine が下の 1〜4 を自動で回す。以下は **Routine が実行する手順であり、同時に手動フォールバックの手順**でもある (Routine 未登録時 / 個別に採点したい時は同じコマンドを人が打てばよい)。

1. プローブを回す (毎朝 07:00 JST は自動。手動なら):

   ```bash
   gh workflow run golden-path-monitor.yml
   gh run watch "$(gh run list -w golden-path-monitor.yml -L 1 --json databaseId -q '.[0].databaseId')"
   ```

2. 記録 JSON を取得する:

   ```bash
   PROBE_JSON="$(cicd/scripts/ux-probe/fetch-latest-probe.sh)"
   echo "$PROBE_JSON"
   ```

   標準出力は**パスだけ**。診断は stderr に出る (混ぜると呼び出し側が壊れる)。
   終了コード: `3` = artifact 無し (プローブ手前で fail / 全体スキップ) / `4` = turns 0 件。
   どちらも「採点する材料がない」ので、その朝は採点をスキップして終わる (Common Issues 参照)。

3. UX judge で採点する — **新品コンテキストの subagent** として起動する
   (実装セッション内で直接採点しない — 前提の混入を防ぐのが独立 judge の価値, ADR 0019/0022):

   > Task(ux-reviewer): `$PROBE_JSON` を採点して

   レポートをファイルに保存する (例: `/tmp/ux-judge-report.md`)。

4. 採点を検証してスコアボードへ投稿する:

   ```bash
   cicd/scripts/ux-probe/post-judge-score.sh /tmp/ux-judge-report.md
   ```

   **検証に落ちたら投稿しない** — 蓄積は時系列データで、壊れた 1 件が混ざるとトレンド判断が
   そのぶん狂う。人が転記していた頃は目視が検証だったので、無人化にあたって検証を機械へ移した。
   `validate-judge-score.py` が見るのは形式だけでなく、**total / max が scores と整合しているか**、
   **verdict が rubric の閾値と一致するか**、**UNKNOWN に理由が付いているか**まで。
   ここが狂うと M2 のトリガー判定がそのまま狂うため。

   検証に落ちたときの終了コード: `2` = 採点ブロックが無い (judge が rubric の出力ルール 3 に
   従っていない) / `3` = 内容が不整合。いずれもレポートは残るので、原因を見て judge を再実行する。

5. トレンドを見る (蓄積コメントから JSON を抽出):

   ````bash
   # コメント本文から ```json ブロックを正規表現で切り出し (行区切りに依存しない)、
   # ux-judge-score のみを 1 行 1 採点で出す
   gh api repos/yomote/mind-inbox/issues/127/comments --paginate \
     | jq -r '.[].body
         | try (capture("(?s)```json\\s*(?<j>.*?)```").j | fromjson)
         | select(.kind == "ux-judge-score")
         | "\(.scoredAt)\t\(.scenarioId)\t\(.total)/\(.max)\t\(.verdict)\tUNKNOWN:\(.unknowns | length)"'
   ````

   (集計が育ったら M3 で可視化を整える — 現状は目視で足りる件数)

### Routine (登録済み)

- 名前: `ux-judge — 毎朝の UX 採点 (ADR 0027 D1)` / ID `trig_01B2tk2Z8kRrsnHAwSgRJQcX`
- スケジュール: 毎朝 08:00 JST — cron (UTC) `0 23 * * *` (golden-path-monitor の 07:00 JST 実行が終わってから)
- セッション種別: 毎回新規セッション (fresh session per fire)
- プロンプトの中身は Routine 側が正典 (`update_trigger` で編集する)。実行する手順は上の Steps 2〜4 と同じ

2026-08-09 に登録した ([#156](https://github.com/yomote/mind-inbox/issues/156))。
以前 **「claude-code-remote MCP サーバー全体が承認ゲートの内側にあり agent からは登録できない」**
と記録していたが、これはセッション横断の恒久的な制約ではなく**セッションごとの権限差**だった
— MCP が許可されたセッションからは `list_triggers` / `create_trigger` / `update_trigger` が
そのまま通る。#90 / #92 も同じ理由で滞留していたので、同様に解ける見込み。

### gh が無い環境でのフォールバック

Claude Code on the web の実行環境には **`gh` CLI が無く**、`GITHUB_TOKEN` での
`api.github.com` 直叩きも 403 になる (2026-08-09 実測)。上の 2 本のスクリプトはどちらも
冒頭で `command -v gh` を見て終了コード 1 で落ちるため、その環境では GitHub MCP で代替する
(Routine のプロンプトにも同じ手順が入っている):

- **記録 JSON の取得** (Step 2 の代替):
  1. `actions_list` (method=`list_workflow_runs`, resource_id=`golden-path-monitor.yml`, per_page=1) → 最新 run ID
  2. `actions_list` (method=`list_workflow_run_artifacts`, resource_id=`<run id>`) → `ux-probe-<run id>` の artifact ID。無ければ終了コード 3 と同じ扱い (無投稿で終了)
  3. `actions_get` (method=`download_workflow_run_artifact`, resource_id=`<artifact id>`) の URL を curl + unzip
- **検証と投稿** (Step 4 の代替): `python3 cicd/scripts/ux-probe/validate-judge-score.py <レポート>`
  を直接回し、**終了コード 0 のときだけ** `add_issue_comment` で #127 へ投稿する。
  検証は gh に依存しないので、**「検証に落ちたら投稿しない」という不変条件はフォールバック側でも保たれる** — ここを飛ばさないこと

## Verification

- [ ] golden-path-monitor の run に artifact `ux-probe-<run_id>` があり、JSON の `turns` が 4 件ある
- [ ] レイテンシ閾値超過があれば run の Annotations に warning が出ている
- [ ] **人手を介さず** Issue #127 に採点コメントが増えている (verdict + JSON ブロック) — Routine 初回発火は 2026-08-10 08:00 JST。**未検証**なので翌朝に確認する
- [ ] 上の Steps 5 の jq が、増えたコメントを 1 行として抽出できる (蓄積が機械可読なままか)

## Rollback

- 蓄積はコメント追記のみで破壊的操作なし。誤採点コメントは編集せず、訂正コメントを追加して
  旧コメントに「訂正あり → リンク」と追記する (時系列の改ざんを避ける)
- シナリオを変えた場合 (`ux-probe.spec.ts` の `SCENARIO.id` 更新) はスコアの断絶点になる —
  Issue #127 に区切りコメントを 1 本入れる

## Common Issues

### artifact `ux-probe-<run_id>` が無い

- 原因: プローブ手前 (curl 版 golden-path / 結線カナリア) で fail してプローブ未到達、
  または AZURE\_\* variables 未設定で全体スキップ
- 対処: run の step summary / NG 行でホップを特定。プローブ自体の問題ではない

### 記録はあるが turns が 4 件未満

- 原因: 途中の往復で対話が壊れた (応答空 / stub / タイムアウト)。記録は 1 往復ごとに
  書き出すので、壊れる直前までは残っている
- 対処: 残った turns とE2E ログで壊れたホップを切り分け。採点は完了分のみで可 (judge に明示)

### judge のスコアが信用できない気がする

- 原因: 生成側と judge が同系モデル (ADR 0022 の既知リスク)
- 対処: PO の抜き打ち監査 — 記録 JSON の生の会話を直接読み、乖離があれば rubric を直す
  (rubric 改定は PO の専権)。M1.5 較正実験 (#123 コメント) で一致率を測る

## Related

- ADR: [0022 UX 自律改善ループ](../adr/0022-autonomous-ux-improvement-loop.md) / [0019 独立 judge](../adr/0019-independent-judge-agents-security-qa-release.md) / [0018 動作検証](../adr/0018-runtime-verification-in-the-loop.md)
- rubric: `.github/claude/ux-rubric.md` / subagent: `.claude/agents/ux-reviewer.md`
- プローブ実装: `apps/frontend/e2e-live/ux-probe.spec.ts` / workflow: `.github/workflows/golden-path-monitor.yml`
- epic: [#123](https://github.com/yomote/mind-inbox/issues/123) / スコアボード: [#127](https://github.com/yomote/mind-inbox/issues/127)
