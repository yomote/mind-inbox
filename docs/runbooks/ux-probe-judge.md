# UX プローブと UX judge の運用 (#123 M0/M1)

## Trigger

- 毎朝の golden-path-monitor 実行後、UX プローブ記録 (相談 4 往復の会話 + レイテンシ) を採点したいとき
- プローブを手動で回したいとき (プロンプト変更後の確認等)
- スコアの時系列トレンドを見たいとき

## Prerequisites

- `gh` CLI (repo read + issues write)
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

**既定は無人** (#154 / [ADR 0026](../adr/0026-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) D1) — 毎朝の Routine が下の 1〜4 を自動で回す。以下は **Routine が実行する手順であり、同時に手動フォールバックの手順**でもある (Routine 未登録時 / 個別に採点したい時は同じコマンドを人が打てばよい)。

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

### Routine の登録 (人間の 1 クリック宿題)

Routine の登録は Web UI 操作が必要で **agent からは実行できない** — claude-code-remote MCP
サーバー全体が承認ゲートの内側にあり、`create_trigger` / `create_session` はもちろん
読み取り専用の `list_environments` すら `-32003 requires approval` で弾かれる
(2026-08-08 に実測)。#90 / #92 / #156 が滞留しているのはすべてこの 1 つの原因による。
登録の宿題は [#156](https://github.com/yomote/mind-inbox/issues/156)。

登録する Routine のプロンプト (そのまま貼れる形):

> Mind Inbox (yomote/mind-inbox) の UX 採点セッションです。`docs/runbooks/ux-probe-judge.md`
> の Steps 2〜4 を実行してください。
>
> 1. `cicd/scripts/ux-probe/fetch-latest-probe.sh` で直近の記録 JSON を取得する。
>    終了コード 3 (artifact 無し) / 4 (turns 0 件) の場合は、その旨だけを報告して終了する
>    (採点する材料がないので Issue には何も投稿しない)。
> 2. subagent `ux-reviewer` を**新品コンテキスト**で起動し、そのパスを採点させる。
>    レポートを `/tmp/ux-judge-report.md` に保存する。
> 3. `cicd/scripts/ux-probe/post-judge-score.sh /tmp/ux-judge-report.md` で検証・投稿する。
>    検証に落ちたら投稿せず、失敗理由を Issue #127 にではなく**あなたの応答として**残す。
>
> rubric (`.github/claude/ux-rubric.md`) は読むだけで、**改定しないこと** (PO の専権)。
> プロダクトコードは一切変更しないこと。

スケジュール: 毎朝 08:00 JST (golden-path-monitor の 07:00 JST 実行が終わってから)。
cron (UTC) では `0 23 * * *`。

## Verification

- [ ] golden-path-monitor の run に artifact `ux-probe-<run_id>` があり、JSON の `turns` が 4 件ある
- [ ] レイテンシ閾値超過があれば run の Annotations に warning が出ている
- [ ] **人手を介さず** Issue #127 に採点コメントが増えている (verdict + JSON ブロック) — Routine 登録後の翌朝に確認する
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
