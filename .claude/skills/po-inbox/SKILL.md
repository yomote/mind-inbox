---
name: po-inbox
description: user (PO) 宛ての依頼・承認待ち・持ち越しを全ソースからライブ集計して「あなた待ち一覧 + 次の一手」を 1 画面で出す PO 受信箱。user が「/po-inbox」「私待ちのやつある?」「何を見ればいい?」「依頼来てる?」「自分がボトルネックになってない?」等と言ったとき、またはセッション再開時に user が何から手を付けるべきか迷っているときに起動。/status が「進捗」を出すのと対で、これは「user 待ち」を出す。設計背景は ADR 0017。
---

# po-inbox

user (PO) 宛ての「あなた待ち」を **1 画面に集計する受信箱ビュー**。依頼は 6 箇所 (needs-human Issue / Open PR / Proposed ADR / debrief の溜まり / journal 持ち越し) に散在するので、毎回すべてをライブで引いて優先度順に並べ、**次の一手を 1 つ名指す**。ADR 0017 の読み取り側。

> 製品側の「受信箱」(v2 M3, ADR 0015) とは別物。これは開発ループの PO 向けビュー。

## いつ起動するか

- user が「/po-inbox」「私待ち」「依頼来てる?」「どこ見ればいい?」等と言ったとき
- セッション再開時、user が何から手を付けるか迷っているとき
- エージェントが user 宛て依頼を積んだ直後に全体像を見せたいとき

## 設計思想

- **真実は各ソース側** (ADR 0011 / 0017)。skill は状態・スナップショットを持たず、毎回ライブで引く
- **ミラーしない**。Proposed ADR や持ち越しを Issue 化せず、ビューが都度拾う
- **優先度が主役**。フラットな一覧でなく「ループを止めているもの」を先頭に
- **次の一手を 1 つ名指す** (`/status` と同じ思想)。受信箱は眺めるためでなく動くために出す
- **数字の整合を自己申告**。引き損ねたソースを「ゼロ件」と誤報しないため、集計対象ソースを明示する

---

## 手順

### Step 1 — 全ソースをライブで引く

repo は `yomote/mind-inbox`。GitHub MCP ツール (`mcp__github__*`、ToolSearch で `select:` して読み込む) とローカル repo の両方を使う。ローカル repo が古い可能性があるときは先に `git fetch origin main` して main 基準で読む。

1. **needs-human Issue** — `list_issues` (state OPEN, labels `["needs-human"]`, fields: number/title/labels/updated_at)
2. **Open PR** — `list_pull_requests` (state open)。draft は区別して表示
3. **Proposed ADR** — `grep -l "Status: Proposed" docs/adr/*.md`
4. **debrief の溜まり** — `docs/debrief/journal.md` 最新エントリの日付をマーカーに、`search_pull_requests` で `repo:yomote/mind-inbox is:pr is:merged merged:>{マーカー日付}` の件数を取る
5. **journal 持ち越し** — journal 最新エントリの「持ち越し」行 (「なし」以外なら拾う)

### Step 2 — 優先度 4 段にマッピング

| 段 | 中身 | なぜこの位置か |
| --- | --- | --- |
| 🔴 **今すぐ** | needs-human のうち `P1` または本文が「ループ停止」を示すもの | 返答までループが止まっている |
| 🟠 **早め** | Open PR (レビュー・マージ待ち) / needs-human の作業系 (`P2` 等) | 放置すると conflict・文脈風化で腐る |
| 🟡 **まとめて** | Proposed ADR / debrief 開催提案 | 非同期で良い。まとめて処理する方が効率的 |
| ⚪ **背景** | journal 持ち越し | 期限なし。忘却防止のリマインド |

**debrief 開催提案の閾値** (debrief skill の起動条件と揃える): Proposed ADR が 2 件以上 **または** マーカー以降のマージ PR が 3 本以上 → 🟡 に「/debrief 開催」を 1 項目として出す。

### Step 3 — 次の一手を 1 つ選ぶ

🔴 があれば最古の 🔴、無ければ 🟠 → 🟡 の順。同段に複数あれば「所要が短い順」で選ぶ (5 分で返せる判断を先に返した方がループの再開が早い)。

### Step 4 — 出力

既定は下記 compact 形式 (1 画面)。user が「詳しく」と言った時のみ各項目にリンク・本文要約を付す。

```markdown
## 📮 PO 受信箱（{YYYY-MM-DD}） — あなた待ち {N} 件

🔴 **今すぐ（ループ停止中）**
- #{n} {タイトル} — {放置されると何が止まるか 1 行}

🟠 **早め（放置すると腐る）**
- PR #{n} {タイトル} — レビュー/マージ待ち {経過日数}

🟡 **まとめて（非同期で OK）**
- Proposed ADR {k} 件: {番号列挙} → 次の debrief / design-gate で裁定
- （閾値超過時のみ）/debrief 開催提案 — マージ PR {m} 本が未解説

⚪ **背景（持ち越し）**
- {journal 最新エントリの持ち越し}

**次の一手** → **{1 つを名指し。例: #89 に返答（ブランチ保護の設定 5 分）}**

集計ソース: needs-human Issue / Open PR / Proposed ADR / journal（全 {日時} 時点）
```

- 全段 0 件なら「📮 あなた待ちゼロ。ループは全部回っています」と 1 行で返す (無理に項目を作らない)
- 最終行の「集計ソース」は必ず出す — どのソースを見た上での「ゼロ」かを user が検証できるようにする (誤報防止)

### HTML ダッシュボードモード

user が「HTML で」「ダッシュボードで」「見れる形で」等と言ったときは、同じデータを 1 枚の HTML ダッシュボードにする。

- 集計は Step 1〜3 と同じライブデータ (HTML のためにやり直さない)
- 構成: ヘッダ (日付・総件数) → 優先度 4 段のカード列 (🔴 が最上部・最大) → 次の一手 (1 つを大きく) → 集計ソースの脚注
- 描画前に artifact-design skill (chart を描くなら dataviz も) を読む。Artifact ツールが使える環境ではそれで公開、無ければ HTML ファイルを書いて渡す

---

## 依頼を「積む」側の規約 (エージェント向けリマインダ)

このビューに載るのは Issue に積まれた依頼だけ。**user に非同期で何かを頼むときはチャットで言うだけで終わらせず**、ADR 0017 の規約で Issue を作る:

- ラベル `needs-human` + タイトル接頭辞 `[needs-human]`
- 本文冒頭に **種別** (判断 / 作業 / 確認) と **「放置されると何が止まるか」1 文**
- ループを止める依頼には `P1`
- Proposed ADR・debrief・持ち越しは Issue 化しない (ビューが docs から直接拾う)

## やらないこと

- ❌ 集計結果・Issue 番号の skill 内ハードコード (毎回ライブで引く)
- ❌ Proposed ADR や持ち越しのミラー Issue 作成 (ADR 0017 のミラー禁止)
- ❌ 依頼への代理応答・Issue の close (受信箱は読み取り専用。処理は user の行為)
- ❌ 進捗の集計 (それは `/status`。ここは「user 待ち」だけ)

## 失敗時の挙動

- GitHub MCP が未接続 → ToolSearch で読み込む。それでも不可なら、ローカル grep で引けるソース (Proposed ADR / journal) だけ集計し、**引けなかったソースを明示して**出す (「ゼロ」と偽らない)
- journal が無い / マーカー不明 → debrief 溜まり判定をスキップし、その旨を集計ソース行に記す

## 関連

- ADR 0017 (この仕組みの設計判断): `docs/adr/0017-needs-human-queue-and-po-inbox.md`
- ADR 0014 (承認の構造 — design-gate / debrief / Proposed キュー): `docs/adr/0014-design-comprehension-gate-and-debrief.md`
- 対: `status` skill (進捗の pull) / `debrief` skill (溜まりの消化)
