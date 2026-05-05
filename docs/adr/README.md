# Architecture Decision Records (ADR)

> アーキテクチャに関わる判断を不変の記録として残す場所。
> 戦略全体: [`docs/documentation/strategy.md`](../documentation/strategy.md)

## ADR とは

「なぜそういう構成 / 技術選択をしたか」を残すドキュメント。実装が変わってもこの判断記録は残し続ける。

## いつ書くか

次のような判断をする**前に**書く:

- フレームワーク / ライブラリ / クラウドサービスの採用・廃止
- 異なる選択肢があり得るアーキテクチャ判断 (例: REST vs tRPC、AKS vs Container Apps)
- 後から覆すと影響範囲が広い設計上の前提 (例: mockApi.ts を真実にする)
- セキュリティ / コンプライアンスに関わる構造的な決定

書かなくて良いもの:

- 実装詳細 (関数名、ファイル分割の仕方)
- 一時的な対処 / バグ修正
- 運用手順 (Runbook の領域)

## 書き方

### 1. 番号を決める

`ls docs/adr/` で既存の最大番号を確認し、次の連番 (4 桁) を使う。

### 2. ファイルを作る

`docs/adr/NNNN-{kebab-case-slug}.md` の形式。

```bash
cp docs/adr/template.md docs/adr/0006-my-decision.md
```

### 3. 書く

[`template.md`](./template.md) は MADR 3.0 形式。最低限埋めるセクション:

- Status (`Proposed` で開始)
- Context and Problem Statement
- Considered Options
- Decision Outcome (chosen option + 理由)
- Consequences (positive / negative)

### 4. レビュー

ADR-only の PR を出す。**実装より先に承認**を得る。承認時に Status を `Accepted` に変更。

## Status 遷移

```
Proposed  ─→  Accepted  ─→  Deprecated  (使われなくなった)
                       └→  Superseded by NNNN  (別 ADR が代替)
                       └→  Rejected            (採用しなかったが記録は残す)
```

過去 ADR は**書き換えない**。状態が変わった時のみ Status 行を更新する (もしくは新規 ADR で supersede する)。

## CLAUDE.md からの参照

エージェントが過去判断を覆さないよう、CLAUDE.md からこのディレクトリにリンクする (#13 で実施)。

## 既存 ADR

(初期 5 本は #11 で起こす)

- 0001 — BFF を tRPC で書く判断
- 0002 — Container Apps (scale-to-zero) を選択した判断
- 0003 — bootstrap → config の 2-phase Bicep IaC
- 0004 — `mockApi.ts` を frontend mock の唯一の真実とする
- 0005 — UI 仕様は MDX が真実、実装が乖離したら実装を直す
