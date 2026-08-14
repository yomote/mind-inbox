# CLAUDE.md — frontend

**このファイルは `apps/frontend/` を触るときだけ読まれる。全セッション共通の規約は root の [`CLAUDE.md`](../../CLAUDE.md)。**

React 19 + Vite + MUI + tRPC client。起動・テストの手順は `dev` skill。

## パッケージマネージャは pnpm (npm ではない)

正典は `pnpm-lock.yaml`。**`npm install` を打たない** (別 lock が生まれてビルドが割れる)。

```bash
pnpm --dir apps/frontend install
pnpm --dir apps/frontend test        # vitest
pnpm --dir apps/frontend lint
pnpm --dir apps/frontend build       # tsc -b && vite build
```

リポジトリ root からは `npm run test:frontend` / `npm run lint:frontend` が同じものへ委譲する。

## `mockApi.ts` は mock 兼テスト fixture

`src/mockApi.ts` は 2 つの役割を 1 本で持つ ([ADR 0004](../../docs/adr/0004-mockapi-as-frontend-truth.md)):

1. `VITE_USE_MOCK=true` のデモビルド (BFF も認証も課金リソースも要らない自己完結モード)
2. フロントのテストが使う fixture

**テストごとに別 mock を増やさない。** 新しいデータ形が要るなら `mockApi.ts` を拡張する — ローカルに mock を生やすと「デモでは動くがテストは別物を見ている」状態になり、片方だけ壊れても気づけない。

`VITE_USE_MOCK` の分岐は `src/api/*.ts` に集約されている。画面コンポーネント側で `import.meta.env` を直接読んで分岐を増やさない。

> mock モードと BFF の **stub fallback は別物**。stub は BFF が外部サービス未設定のときに返す応答 (`src/api/stubStatus.ts`)。mock は BFF を呼ばない。混ぜない。

## UI 仕様は MDX が真実

`docs/frontend/ui_specs/*.mdx` が UI 仕様の正典 ([ADR 0005](../../docs/adr/0005-mdx-ui-spec-as-truth.md))。

- **実装と MDX が食い違ったら、直すのは実装**。仕様を実装に合わせて書き換えない。
- 仕様そのものを変えるなら MDX を先に更新し、その差分を根拠に実装する。
- 画面を足す / 動線を変える PR は、対応する MDX の更新を同じ PR に含める。

## E2E の置き場所

| ディレクトリ | 何を見るか                             | 方針                                 |
| ------------ | -------------------------------------- | ------------------------------------ |
| `e2e/`       | mock ビルドに対する Playwright (旧 L3) | **廃止方針。新規シナリオを足さない** |
| `e2e-uc/`    | 実配線に対するユースケース・異常系     | スモーク層。ここに足す               |
| `e2e-live/`  | 実環境に対する通し                     | E2E 層                               |

判断の正典は [テスト戦略](../../docs/testing/strategy.md) (§6.3)。新規テストには `[契約]` / `[単体]` / `[スモーク]` / `[E2E]` のプレフィックスを付ける。

## 型は tRPC が真実

BFF の zod スキーマから型が流れてくる ([ADR 0001](../../docs/adr/0001-bff-as-trpc-not-rest.md))。フロント側で API のレスポンス型を手書きし直さない — 手書きすると BFF の変更に気づけないまま `any` 相当で通る。
