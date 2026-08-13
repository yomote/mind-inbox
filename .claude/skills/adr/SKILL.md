---
name: adr
description: ADR (Architecture Decision Record) を書く・採番する・Status を動かすときの規約。アーキテクチャ判断 (フレームワーク / クラウドサービス / 覆すと影響が広い設計前提 / セキュリティ構造) を決める直前、新しい ADR ファイルを作ろうとしたとき、既存 ADR の Status を変えようとしたとき、user が「/adr」「ADR 書いて」「これ ADR 案件?」等と言ったときに起動。運用・プロセスの決め事は ADR ではない (その判定もここでする)。
---

# adr

`docs/adr/` は「なぜそういう構成 / 技術選択をしたか」を**不変の記録**として残す場所。この skill は、ADR を新規に書く / 番号を取る / Status を動かす / 索引を直す、の 4 つを踏み外さないための手順。

正典は [`docs/adr/README.md`](../../../docs/adr/README.md) と [`docs/documentation/strategy.md`](../../../docs/documentation/strategy.md)。ここはそこから「毎回間違える所」を抜き出したもの。

## いつ起動するか

- アーキテクチャに関わる判断をしようとしている**直前** (実装より前。後から書くと意図が薄れる)
- `docs/adr/NNNN-*.md` を新規に作ろうとしたとき (**採番だけでも必ずここを通す**)
- 既存 ADR の `Status` を書き換えようとしたとき
- user が `/adr`「ADR 書いて」「これ ADR に残す?」等と言ったとき

---

## Step 1 — そもそも ADR か判定する

**書くもの (アーキテクチャ判断)**:

- フレームワーク / ライブラリ / クラウドサービスの採用・廃止
- 選択肢があり得る構成の判断 (REST vs tRPC / AKS vs Container Apps)
- 後から覆すと影響範囲が広い設計上の前提 (`mockApi.ts` を真実にする 等)
- セキュリティ / コンプライアンスに関わる構造的な決定

**書かないもの**:

- 実装詳細 (関数名・ファイル分割) / 一時的な対処・バグ修正
- 運用手順 → Runbook (`docs/runbooks/`)
- **開発の運用・プロセスの決め事** — エージェントの回し方 / レビュー体制 / PM 機構 / セッション分配 / CI の運転ルール。**これらは ADR にしない**

> 運用・プロセスを ADR にしない理由: この領域は数日で改訂され、不変記録の棚として機能しない (同じテーマが 3〜4 代替わりし、最新を特定するのに複数本読む羽目になる)。置き場は `CLAUDE.md` と `.claude/skills/`。

**判定の一言**: 「これは 1 年後も同じ判断のままか?」— 揺れるなら ADR ではない。

## Step 2 — 番号を取る

**`origin/main` の最大番号 +1** (4 桁)。**退役番号を必ず合算する**:

```bash
git fetch origin main -q
{ git ls-tree -r origin/main --name-only docs/adr/ | grep -oE 'docs/adr/[0-9]{4}-' | grep -oE '[0-9]{4}'
  git show origin/main:docs/adr/archive/retired-numbers.txt | grep -oE '^[0-9]{4}$'
} | sort -n | tail -1
```

- ⚠️ **`ls docs/adr/` のローカル最大値を使わない** — 並行セッションが同じ番号を取り、過去 2 回衝突している。**採番は書く瞬間にこのコマンドで取る** (セッション開始時の値は、別 PR が ADR を着地させると腐る)。取り違えは CI (`adr-number-guard`) が退役番号の再利用も含めて赤にする
- ⚠️ **`docs/adr/` の実ファイルだけを数えない** — `archive/` のファイルは名前から番号を落としてあるので、実ファイルだけでは使用済みが見えない。`retired-numbers.txt` を合算する
- ⚠️ **欠番は埋めない** — 0008 / 0011 / 0014 / 0018〜0022 … が飛んでいるのは退役番号。番号は ID であって順序ではない。振り直すと Issue / PR 本文の「ADR 0030 を見て」が全部リンク切れになる (リポジトリ外なので機械置換が届かない)

## Step 3 — 書く

```bash
cp docs/adr/template.md docs/adr/NNNN-{kebab-case-slug}.md
```

MADR 3.0 形式。最低限埋める: Status / Context and Problem Statement / Considered Options / Decision Outcome (chosen option + 理由) / Consequences (positive / negative)。日本語で書く。

## Step 4 — Status を動かす

```
Proposed ─→ Accepted ─→ Deprecated (使われなくなった)
                    └→ Superseded by NNNN (別 ADR が代替)
                    └→ Rejected (採用しなかったが記録は残す)
```

- **エージェント起案の ADR は `Status: Proposed` で入れる**。Proposed の判断を前提に実装を進めてよいが、承認キューとして残る
- **`Accepted` へ遷移させるのは user だけ** — design-gate / debrief の場で user が承認したときに更新する。エージェントが自分の ADR を Accepted にしない
- **過去 ADR の本文は書き換えない**。状態が変わったときだけ Status 行を更新するか、新規 ADR で supersede する
- ADR-only の PR を出し、**実装より先に**承認を得るのが基本形。ADR 本文のマージは Proposed のままでもよいが、その判断に依存する**不可逆な実装**を同じ PR に含めるなら裁定を待つ

## Step 5 — 索引を更新する

[`docs/adr/README.md`](../../../docs/adr/README.md) の「既存 ADR」節に 1 行足す。**同じ PR で**。

- 並びは**番号順ではなくテーマ順** (ドメイン / フロントエンド / BFF / 音声 / インフラ)。適切な節に入れる
- 無印 = Accepted。それ以外は行末に Status を明記する (`— **Superseded by 0013**` 等)
- 冒頭の「既存 ADR (N 本)」の件数も直す

## `docs/adr/archive/` の扱い

- ここにあるのは **ADR ではない**。運用・プロセスとして書かれていた 29 本の退避先
- **現行ルールではない。** 「今どう動かすか」は `CLAUDE.md` と `.claude/skills/` を見る
- ファイル名から番号を落としてあるので、**「ADR 0035」と呼ばない**。参照するときはパスで指す
- `Status:` 行は退避時点で凍結。**ここを直して運用を変えようとしない**
- 過去の判断の経緯を追う必要が出たときだけ読む

---

## やらないこと

- ❌ 運用・プロセスの決め事を ADR にする (数日で覆るものを不変記録の棚に置かない)
- ❌ ローカル最大値 +1 での採番 / 欠番の穴埋め / 番号の振り直し
- ❌ `retired-numbers.txt` を見ずに採番する
- ❌ エージェント判断で `Proposed` → `Accepted` にする
- ❌ 実装だけ先に入れて ADR を後追いで書く
- ❌ ADR を足して `docs/adr/README.md` の索引を放置する
- ❌ `docs/adr/archive/` を現行ルールとして引用する / 編集する

## 失敗時の挙動

- `origin/main` を fetch できない → 採番できないので**新規 ADR を作らない**。オフラインなら本文だけ先に書き、番号はネットワーク回復後に決める (仮番号でファイルを作らない)
- 既に同じ番号が open PR にある → CI (`adr-number-guard`) が赤にする。**自分が後発なら自分が動く** (main の最大 +1 を取り直す)
- ADR にすべきか判断がつかない → 判定の一言 (Step 1) に戻る。それでも揺れるなら user に選択肢形式で聞く

## 関連

- 正典: `docs/adr/README.md` / `docs/documentation/strategy.md`
- テンプレート: `docs/adr/template.md`
- 退避済み運用系: `docs/adr/archive/README.md`
- 退役番号: `docs/adr/archive/retired-numbers.txt`
- 承認の場: `design-gate` skill (実装前) / `debrief` skill (事後の Accept/Reject)
