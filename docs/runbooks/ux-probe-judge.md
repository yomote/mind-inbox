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

1. プローブを回す (毎朝 07:00 JST は自動。手動なら):

   ```bash
   gh workflow run golden-path-monitor.yml
   gh run watch "$(gh run list -w golden-path-monitor.yml -L 1 --json databaseId -q '.[0].databaseId')"
   ```

2. 記録 JSON を取得する:

   ```bash
   RUN_ID="$(gh run list -w golden-path-monitor.yml -L 1 --json databaseId -q '.[0].databaseId')"
   gh run download "$RUN_ID" -n "ux-probe-$RUN_ID" -D /tmp/ux-probe
   ls /tmp/ux-probe   # ux-probe-<timestamp>.json
   ```

3. UX judge で採点する — Claude セッションから **新品コンテキストの subagent** として起動する
   (実装セッション内で直接採点しない — 前提の混入を防ぐのが独立 judge の価値, ADR 0019/0022):

   > Task(ux-reviewer): `/tmp/ux-probe/ux-probe-<timestamp>.json` を採点して

4. レポート末尾の 1 行サマリ + `ux-judge-score` JSON ブロックを、**呼び出し元セッションが**
   スコアボード Issue #127 にコメントとして転記する (judge 自身は投稿しない)。

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

## Verification

- [ ] golden-path-monitor の run に artifact `ux-probe-<run_id>` があり、JSON の `turns` が 4 件ある
- [ ] レイテンシ閾値超過があれば run の Annotations に warning が出ている
- [ ] Issue #127 に採点コメントが増えている (verdict + JSON ブロック)

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
