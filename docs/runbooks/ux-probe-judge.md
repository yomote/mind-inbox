# UX プローブと UX judge の運用 (#123 M0/M1)

## Trigger

- 毎朝の golden-path-monitor 実行後、UX プローブ記録 (相談 4 往復の会話 + レイテンシ) を採点したいとき
- プローブを手動で回したいとき (プロンプト変更後の確認等)
- スコアの時系列トレンドを見たいとき

## Prerequisites

- `gh` CLI (repo read + issues write) — **満たせるのは人間の手元だけ**。agent セッションでは使えないので GitHub MCP で代替する (下記「agent セッションでは gh が使えない」)
- 採点は Claude セッションから subagent `ux-reviewer` を起動できること
- 背景理解: [ADR 0022](../adr/0022-autonomous-ux-improvement-loop.md) / rubric は `.github/claude/ux-rubric.md` (**真実**)

## データの流れ (どこに何が残るか)

| データ                                        | 置き場                                                                                                                    | 保持       |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ---------- |
| 会話全文 + 区間レイテンシ (`ux-probe-record`) | **記録 Issue [#162](https://github.com/yomote/mind-inbox/issues/162) のコメント** (1 run = 1 コメント) — **採点が読む先** | 永続       |
| 同上 (障害調査用の一次情報)                   | golden-path-monitor の artifact `ux-probe-<run_id>` — 人間が `gh` で取る                                                  | 90 日      |
| レイテンシ閾値超過 (#120)                     | 同 run の warning annotation + step summary                                                                               | run と同じ |
| judge 採点 (`ux-judge-score`)                 | **スコアボード Issue [#127](https://github.com/yomote/mind-inbox/issues/127) のコメント** (1 採点 = 1 コメント)           | 永続       |

記録を artifact と Issue コメントの両方に置いている理由は [ADR 0029](../adr/0029-probe-record-transport-via-issue-comment.md) — **agent セッションからは artifact をダウンロードできない** (取得先の `*.blob.core.windows.net` が egress ポリシーで拒否される / [#160](https://github.com/yomote/mind-inbox/issues/160))。採点が読む「正」はコメント側で、artifact は人間が掘るための一次情報。

リポジトリへの commit 蓄積は不採用 (monitor に `contents: write` を渡す必要 + 毎朝のノイズ commit を避けた)。

## Steps

**既定は無人** (#154 / [ADR 0027](../adr/0027-ux-improvement-loop-ab-protocol-and-mutation-boundary.md) D1) — 毎朝の Routine が下の 1〜4 を自動で回す。以下は **Routine が実行する手順であり、同時に手動フォールバックの手順**でもある (Routine 未登録時 / 個別に採点したい時は同じコマンドを人が打てばよい)。

1. プローブを回す (毎朝 07:00 JST は自動。手動なら):

   ```bash
   gh workflow run golden-path-monitor.yml
   gh run watch "$(gh run list -w golden-path-monitor.yml -L 1 --json databaseId -q '.[0].databaseId')"
   ```

2. 記録 JSON を取得する。**経路は 2 つあり、どちらも同じ判断部品を通る** (ADR 0029):

   **agent (毎朝の Routine — 既定):** 記録 Issue [#162](https://github.com/yomote/mind-inbox/issues/162) の
   最新コメントを GitHub MCP (`issue_read` method=`get_comments`) で読み、本文をファイルに保存してから:

   ```bash
   OUT="$(cicd/scripts/ux-probe/probe-record-comment.py extract /tmp/comment.md /tmp/ux-probe)"
   PROBE_JSON="$(cicd/scripts/ux-probe/inspect-probe-artifact.py "$OUT")"
   ```

   **人間 (手元 / 個別に採点したいとき):** `gh` があるので artifact から直接取れる:

   ```bash
   PROBE_JSON="$(cicd/scripts/ux-probe/fetch-latest-probe.sh)"
   ```

   どちらも標準出力は**パスだけ**。診断は stderr に出る (混ぜると呼び出し側が壊れる)。
   終了コード: `3` = 記録が無い (プローブ手前で fail / 全体スキップ) / `4` = turns 0 件 /
   `1` = 記録が壊れている。`3` と `4` は「採点する材料がない」ので、その朝は採点をスキップして
   終わる (Common Issues 参照)。`extract` は `2` = コメントに記録ブロックが無い。

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

### agent セッションでの経路 (gh は使えない)

`fetch-latest-probe.sh` / `post-judge-score.sh` は **人間の手元専用**。agent セッションでは
`gh` も artifact ダウンロードも通らないため、記録は Issue コメントで運ぶ
([ADR 0029](../adr/0029-probe-record-transport-via-issue-comment.md) / [#160](https://github.com/yomote/mind-inbox/issues/160))。

- **記録の取得** (Step 2): `issue_read` (method=`get_comments`) で記録 Issue
  [#162](https://github.com/yomote/mind-inbox/issues/162) の最新コメントを読み、本文を保存してから
  `probe-record-comment.py extract` → `inspect-probe-artifact.py`。
  **`actions_*` で artifact を取りに行かないこと** (必ず落ちる)
- **検証と投稿** (Step 4): `python3 cicd/scripts/ux-probe/validate-judge-score.py <レポート>`
  を直接回し、**終了コード 0 のときだけ** `add_issue_comment` で #127 へ投稿する。
  検証は gh に依存しないので「検証に落ちたら投稿しない」は agent 経路でも保たれる — ここを飛ばさないこと

<details>
<summary>gh / artifact が通らないことの実測 (2026-08-09)</summary>

- `gh` は apt で入れられる (2.45.0) が、`api.github.com` がゲートウェイで拒否される
  (`HTTP 403: GitHub access is not enabled for this session.`)。`curl` で直に叩いても同じ。
  インストールでは解けない。git の fetch/push は別経路の credential helper を通るので動く
- GitHub MCP の `download_workflow_run_artifact` が返す署名付き URL も egress で拒否される
  (`connect_rejected / productionresultssa16.blob.core.windows.net:443`)。ホスト名の
  `productionresultssaNN` は可変なので狭い許可では足りない

</details>

## Verification

- [ ] golden-path-monitor の run に artifact `ux-probe-<run_id>` があり、JSON の `turns` が 4 件ある
- [x] **人手を介さず** 記録 Issue #162 に `ux-probe-record` のコメントが 1 件増えている — **2026-08-10 07:37 JST に初回を確認** ([run 31339682965](https://github.com/yomote/mind-inbox/actions/runs/31339682965) / `work-overwhelm-v1` 4/4 往復 / warning 0 件)
- [ ] レイテンシ閾値超過があれば run の Annotations に warning が出ている
- [ ] **人手を介さず** Issue #127 に採点コメントが増えている (verdict + JSON ブロック) — **初回 (2026-08-10 08:02 JST) は投稿されず**。原因調査は [#194](https://github.com/yomote/mind-inbox/issues/194)
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

### #162 に記録は増えたのに #127 に採点が増えない

2026-08-10 の初回でこの形になった ([#194](https://github.com/yomote/mind-inbox/issues/194))。切り分けは**上流から順に**:

1. **記録は読めるか** — #162 の最新コメントを保存して `probe-record-comment.py extract` → `inspect-probe-artifact.py`。
   ここが通るならコードは無罪 (初回はこの手順で通ることを実データで確認済み)
2. **Routine が発火したか** — `list_triggers` の `last_fired_at`。**claude-code-remote MCP は承認ゲートで塞がることがある**
   ([#160](https://github.com/yomote/mind-inbox/issues/160) の「セッションごとの権限差」。通る/通らないが安定しない)
3. **検証で止まったのか** — `validate-judge-score.py` が落ちた場合は**仕様どおり投稿しない**。
   バグではなく judge の出力側の問題なので、レポートを見て rubric との差を確認する

**「投稿されない」は必ずしも異常ではない** — 材料なし (終了コード 3 / 4) と検証落ちは、どちらも無投稿が正しい振る舞い。

### judge のスコアが信用できない気がする

- 原因: 生成側と judge が同系モデル (ADR 0022 の既知リスク)
- 対処: PO の抜き打ち監査 — 記録 JSON の生の会話を直接読み、乖離があれば rubric を直す
  (rubric 改定は PO の専権)。M1.5 較正実験 (#123 コメント) で一致率を測る

## Related

- ADR: [0022 UX 自律改善ループ](../adr/0022-autonomous-ux-improvement-loop.md) / [0019 独立 judge](../adr/0019-independent-judge-agents-security-qa-release.md) / [0018 動作検証](../adr/0018-runtime-verification-in-the-loop.md)
- rubric: `.github/claude/ux-rubric.md` / subagent: `.claude/agents/ux-reviewer.md`
- プローブ実装: `apps/frontend/e2e-live/ux-probe.spec.ts` / workflow: `.github/workflows/golden-path-monitor.yml`
- epic: [#123](https://github.com/yomote/mind-inbox/issues/123) / スコアボード: [#127](https://github.com/yomote/mind-inbox/issues/127)
