# 0004. `mockApi.ts` を Frontend モックの唯一の真実とする

- Status: Accepted
- Date: 2026-06-21
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: 既存実装の遡及的記録 (#11) — 判断自体はリポジトリ初期から下されている

## Context and Problem Statement

Frontend (React SPA) は BFF が無い状態でも全画面を開発・プレビューできる必要がある
(onboarding / home / session / result / actionPlan / history / settings 等)。
モックデータをどこに置くか — 各コンポーネント/Storybook/テストにばらまくか、
1 か所に集約するか — を決める必要がある。モックが複数箇所に散ると、画面ごとに
微妙に異なるダミーデータが生まれ、結合時に齟齬が出る。

## Decision Drivers

- モックデータの一貫性 (画面間でズレた形のダミーが生まれないこと)
- BFF 未起動でのフルプレビュー可能性
- テスト/preview/開発の fixture を 1 つに揃えたい
- BFF (tRPC) の型と乖離したら気づけること

## Considered Options

- Option A: **`apps/frontend/src/mockApi.ts` に全画面分のモックを集約**し単一の真実とする
- Option B: コンポーネント/画面ごとにローカルなモックを持つ
- Option C: MSW 等でネットワーク層をモックし fixture を別管理

## Decision Outcome

Chosen option: **"Option A"** (`mockApi.ts` 集約)。
モックを 1 ファイルに集約することで、全画面が**同一形状のダミーデータ**を共有し、
テスト・preview・ローカル開発の fixture が 1 つの真実に揃う。BFF が無くても
全画面をプレビューでき、tRPC の型と乖離すれば `mockApi.ts` の型エラーで気づける。
散在モック (Option B) は画面ごとにズレを生み、ネットワーク層モック (Option C) は
現フェーズには重い。

なお Frontend の**UI 仕様**の真実は MDX ([ADR 0005](0005-mdx-ui-spec-as-truth.md)) であり、
本 ADR が定めるのは「モックデータ (fixture) の真実が `mockApi.ts`」という点に限る。

## Positive Consequences

- 全画面が一貫した形のダミーデータを共有する
- BFF 未起動でもフル画面プレビュー・テストができる
- preview / テスト / 開発が同一 fixture を使い回せる
- tRPC 型との乖離が `mockApi.ts` のコンパイルで検知できる

## Negative Consequences

- `mockApi.ts` が肥大化しやすい (画面追加ごとに追記される)
- 実際の BFF レスポンスとモックがずれても、結合テストを通すまで気づかない可能性がある
- モックの「正しさ」は人手で BFF と合わせる必要がある (契約テストの範囲外)

## Pros and Cons of the Options

### Option A: `mockApi.ts` に集約 (採用)

全画面分のモックを 1 ファイルに置き単一の真実にする。

- Good, because 画面間でダミーデータの形が揃う
- Good, because BFF なしでフルプレビュー/テストできる
- Bad, because 単一ファイルが肥大化しやすい

### Option B: 画面ごとのローカルモック

各コンポーネント/画面が自前のモックを持つ。

- Good, because 画面単位で完結し見通しが良い場合がある
- Bad, because 画面間でダミーデータの形がズレ結合時に齟齬が出る

### Option C: MSW 等でネットワークモック

Service Worker でリクエストを横取りし fixture を返す。

- Good, because 実通信に近い形でモックでき本番に寄せやすい
- Bad, because セットアップが重く現フェーズの PoC には過剰

## Links

- 実装: `apps/frontend/src/mockApi.ts`
- 関連 ADR: [0005](0005-mdx-ui-spec-as-truth.md) — UI 仕様の真実は MDX (本 ADR は fixture の真実を定める)
- 戦略: [docs/documentation/strategy.md §2](../documentation/strategy.md)
