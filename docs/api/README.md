# API ドキュメント

> このディレクトリの YAML はすべて **CI で自動生成された OpenAPI**。
> **手書き編集禁止**。
> 戦略全体: [`docs/documentation/strategy.md`](../documentation/strategy.md)

## ファイル構成

| ファイル        | 真実の所在                                     | 生成方法                             | 関連 issue                                          |
| --------------- | ---------------------------------------------- | ------------------------------------ | --------------------------------------------------- |
| `bff-trpc.yaml` | `apps/bff/src/trpc/router.ts` の zod schema    | `trpc-to-openapi` 等で router → YAML | [#8](https://github.com/yomote/mind-inbox/issues/8) |
| `ai-agent.yaml` | `apps/services/ai-agent/app/main.py` (FastAPI) | `app.openapi()` を YAML 化           | [#9](https://github.com/yomote/mind-inbox/issues/9) |
| `voicevox.yaml` | `apps/services/voicevox/app/main.py` (FastAPI) | 同上                                 | [#9](https://github.com/yomote/mind-inbox/issues/9) |

## 更新フロー

1. 該当する router / FastAPI コードを変更
2. ローカルで再生成 (例: `npm run docs:openapi:bff` / `make docs-openapi-python`)
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
