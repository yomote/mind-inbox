# 0048. 子セッションは再び起動できる — 会話は Routine 経由で片道 1 分なので、分配先は往復の少ない作業に限る

- Status: Accepted (debrief, 2026-08-12)
- Date: 2026-08-12
- Deciders: omoteforlab (2026-08-12 の debrief で Accept)
- Consulted: —
- Informed: —

Technical Story: 2026-08-12、PO から「Claude のルーティン一覧は見えるか / コセッションは作れるか」と問われ、実測したところ [ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) の前提が崩れていた。

## Context and Problem Statement

[ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) は **「この実行環境では `create_session` が承認ゲートで弾かれる」** という実測を前提に、分配先を子セッションから subagent へ切り替えた。同 ADR の D3 は「`create_session` が通る環境では従来どおり子に出してよい。通るなら子の方が優れる」と、復帰条件まで書いてある。

2026-08-12 に実測したところ、**`create_session` は通る**。`create_trigger` / `fire_trigger` / `delete_trigger` も通る。つまり D3 の復帰条件は満たされた。

しかし同じ実測で、D3 が想定していなかった制約が 2 つ見つかった。

1. **リアルタイムの会話 API が無い。** この環境の Claude Code Remote MCP には `send_message` / `list_events` が実装されておらず、`ListAgents` も子を認識しない (`No reachable agents`)。親が子から同期的に取れるのは `get_session` の `post_turn_summary` (status カテゴリ + 一行の要約) だけである。**メッセージを送る経路は別途見つかった** (下記 D6 — `run_once_at` 付き Routine を相手セッションに bind する) が、遅延が約 1 分あり、`fire_trigger` による即時 poke は配送されない
2. **同じ MCP サーバが、セッションの種類によって別名で見える。** 対話セッションでは `Claude_Code_Remote`、`create_session` で起こした子セッションでは UUID 名 `bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a`。権限の allow をどちらか片方の名前でしか書いていないと、もう片方では毎回承認プロンプトが出て子が停止する

したがって「D3 の条件が満たされたので子セッションへ戻す」と単純には言えない。**復帰させるが、分配してよい作業の形を狭める**という判断が要る。

## Decision Drivers

- **user のクリックを減らす** — [ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) の原点。承認ゲートの穴埋めを user にさせない
- **親のコンテキストの経済** — subagent に出す唯一かつ実測された理由 (ADR 0033 改訂の記録)
- **投げた作業が迷子にならないこと** — 往復できない相手に「途中で判断が要る作業」を渡すと、黙って止まる
- **沈黙と正常を区別する** — 子が黙ったとき、成功なのか死んだのか判別できること (CLAUDE.md の最頻事故)

## Considered Options

- Option A: [ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) 以前に戻す (原則すべて子セッションへ分配)
- Option B: **子セッションを復帰させるが、「投げ切れる作業」に限る。往復が要るものは subagent のまま**
- Option C: ADR 0033 のまま据え置く (子セッションは使わない)

## Decision Outcome

Chosen option: **"Option B"**。`create_session` が通るようになった以上 [ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) D3 は発動する。会話も (D6 の経路で) できる。ただし**片道が約 1 分**なので、細かく指示を出し直しながら進める作業は依然として subagent の方が速い。ADR 0033 の「作業の大きさで決める」表は捨てず、そこに「往復の回数」の軸を足す。

### 決定の内訳

- **D1 分配先は 3 択になる。** ADR 0033 D1 の表を次で置き換える。

  | 作業 | 担い手 |
  | --- | --- |
  | レビュー指摘への対応 / 1〜2 ファイルの修正 / 設定・ワークフローの調整 | **親が直接書く** |
  | 何度も結果を見て指示を出し直す / 短い往復を繰り返す / 親が結果を読んで即座に次を決める | **subagent (`isolation: "worktree"`)** |
  | 指示をおおむね言い切れて長時間かかる / 真に並行させたい / 成果が PR・Issue に残る | **子セッション (`create_session`)** |

  判断軸は**往復の回数**であって独立性ではない。**往復が 1 回増えるごとに 1 分待つ**ので、10 往復する作業を子に出すと subagent より遅くなる

- **D2 子セッションへ出すときは、起票パケットを一度で完結させる。** [ADR 0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) D1 / [ADR 0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) D2 の 4 点 (対象 Issue / 完遂条件 / **触ってはいけないファイル境界** / CLAUDE.md 参照) に加え、**「詰まったら Issue にコメントを残して終了する」** と **D7 の返信先** を必ず書く。追加指示は送れるが 1 分かかるので、最初に言い切れるものは言い切る

- **D3 `create_session` には `source_url` と `source_revision` を必ず渡す。** 環境は継承されるが **リポジトリは継承されない** — 省略すると子は空の作業ディレクトリで起動し、「repo が無い」と言って止まる (2026-08-12 実測)

- **D4 MCP の権限 allow は、対話セッション名と子セッションの UUID 名の両方を書く。さらに破壊的なツールはツール単位でも明示する。** `.claude/settings.json` に `mcp__Claude_Code_Remote` と `mcp__bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a` を並べ、**加えて `create_session` / `archive_session` / `interrupt_session` / `delete_trigger` / `update_trigger` を両方の名前でツール単位に書く**。

  - **片方の名前だけだと、もう片方の系統で毎回承認プロンプトが出る** — 従来 UUID 名の `list_triggers` 1 個だけが許可されていたのが、承認が飛び続けていた原因
  - **サーバ単位 (`mcp__<server>`) の allow だけでは `delete_trigger` が止まった。** ツール単位で明示して初めてゼロになった (2026-08-12 実測)
  - **`settings.json` の変更は実行中のセッションには反映されない。** 効くのは次に開くセッションから。「直したのにまだ承認が飛ぶ」の大半はこれ

- **D5 子の生死は `get_session` の `post_turn_summary` で見る。** `status_category` が `need_input` なら権限待ちで止まっている。**`needs_action` に出るツール名を D4 の allow に足す** のが恒久対応で、その場しのぎで user に承認させない

- **D6 セッション間のメッセージは「`run_once_at` を約 1 分後に置いた Routine を相手に bind する」で送る。** `create_trigger` に `persistent_session_id`(相手のセッション ID) と `run_once_at`(現在時刻 + 1〜2 分) を渡すと、`prompt` が相手セッションに**ユーザー発言として届き、相手はそれを実行する**。子 → 親も同じ手順 (子が親のセッション ID に bind する)。

  **`fire_trigger` による即時 poke は使わない** — API は成功を返すが配送されない。2026-08-12 に親 → 子 (idle) / 子 → 親 / 自己宛の 3 方向で試し、いずれも `last_fired_at` すら付かなかった。**「送信 API が成功した」を「届いた」と読み替えないこと**

- **D7 会話が続く相手には、返信先を必ず明示する。** 送るメッセージに**自分のセッション ID** と「返信は `create_trigger` + `run_once_at` でこの ID に bind して送る」ことを書く。相手は親の ID を知らないので、書かなければ返せない

- **Routine (`create_trigger`) はエージェントから作れる。** ただし**このツールで作った Routine は MCP connector を保存しない** (作成時に warning が出る) ため、発火セッションは Gmail 等の connector ツール無しで動く。connector が要る Routine は claude.ai の web UI から作る

### 未解決 (この ADR では決めない)

- **`fire_trigger` が配送しない理由は未特定。** 即時配送ができれば往復が 1 分から秒単位に縮むので、追う価値はある ([#353](https://github.com/yomote/mind-inbox/issues/353))
- **`SendMessage` / `ListAgents` によるチームメイト連携はこの環境では使えない** (`No reachable agents`)。体制はこれ抜きで組む ([#356](https://github.com/yomote/mind-inbox/issues/356))

### Positive Consequences

- **真の並行が戻る。** subagent は親の同時実行数に縛られるが、子セッションは独立したコンテナで動く
- **親のコンテキストが守られる** — [ADR 0021](0021-parent-session-as-pm-orchestrator.md) の元の狙いに近づく。子は自分で PR を出し、CI を追える
- **承認プロンプトが止まる** (D4)。PO の「毎回承認が飛んでくる」への直接の対処
- ADR 0033 は捨てずに済む — D3 が想定した復帰がそのまま起きただけで、思想の転換ではない

### Negative Consequences

- **投げた子と会話できない。** 指示を書き切る負担が親に乗る。書き切れない作業は子に出せない、という制約が恒常的に残る
- **子が黙ったときの診断が粗い。** `post_turn_summary` の一行しか無く、失敗の切り分けに子の再起動が要ることがある
- **分配先が 3 択になり、判断コストが増える。** ADR 0033 の「大きさだけ見ればよい」という分かりやすさが失われる
- **子セッションはコンテナを 1 つ占有する。** 使い終わったら `archive_session` する規律が要る (放置すると環境の資源を食う)
- **権限 allow をサーバ単位で広く開けた** (D4)。`delete_trigger` / `archive_session` のような破壊的操作も無確認で通るようになる

## Pros and Cons of the Options

### Option A: ADR 0033 以前に戻す (原則すべて子セッションへ分配)

- Good, because [ADR 0021](0021-parent-session-as-pm-orchestrator.md) の hub-and-spoke がそのまま復活し、規約が 1 本になる
- Good, because 親のコンテキストが最大限守られる
- Bad, because **子と会話できないので、途中で判断が要る作業が黙って止まる**。ADR 0033 が subagent で得ていた「結果を読んで次を指示する」ループが失われる
- Bad, because 起票パケットを書き切れなかったぶんが、そのまま手戻りになる

### Option B: 子セッションを復帰させるが「投げ切れる作業」に限る (採用)

- Good, because 往復が要る作業は subagent、要らない作業は子、と失敗モードで切り分けられる
- Good, because ADR 0033 の実測 (subagent に出す理由はコンテキストの経済) を否定せずに済む
- Good, because 子が黙っても、成果が GitHub に残る設計なので親が拾い直せる
- Bad, because 分配の判断軸が 2 つ (大きさ / 往復の要否) になり、規約として重くなる
- Bad, because 「往復が要るか」は着手前に読み切れないことがある

### Option C: ADR 0033 のまま据え置く (子セッションは使わない)

- Good, because 規約を変えなくてよい。判断コストが増えない
- Good, because subagent は結果を読んで軌道修正できるぶん、確実
- Bad, because **ADR 0033 D3 が自ら定めた復帰条件を満たしたのに従わない**ことになり、ADR が実態と乖離する
- Bad, because 真の並行が使えないまま。親の同時実行数が上限になる

## 動作検証

2026-08-12 に実測した内容 (すべて `create_session` で起こした使い捨てセッションで確認し、確認後 `archive_session`)。

| 確認したこと | 結果 |
| --- | --- |
| `create_session` が承認ゲートで弾かれないか | 通った (ADR 0033 の前提が崩れた) |
| `source_url` 無しで起こした子がリポジトリを持つか | **持たない**。作業ディレクトリが空で停止 (D3 の根拠) |
| `source_url` + `source_revision` を渡した子 | clone され、README を読んで正常終了 |
| 子セッションでの MCP サーバ名 | UUID 名 `bf7c680d-...` (D4 の根拠) |
| 修正前の allow で子が `create_trigger` を呼ぶ | 承認待ちで停止 |
| サーバ単位 allow だけを入れた子 | `create_trigger` / `fire_trigger` は無プロンプト。**`delete_trigger` だけ停止** |
| ツール単位 allow も足した子 | **承認プロンプトゼロ**。`create_trigger` / `update_trigger` / `delete_trigger` / `list_sessions` / `add_issue_comment` の 5 つ全部が無停止 ([証拠](https://github.com/yomote/mind-inbox/issues/353#issuecomment-5268573108)) |
| `send_message` / `list_events` の有無 | **無い**。`ListAgents` も `No reachable agents` |
| poke 専用 Routine + `fire_trigger` で送信 | **配送されない**。親 → 子 (idle) / 子 → 親 / 自己宛の 3 方向で `last_fired_at` すら付かず |
| `run_once_at` 付き Routine を子に bind して送信 | **届く**。`last_fired_at 14:45:58` → 子が 14:46:10 に起動 (`updated_at` が動いた)。**遅延は約 1 分** (D6 の根拠) |
| 同じ経路で送った指示を子が**実行**するか | **実行する**。`last_fired_at 14:54:00` → 子が 14:54:16 に受信 → 14:54:26 に [#353 へコメント投稿](https://github.com/yomote/mind-inbox/issues/353#issuecomment-5268488790)。**`disconnected` になっていた子も起き直した** |
| 子が `mcp__github__*` を持つか | **持つ**。承認プロンプト無しで Issue コメントを投稿できた (子 → 親の恒久的な回収経路になる) |
| 子が自分のセッション ID を知っているか | 知っている (システムプロンプトのセッションリンクから)。返信先として使える |

## Links

- 発端: 2026-08-12 の PO 質問「Claude のルーティン一覧って見れますか。またコセッションって作れますか」
- 関連 ADR: [0033](0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) (D3 の復帰条件が満たされた — D1 の表を本 ADR D1 が置き換え) / [0021](0021-parent-session-as-pm-orchestrator.md) (hub-and-spoke — 子の成果を GitHub に残す条項は維持) / [0028](0028-dispatch-packet-in-issue-and-session-start-preflight.md) (起票パケット — D2 で強化) / [0040](0040-project-continuity-three-layers.md) (Routine による継続性)
