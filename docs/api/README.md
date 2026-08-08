# API ドキュメント

> このディレクトリの YAML はすべて **CI で自動生成された OpenAPI**。
> **手書き編集禁止**。
> 戦略全体: [`docs/documentation/strategy.md`](../documentation/strategy.md)

## ファイル構成

| ファイル        | 真実の所在                                  | 生成方法                                          | 関連 issue                                          |
| --------------- | ------------------------------------------- | ------------------------------------------------- | --------------------------------------------------- |
| `bff-trpc.yaml` | `apps/bff/src/trpc/router.ts` の zod schema | `npm run docs:openapi:bff` (router introspection) | [#8](https://github.com/yomote/mind-inbox/issues/8) |

FastAPI 2 サービス (ai-agent / voicevox) の OpenAPI 生成は**未整備** ([#9](https://github.com/yomote/mind-inbox/issues/9) 未完)。
`ai-agent.yaml` / `voicevox.yaml` はまだ存在せず、CI ゲートも無い。真実は各サービスの pydantic 実装
(ai-agent は `app/schemas.py`) にある。#9 を実装するまでこの README に載せない。

### `bff-trpc.yaml` の生成方式

`apps/bff/scripts/generate-openapi.mjs` が tRPC router を introspection し、各 procedure の
`.input()` / `.output()` zod schema を `zod-to-json-schema` で OpenAPI 化する。
`trpc-to-openapi` は使わない — BFF は単一 tRPC エントリポイントで REST を公開しないため
([ADR 0001](../adr/0001-bff-as-trpc-not-rest.md))、各 procedure を `1 procedure = 1 operation`
として `/api/trpc/{path}` に素直にマップする。レスポンス仕様を保つため router の各 procedure には
`.output()` を付与している (出力 schema の真実も router に集約)。

## 更新フロー

1. 該当する router のコードを変更
2. ローカルで再生成 (`npm run docs:openapi:bff`)
3. commit に含める
4. CI が再度生成し `git diff --exit-code` で乖離をチェック

## なぜ生成物を commit するか

- PR レビューで API 変更を**強制的に可視化**するため
- ブランチを切り替えた時にすぐ最新仕様を読めるため
- 外部ツール (Stoplight / Swagger UI / Postman) で eternally に参照可能にするため

## 手書きしてはいけない

- 手で直しても次の生成で上書きされる
- 真実は実装側にある (BFF: zod / FastAPI: 実装)
- 編集したい場合は **真実を直す**
