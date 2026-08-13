---
name: dispatch
description: セッション運用 (hub-and-spoke) と作業の分配。窓口 PM をどう名乗り旧窓口をどう退役させるか、その作業を親が直接書くか subagent に出すか子セッションに出すかの判断、起票パケットの必須項目、子セッションを起こすときの必須事項を扱う。user が対話で新しいセッションを開いたとき、複数の作業を抱えて着手順を決めるとき、subagent / 子セッション (`create_session`) を起こそうとしたとき、user が「/dispatch」「並列でやって」「子セッションに投げて」等と言ったときに起動。
---

# dispatch

Mind Inbox は **hub-and-spoke** で開発を回す。user の対話窓口は親 (PM) セッション 1 本で、独立した作業は親が subagent / 子セッションへ分配する。この skill は「誰が何を実行するか」を決めて、実行者を正しく起こすまでの手順。

**並列化が既定**。着手する前に「独立に走らせられる筋は何本あるか」を数え、順番に片付けたくなる衝動を止める。直列に倒してよいのは依存が実在するときだけ。

## いつ起動するか

- **user が対話で新しいセッションを開いたとき** (最初のメッセージが挨拶だけでも)
- 作業が複数あって、着手順・実行者を決めるとき
- subagent (`isolation: "worktree"`) や子セッション (`create_session`) を起こそうとするとき
- user が `/dispatch`「並列でやって」「分担して」「子セッションに投げて」等と言ったとき

---

## Step 1 — 窓口 PM として名乗る

**起票パケット (対象 Issue / 完遂条件 / ファイル境界) や当番 Routine のプロンプトを与えられていない対話セッションは、既定で窓口 PM**。user の最初の用件に入る前に:

1. GitHub のライブ状態を復元する (open PR / `needs-human` / Proposed ADR / 自動起票 Issue)
2. 冒頭に「🙋 あなたの番」を付けた `/status` 報告を出す
3. そのうえで用件に入る

**窓口は常に 1 本・使い捨てローテーション。** 節目やコンテキスト劣化時に次代を開いて移る。**識別と退役はエージェントが機械で行う** (user に何も貼らせない):

- 自分に `[PM] Mind Inbox ハブ (YYYY-MM-DD〜)` を `set_session_title` で付ける
- 旧窓口を `[PM-retired] <元タイトル>` にリネームして `archive_session` する
- 複数の対話セッションが同時に開いていたら、**新しい方が窓口を名乗り、古い方を退役させる**
- 例外: **実行中の窓口は退役させない** / archive の前に in-flight の作業を GitHub (Issue / PR) へ書き出す

> **退役操作をしてよいのは新しく開かれた対話セッション (窓口 PM) だけ。** 当番 PM Routine・子セッション・subagent は退役操作を一切しない (使い捨ての当番が対話窓口を archive すると、user が話している最中のセッションが消える)。

子セッションの命名規約は無い。

## Step 2 — 実行者を決める

判断軸は **作業の大きさ**と**往復が要るか**の 2 つ。

| 実行者 | 出す作業 |
| --- | --- |
| **親が直接書く** | レビュー指摘への対応 / 1〜2 ファイルの修正 / 設定調整 |
| **subagent** (`isolation: "worktree"`) | 複数ファイル / 調査を伴う / 途中で判断が要る / 結果を読んで次を決める作業 |
| **子セッション** (`create_session`) | 指示を一度で言い切れて、成果が PR / Issue に残る作業 |

- subagent に出す理由は独立性ではなく**親のコンテキストの経済** (実装者とレビュアーの分離はレビュー側の judge が担う)
- **子との会話は片道 約 1 分**。`send_message` / `list_events` はこの環境に無く、メッセージは `create_trigger` + `run_once_at` を相手セッションに bind して送る。**往復が 1 回増えるごとに 1 分待つので、短い往復を繰り返す作業は子に出さず subagent にする**
- 親 → 子の向きだけが確認済み。**子 → 親の報告は Issue コメントを既定**にする
- **design-gate 対象の設計判断は分配しない** — 親でゲートを通してから分配する
- **user にクリックを肩代わりさせない**

## Step 3 — 起票パケットを書く

subagent にも子セッションにも、指示文は**必ずこの 4 点 + 子には 2 点**を満たす:

1. **対象 Issue**
2. **完遂条件** (何が揃ったら終わりか)
3. **触ってはいけないファイル境界** (並行作業と衝突する場所を名指しで禁じる)
4. **CLAUDE.md を読むこと**
5. (子のみ) **詰まったら Issue にコメントを残して終了すること** — 黙って止まらせない
6. (子のみ) **報告先** — 既定は対象 Issue へのコメント

## Step 4 — 子セッションを起こす

```text
create_session(
  title:           "[子] 何をする子か",
  prompt:          <Step 3 の起票パケット>,
  source_url:      "https://github.com/yomote/mind-inbox",
  source_revision: "main" または作業ブランチ,
)
```

- **`source_url` と `source_revision` は必須**。環境は継承されるが**リポジトリは継承されない** — 省略すると子は空の作業ディレクトリで止まる
- 起動には 1〜3 分かかる
- **使い終わったら `archive_session` する**
- 子が止まったら `get_session` の `status_category` を見る。**`need_input` なら権限待ち** — `needs_action` のツール名を `.claude/settings.json` の `allow` に足す (その場しのぎで user に承認させない)
  - ただし **`deny` に載っているツールは足さない** — 塞いだのは意図。子には commit + PR に切り替えさせる
  - **allow の追加は起動済みの子には効かない**。子は clone した時点のブランチの設定を読むので、**足したうえで子を起こし直す** (設定を main に入れるか、修正済みブランチを `source_revision` に指定する)
  - **MCP サーバ名は対話セッションと子セッションで違う** (`Claude_Code_Remote` / UUID `bf7c680d-…`)。allow は**両方の名前**を書く。破壊的なツールはサーバ単位ではなくツール単位でも列挙する

## Routine の制約

**実務を回す Routine は web UI から作る。エージェントの `create_trigger` はセッション間メッセージ専用。**

- `create_trigger` には **`source_url` を渡す口が無く、作られた Routine は `sources` を持たない**。発火したセッションはリポジトリを掴めず、モデルも指定できず、MCP connector も付かない。**仕事をさせる Routine としては使えない**
- 使ってよいのは、**既にリポジトリを持っている相手セッションへ `run_once_at` でメッセージを届ける**経路だけ
- **`update_trigger` は「自分が作った Routine」しか通らない** — PO が web UI で作った Routine はエージェントから読めても書けない

---

## やらないこと

- ❌ 独立に走らせられる作業を数えずに直列で片付ける
- ❌ 対話で開いた新セッションが窓口 PM を名乗らずに用件へ入る
- ❌ 当番 Routine・子・subagent が窓口の退役操作をする / 実行中の窓口を archive する
- ❌ 短い往復を繰り返す作業を子セッションに出す (1 往復 1 分。subagent にする)
- ❌ `source_url` / `source_revision` を渡さずに子を起こす
- ❌ ファイル境界を書かずに分配する (並行作業が同じファイルで衝突する)
- ❌ 子から user への直接報告 (成果は PR / Issue / `needs-human` に残し、親が集約して報告する)
- ❌ 実務 Routine を `create_trigger` で作る
- ❌ 権限待ちを user のクリックで解決する

## 失敗時の挙動

- 子が `need_input` のまま進まない → `needs_action` のツールを allow に足し、**起こし直す** (待っても解けない)
- 子が黙って止まった → `get_session` で状態を確認し、成果が無ければ親か subagent で引き取る。**「たぶん動いている」で放置しない**
- `create_session` が使えない → subagent (`isolation: "worktree"`) に倒す。分配自体を諦めない
- 分配先の作業が想定より対話を要すると分かった → 子を archive して subagent で引き取る (往復を積まない)

## 関連

- 手順の詳細: `docs/runbooks/child-sessions.md`
- 権限設定の現物: `.claude/settings.json`
- 状況の集約: `status` skill / 設計の承認: `design-gate` skill
- セッション記録: `docs/debrief/journal.md`
