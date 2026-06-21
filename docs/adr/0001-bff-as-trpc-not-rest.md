# 0001. BFF を REST ではなく tRPC で書く

- Status: Accepted
- Date: 2026-06-21
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: 既存実装の遡及的記録 (#11) — 判断自体はリポジトリ初期から下されている

## Context and Problem Statement

Mind Inbox の BFF (Backend for Frontend) は Frontend (React SPA) からのみ呼ばれ、
AI Agent / VOICEVOX サービスのオーケストレーションを担う。Frontend と BFF はどちらも
TypeScript の単一モノレポ内にある。両者の I/O 契約をどの方式で結ぶか
(REST + OpenAPI 手書き / REST + 生成クライアント / tRPC) を決める必要がある。

## Decision Drivers

- Frontend ↔ BFF の型安全性 (リクエスト/レスポンスの型ずれを compile 時に検出したい)
- コード生成パイプラインを増やさない (OpenAPI → client codegen の運用コストを避けたい)
- BFF は外部公開 API ではなく自前 Frontend 専用 (公開 REST 契約の汎用性は不要)
- 実装速度 (PoC 段階で endpoint 追加を軽くしたい)

## Considered Options

- Option A: **tRPC** (router の zod schema を Frontend が型として直接 import)
- Option B: REST + OpenAPI 手書き + client 自動生成
- Option C: REST + 各 endpoint を素の Azure Functions で実装し型は手で合わせる

## Decision Outcome

Chosen option: **"Option A" (tRPC)**。
Frontend と BFF が同一モノレポ・同一言語 (TS) であり、BFF は自前 Frontend 専用のため、
公開 REST 契約の汎用性よりも **end-to-end の型安全性**と**codegen を挟まない軽さ**が勝る。
tRPC は router の型を Frontend が直接 import するだけで型が通り、生成ステップが不要。
Azure Functions v4 上では tRPC fetch アダプタを単一 HTTP エントリポイント
(`/api/trpc/{path}`) に載せることで両立できる。

外部ツール (Swagger UI / Postman 等) から参照したい場合の OpenAPI は、tRPC が真実のまま
**router から自動生成** (#8) して補う。手書きの REST 契約は持たない。

### Positive Consequences

- Frontend ↔ BFF のリクエスト/レスポンス型が compile 時に一致保証される
- OpenAPI → client codegen のパイプラインを運用しなくてよい
- endpoint 追加が router に procedure を 1 つ足すだけで済む
- zod schema が入力バリデーションと型の単一の真実になる

### Negative Consequences

- BFF が tRPC client 以外 (素の HTTP / 他言語クライアント) から呼びにくい。外部公開する場合は別途 OpenAPI 生成 (#8) で補う
- tRPC は単一 HTTP エントリポイントに集約されるため、Azure Functions の per-route な機能 (route 単位の認証/スロットリング) を素直に使えない
- ベンダー (tRPC) 固有の表現に乗るため、将来 REST へ寄せる場合は router を作り直す必要がある

## Pros and Cons of the Options

### Option A: tRPC (採用)

router の型を Frontend が直接 import する。`/api/trpc/{path}` 単一エントリ。

- Good, because codegen なしで end-to-end 型安全
- Good, because zod が入力検証と型の単一の真実
- Good, because endpoint 追加が procedure 追加だけで軽い
- Bad, because 外部/他言語クライアントから呼びにくい (OpenAPI 生成で緩和)

### Option B: REST + OpenAPI 手書き + client 生成

OpenAPI を手書きし client を codegen する。

- Good, because 言語非依存の汎用契約になり外部公開しやすい
- Bad, because OpenAPI 手書きと実装の乖離が起きやすい
- Bad, because codegen パイプラインの運用コストが常時かかる

### Option C: 素の Azure Functions で REST を手実装

各 endpoint を Functions として書き型は手で合わせる。

- Good, because Functions の per-route 機能をそのまま使える
- Bad, because Frontend ↔ BFF の型一致を人手で保つことになり退行しやすい

## Links

- 実装: `apps/bff/src/trpc/router.ts` / `apps/bff/src/functions/trpc.ts`
- 関連: OpenAPI 自動生成 [#8](https://github.com/yomote/mind-inbox/issues/8) — tRPC を真実に保ったまま OpenAPI を派生
- 戦略: [docs/documentation/strategy.md §4.2](../documentation/strategy.md)
