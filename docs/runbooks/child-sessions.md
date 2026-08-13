# Runbook: 子セッションを起こして会話する

親 (PM) セッションから子セッションを起こし、追加指示を届け、成果を回収するまでの手順。

判断の背景: [ADR 0048](../adr/archive/operations/child-sessions-are-usable-again-with-a-one-way-poke-channel.md) / 2026-08-12 の実測。
分配してよい作業の種類は [CLAUDE.md](../../CLAUDE.md) の「分配の基準」を見ること
(**往復が多い作業は子ではなく subagent**)。

## 前提

`.claude/settings.json` の `permissions.allow` に、**MCP サーバ名が 2 つとも**、
**さらに破壊的なツールがツール単位でも**入っていること (現物は
[`.claude/settings.json`](../../.claude/settings.json) を見ること)。

同じ Claude Code Remote の MCP サーバが、**対話セッションでは `Claude_Code_Remote`、子セッションでは
UUID 名 `bf7c680d-...` として見える**。片方しか書いていないと、もう片方の系統で毎回承認プロンプトが出て
子が止まる (これが 2026-08-12 まで「毎回承認が飛んでくる」と言われていた状態の原因)。

サーバ単位の `mcp__<server>` だけでは足りない。**`delete_trigger` はツール単位で書くまで止まった**。
`create_session` / `archive_session` / `set_session_title` / `interrupt_session` / `delete_trigger` /
`update_trigger` は両方の名前でツール単位に列挙する。

`set_session_title` は **2026-08-12 に PO が常設許可**にした (窓口の識別と退役をエージェントが機械で行う /
[ADR 0043](../adr/archive/operations/pm-self-driving-mode.md) D5・CLAUDE.md)。これが無いと、窓口が自分に `[PM]` を付ける /
旧窓口を `[PM-retired]` にリネームする動作のたびに承認プロンプトが出て、**無人セッションでは止まる**。

**`permissions.deny` も要る。** `mcp__github` をサーバ単位で許可すると、PR を経由せずリポジトリを
直接書き換える `delete_file` / `push_files` / `create_or_update_file` まで通ってしまうので、この 3 つは
`deny` に置く (代替は commit + PR なので、塞いでも仕事は止まらない)。**`ask` は使わないこと** —
当番 PM Routine や子セッションには**クリックする人間がいない**ので、`ask` は永久停止になる。
`deny` はサーバ単位の `allow` に優先する (2026-08-12 実測)。

**UUID 名になるのは Claude Code Remote だけ。github は子セッションでも `mcp__github__*` のまま**
(2026-08-12 実測 — 子が `mcp__github__issue_read` / `add_issue_comment` を使えたと報告している)。
なので `deny` は 1 つの名前で足りる。**全部のサーバが二重名だと一般化しないこと** — 新しいサーバを
足すときは、子セッションから見た実際の名前を 1 回確かめる。

> **`settings.json` の変更は、実行中のセッションには反映されない。** 効くのは次に開くセッションから。
> 「直したのにまだ承認が飛ぶ」の大半はこれ。**main に入るまでは、新しく開いたセッションでも
> 承認が飛び続ける** (子は clone したブランチの設定を読むため、ブランチを指定した子だけは先に効く)。

## 1. 起こす

```text
create_session(
  title:            "[子] 何をする子か",
  prompt:           <起票パケット。下の「プロンプトに必ず書くこと」を参照>,
  source_url:       "https://github.com/yomote/mind-inbox",
  source_revision:  "main"  または作業ブランチ,
  tags:             ["<まとめて探せる名前>"]
)
```

**`source_url` / `source_revision` は必須。** 環境 (`environment_id`) は継承されるが
**リポジトリは継承されない** — 省略すると子は空の作業ディレクトリで起動し「repo が無い」と言って止まる。

起動には 1〜3 分かかる (`SESSION_STATUS_PENDING` → `RUNNING`)。

### プロンプトに必ず書くこと

[ADR 0028](../adr/archive/operations/dispatch-packet-in-issue-and-session-start-preflight.md) の起票パケット 4 点に、子セッション固有の 2 点を足す。

1. 対象 Issue
2. 完遂条件
3. **触ってはいけないファイル境界**
4. CLAUDE.md を読むこと
5. **詰まったら Issue にコメントを残して終了すること** (黙って止まらせない)
6. **報告先** — 既定は「対象 Issue にコメントを残す」(下の §3)。Routine で親に直接返させるのは未検証なので、当てにしない

## 2. 追加指示を送る (親 → 子)

**`fire_trigger` は使わない。** API は成功を返すが**配送されない** (2026-08-12 に 3 方向で実測。`last_fired_at` すら付かない)。
使うのは `run_once_at` を**現在時刻の 1〜2 分後**に置いた Routine。

```text
create_trigger(
  name:                  "[親→子] 何の指示か",
  prompt:                <本文。子にはユーザー発言として届く>,
  persistent_session_id: "<子のセッション ID>",
  run_once_at:           "<現在の UTC + 2 分>"   # 例: 2026-08-12T14:53:00Z
)
```

**遅延は約 1 分**(スケジューラのポーリング + 起床)。実測では `last_fired_at 14:54:00` →
子の受信 `14:54:16` → 指示の実行完了 `14:54:26`。**コンテナが `disconnected` に落ちた子も起き直る**。
1 往復ごとに 1 分かかるので、**細かく指示を出し直す作業は子に出さない**。

`last_fired_at` は反映が遅れることがある。**発火の判定は trigger のフィールドではなく、
子の `updated_at` が動いたか / 成果が GitHub に出たかで行う**。

Routine は発火後 `ended_reason: run_once_fired` で無効化され、二度と発火しない。ただし
**`list_triggers` の一覧には残り続ける**ので、一覧を汚したくなければ `delete_trigger` で消す
(機能上は消さなくても害はない)。

## 3. 返信を受け取る (子 → 親)

**経路は 2 つある。既定は GitHub。**

| 経路                                             | 検証状況        | 使いどころ                                                                                                                                      |
| ------------------------------------------------ | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Issue コメント** (既定)                        | ✅ **実測済み** | 成果・詰まり・診断。**記録が残る**ので後から誰でも拾える ([ADR 0021](../adr/archive/operations/parent-session-as-pm-orchestrator.md) 条項 3・4) |
| **`create_trigger` + `run_once_at` を親に bind** | ⚠️ **未検証**   | 親にすぐ気づいてほしいとき。親 → 子の方向は実測済みだが、**子 → 親の方向はまだ通していない**。当てにせず、通す前に必ず実測すること              |

**子 → 親の既定は Issue コメント一択にする。** 子セッションは `mcp__github__*` を承認プロンプト無しで
使えることが実測済みで、記録も残る。逆方向の Routine 経路は理屈の上では同じ仕組みだが、
**この向きで成功を確認した実測がまだ無い** — 「親 → 子が通ったから子 → 親も通るはず」で書くと、
親が返信待ちのまま沈黙する。子は自分のセッション ID をシステムプロンプトから知っているので、
検証するときは親の ID を渡して bind させれば足りる。

**親が「送った」で終わらせないこと。** 送信 API の成功は配送の証拠にならない。
`get_session` で `updated_at` が動いたか、Issue にコメントが増えたかで**受信を確認する**。

## 4. 生死を見る / 詰まりを外す

```text
get_session(session_id)
```

`post_turn_summary.status_category` を見る。

| 値             | 意味                     | 対処                                                                                                                                                                                                                                         |
| -------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `review_ready` | 1 ターン終えて待機       | 成果を回収する                                                                                                                                                                                                                               |
| `need_input`   | **承認プロンプトで停止** | `needs_action` のツール名を `.claude/settings.json` の `allow` に足して**起こし直す**。その場しのぎで user に承認させない。**`deny` に載っているツール (下の §前提) は足さない** — 塞いだのは意図なので、子には commit + PR に切り替えさせる |
| `failed`       | 落ちた                   | ログは取れないので、指示を直して起こし直す                                                                                                                                                                                                   |

`interrupt_session` で止められるが、**割り込んだセッションは `failed` に落ちて以後メッセージを受け取らなくなる**
(2026-08-12 実測)。止めるなら作り直す前提で。

## 5. 片付ける

```text
archive_session(session_id)
```

子はコンテナを 1 つ占有し続ける。**使い終わったら必ず archive する**。
まとめて探せるよう、起こすときに `tags` を付けておくこと。

## 動作検証 (この Runbook が生きていると言える条件)

新しく子を 1 つ起こして、次が全部通ること。1 つでも欠けたら手順に戻る
(**「たぶん効いている」で先に進まない**)。

1. `source_url` を渡した子が clone 済みで起動する (`current_branches` に値が入る)
2. 子が承認プロンプト無しで 1 ターン完走する (`status_category` が `need_input` にならない)
3. `run_once_at` の Routine で送った指示が届き、**子が実際にそれを実行する** (Issue にコメントが増える等、外から見える形で確認する)
4. `archive_session` で片付く

## Links

- 判断: [ADR 0048](../adr/archive/operations/child-sessions-are-usable-again-with-a-one-way-poke-channel.md) / [ADR 0033](../adr/archive/operations/parent-implements-via-subagent-when-child-sessions-are-gated.md) / [ADR 0021](../adr/archive/operations/parent-session-as-pm-orchestrator.md)
- 残課題: [#353](https://github.com/yomote/mind-inbox/issues/353) (`fire_trigger` が配送しない理由 / `delete_trigger` だけ承認が出る理由)
