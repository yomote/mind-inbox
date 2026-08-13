---
name: merge
description: PR を出したあとの追従と、マージの門の通し方。`subscribe_pr_activity` での監視、`[pm-accept]` の書式と規律、レビュースレッドを resolve してよい条件、auto-merge の武装、マージしてよい / 人間が押す の切り分けを扱う。PR を作成した直後、レビュー指摘や CI コメントが付いたとき、スレッドを resolve しようとしたとき、PR をマージしてよいか判断するとき、user が「/merge」「マージしていい?」「PR どうなってる?」等と言ったときに起動。
---

# merge

PR を作ったら放置せず、**merge / close されるまで追従する**。マージ可否は明文の解釈ではなく **`review-gate` の色**で読む — 判定機構は `cicd/scripts/review-gate/check.py` が正典。

## いつ起動するか

- PR を作成した直後 (監視の武装と受け入れの段取り)
- レビュー / CI のコメントが付いたとき
- レビュースレッドを resolve しようとしたとき
- 「この PR をマージしてよいか」を判断するとき
- user が `/merge`「マージしていい?」「PR どうなってる?」等と言ったとき

---

## Step 1 — 監視を武装する

PR を作ったら `subscribe_pr_activity` で監視を有効化する。

webhook は **CI 成功・新規 push・マージ遷移を配信しない**ので、それだけに完遂を依存させない。取りこぼしは定期チェックインで補い、merge / close で監視を終える。

## Step 2 — 指摘に対応する

- レビュー / CI のコメントは必ず調査する。**小さく確実な修正は push**、曖昧 / 重大な指摘は確認を取る
- **レビュースレッドを resolve してよいのは、指摘者 (代役 judge = `code-reviewer` subagent) の再レビューが OK を出してから**:
  1. 修正を push
  2. `code-reviewer` subagent を**再起動して再レビュー**
  3. **同じ指摘が再提起されないこと**を確認してから resolve
- **操作は PM、判定は指摘者。** 修正せず見送る場合 (別 Issue へ切り出し等) に再レビューでも再提起されたら、独断で畳まず PO に上げる
- docs のみの PR 等、レビュー対象外の指摘 (セルフレビュー・PM レビュー) は対応確認で resolve してよい
- **例外 (PO の個別裁定でのみ)**: (1) 指摘が対応済みか別 Issue へ移送済み、(2) 再レビュー不能の原因が**外部事情** (レビュー枠切れ・サービス障害) であって PR の中身ではない、(3) **PO が明示的に裁定する** — この 3 つが揃うときだけ resolve してよい。**エージェントの自己判断では使えない** (判定者を欠いたまま自分の宿題に合格を出す経路にしない)

> 代役 judge も Claude なので**独立性は完全ではない** — 同じモデルは同じ盲点を持つ。

## Step 3 — 受け入れる (`[pm-accept]`)

PR コメントに投稿する。`check.py` が数えるのは**次の 3 つが全部揃ったコメントだけ**:

1. マーカー **`[pm-accept]`** を含む
2. **いまの head SHA の先頭 7 桁**を含む
3. 投稿者の `author_association` が **OWNER / MEMBER / COLLABORATOR** (public リポジトリなので第三者のコメントでは門が開かない)

```text
[pm-accept] a1b2c3d — <一言の判定理由>
```

- **SHA を含める規約により、push すると自動的に失効する** (受け入れ後に積まれた未レビューコードのマージを防ぐため)
- **引き継ぎ (carryover)**: 受け入れ SHA から現 head までの追加コミットが **base (main) からのマージのみ**で、かつ **PR の実装差分 (`base...head`) が受け入れ時点と同一**なら受け入れは現 head に引き継がれる。実装差分が 1 文字でも変わる push は再受け入れが要る
- **中身を見ずに貼らない。** `[pm-accept]` のコピペは機構では検出できない — 差分を読み、完遂条件に照らして「やってほしいことがそこにあるか」を判断してから貼る。一言の判定理由を必ず添える

## Step 4 — 門を読む

`review-gate` は commit status として貼られ、ブランチ保護が required check として読む。**全部揃うまで failure**:

1. **PM の受け入れ** — Step 3 の条件 (引き継ぎ成立を含む)
2. **レビュースレッドが全部解決している**
3. **コード PR (`apps/` か `cicd/` に触れる) は独立レビューが 1 本ある** — Codex のレビュー、または代役 judge の `<!-- standin-review -->` + **現 head SHA** を含む権限保持者のコメント (引用行は数えない)

> workflow run の緑は「評価できたこと」しか意味しない。**判定の 🟢/🔴 は status 側に出る。**

## Step 5 — マージする

### マージの常設承認

**main への PR は、CI と `review-gate` がともに緑ならエージェントがマージしてよい。** 都度の確認は不要 (毎回聞かれる方が PO のコストになる)。マージ後は関連 Issue の close と持ち越しの確認まで済ませる。

### 例外 (必ず人間が押す)

- **リリース PR (`main → release`)** — judge が 🟢 でも merge / deploy は人間。**この常設承認は適用されない**
- **`needs-human` ラベルの付いた PR** / 未解決のレビュースレッドが残っている PR
- **PO が明示的に「保留」と言った PR**
- **`Status: Proposed` の ADR を実装まで含む PR** — ADR 本文のマージは可 (承認キューとして残す運用) だが、その判断に依存する**不可逆な実装**を含むなら裁定を待つ

### auto-merge を主経路にする

- 受け入れ (`[pm-accept]`) まで済ませたら **`enable_pr_auto_merge` (squash) を掛けて終わってよい**。マージはサーバー側で行われ、**セッションの生死に依存しない**。required check (CI + review-gate) が門を守る
- **auto-merge の武装 = PM の受け入れ意思表示**。review-gate のマージ執行が対象にするのは武装済み PR だけ (base=main / 非 draft / `needs-human` なし)
- **上の例外に当たる PR には掛けない**
- **定期チェックインは auto-merge の補助** (レビュー対応の取りこぼし確認など)。`send_later` ではなく `CronCreate` を使うが、**`CronCreate` はセッション内メモリでセッション終了と共に消える** — チェックインだけに完遂を依存させない
- 全 check 🟢 + auto-merge 有効のまま **2 時間**以上未マージなら機構がストールとして PR にコメントする。それを見たら原因を切り分ける (放置しない)

---

## やらないこと

- ❌ PR を出しっぱなしにする (merge / close まで追従する)
- ❌ 差分を読まずに `[pm-accept]` を貼る / 前のコメントをコピペする
- ❌ 再レビューの OK を待たずにスレッドを resolve する
- ❌ 「外部要因で再レビューが回せない」を**エージェントの判断で**適用する (PO の個別裁定のみ)
- ❌ 例外に当たる PR (リリース PR / `needs-human` / 保留 / Proposed ADR 依存の不可逆実装) をマージする / auto-merge を掛ける
- ❌ workflow run の緑をマージ可の根拠にする (見るのは `review-gate` の status)
- ❌ マージだけして関連 Issue の close と持ち越し確認を飛ばす

## 失敗時の挙動

- `review-gate` が赤で理由が読めない → status の description に不足項目が出る (受け入れ / 未解決スレッド数 / 独立レビュー)。引き継ぎ不成立の理由もそこに出る
- 引き継ぎが `実装差分が受け入れ時点から変化` → 正常動作。差分を確認して問題なければ**新しい head SHA で受け入れを取り直す**
- 全 check 緑・auto-merge 武装済みなのにマージされない → マージ API の失敗理由がログに出る (405 は GitHub の説明文つき)。**「まだマージできない」で放置せず**原因を切り分ける
- 指摘者が応答しない → Step 2 の例外条件を確認する。3 つ揃わなければ PO に上げる

## 関連

- 判定機構の正典: `cicd/scripts/review-gate/check.py`
- 引き継ぎ / Merge Queue の読み方: `docs/runbooks/merge-queue.md`
- レビュー judge の運用: `docs/runbooks/review-agents.md` / 観点は `.github/claude/*-rubric.md`
- リリース PR の判定: `release-gate` skill
- PR を出す前の確認: `pr-readiness` skill
