---
name: debrief
description: 前回以降にマージされた PR と Proposed のままの ADR をまとめて、対話型の「ゼミ」セッションをやる。何を作ったか・なぜこの設計か・代替案は何だったかを可視化つきで解説し、user の理解度を対話で確認し、Proposed ADR をその場で Accept/Reject する。user が「/debrief」「ゼミやろう」「振り返り」「最近のマージ解説して」「溜まってる判断見せて」等と言ったとき、または Proposed ADR やマージ PR が溜まっているのに気づいたときに起動。設計背景は ADR 0014。
---

# debrief

設計決定・マージが溜まったあとに開く**ゼミ型の振り返りセッション**。ループを止めずに進んだ分の「なぜ」を user がまとめてキャッチアップし、理解度を対話で確かめ、**Proposed のままの ADR に user の承認 (Accept/Reject/修正) を入れる**。`/status` が「進捗」を pull するのと対で、これは「理解と承認」を pull する。

## いつ起動するか

- user が `/debrief`・「ゼミ」・「振り返り」・「最近何やったか解説して」等と言ったとき
- Proposed ADR が 2 件以上、またはマージ PR が数本溜まっているのに気づいたとき (エージェントから提案してよい)
- design-gate を通らずに進んだ無人セッションの作業を追認するとき

## 設計思想

- **真実は GitHub と docs 側**。skill は解説内容を持たず、毎回マージ PR の diff・ADR・コードをライブで読んで解説を生成する (陳腐化しない)
- **教材は自リポジトリ**。一般的な技術概念 (例: tRPC とは、Container Apps とは) も、必ず「このリポジトリではこう使われている」に接続して説明する
- **理解確認は穴の発見のため**。逆質問は正解率を測るのでなく、曖昧な箇所を見つけて解説し直すために使う
- **承認をこの場で完結させる**。ADR の Status 変更・journal 追記までやってセッションを閉じる (「あとで反映」を残さない)

---

## 手順

### Step 1 — 前回以降の差分を集める

1. `docs/debrief/journal.md` の最新エントリ日付 = 前回マーカーを読む (エントリが無ければ全期間)
2. GitHub MCP (`mcp__github__search_pull_requests`、ToolSearch で読み込み) で前回以降の**マージ済み PR** を取得 — query: `repo:yomote/mind-inbox is:pr is:merged merged:>{前回マーカー日付}`。マーカーが無い (初回) 場合のみ `list_pull_requests` (state closed, sort updated desc) で直近から引く
3. `grep -l "^- Status: Proposed" docs/adr/*.md` で **Proposed のままの ADR** を列挙 (**行頭アンカー (`^-`) を落とすと本文中の説明文に誤マッチする** — 2026-08-12 に Accepted の ADR 0014 を未裁定として数えた実例あり)
4. 前回以降に追加・大幅更新された `docs/design/`・`docs/adr/` も対象に含める

### Step 2 — アジェンダ提示

集めた項目を「①解説対象 (マージ PR・新設計) / ②承認待ち (Proposed ADR)」に分けて一覧提示し、user に順番・スキップの希望を聞く。項目が多いときは重要度順 (アーキテクチャ判断 > 機能 > 修正) に絞る提案をする。

### Step 3 — 項目ごとのゼミ

各項目について、この順で解説する:

1. **何を作ったか** — 動くものベースで 1〜2 文 (画面・API・挙動)
2. **なぜこの設計か** — 判断とその理由。関連 ADR があれば要点を引く
3. **捨てた代替案** — 何と比べて選んだか、比較表 1 枚
4. **一般技術との接続** — 出てくる技術概念を「このリポジトリではこう」の形で解説 (user の技術学習パート)

可視化: 構成・フローは mermaid 図で。項目が多い・図が多いセッションは全体を HTML レポート 1 枚 (Artifact ツールが使える環境ではそれで公開。無ければ HTML ファイルを書いて渡す) にまとめてから対話する。

各項目の最後に**逆質問を 1〜2 問** (「この構成だと〜のとき何が起きる?」形式)。曖昧だった箇所は角度を変えて解説し直す。user からの質問にはコード・docs を開いて答える。

### Step 4 — Proposed ADR の承認

承認待ちの各 ADR について user の決定を取る: **Accept / Reject / 修正して再提案**。

- Accept → ADR の `Status: Proposed` を `Accepted` に編集
- Reject → `Rejected` に編集し、理由を ADR に 1〜2 文追記。実装済みコードへの影響 (巻き戻しの要否) を整理して Issue 化を提案
- 修正 → user の指摘を ADR に反映して Proposed のまま置き、次回 or その場で再判断

### Step 5 — 記録して閉じる

`docs/debrief/journal.md` の先頭にエントリを追記: 日付 / debrief / 扱った項目 / ADR の決定 / 学びメモ (user が曖昧だった → 解説し直した箇所。次回の解説の深さ調整に使う) / 持ち越し。ADR の Status 変更とあわせてコミットする。最後に「次アクション」(残った Proposed、次にゲートが要りそうな設計) を 1 行で示す。

---

## やらないこと

- ❌ 解説・進捗スナップショットの skill 内ハードコード (毎回ライブで引く)
- ❌ user 不在のままの ADR Accept (承認は user の行為。エージェントは遷移させる操作だけ代行)
- ❌ 進捗数字の集計 (それは `/status`。ここは理解と承認)
- ❌ 新しい設計の起案 (それは design-gate の前段。ここは事後の追認と学習)

## 失敗時の挙動

- journal が無い / マーカー不明 → 全期間を対象にし、多すぎる場合は直近から絞る提案をする
- GitHub MCP が使えない → ローカルの `git log` でマージコミットを代替取得
- 対象がゼロ (溜まっていない) → 「ゼミの材料なし」と 1 行で返して終了。無理に開催しない

## 関連

- ADR 0014 (この仕組みの設計判断): `docs/adr/archive/operations/design-comprehension-gate-and-debrief.md`
- 記録: `docs/debrief/journal.md`
- 対: `design-gate` skill (事前・同期のゲート) / `status` skill (進捗の pull) / `explain` skill (オンデマンド可視化)
