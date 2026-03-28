# Backend (Azure Functions / Python)

最小構成の Azure Functions (Python) プロジェクトです。  
`GET /api/health` でヘルスチェックを返し、Swagger UI で API ドキュメントを確認できます。

## セットアップ

1. Python 仮想環境を作成
2. 依存関係をインストール
3. Azure Functions Core Tools で起動

例:

- `python -m venv .venv`
- `source .venv/bin/activate`
- `pip install -r requirements.txt`
- `func start`

## API

- `GET /api/health`
- レスポンス例:
  - `{ "status": "ok", "service": "mind-inbox-backend", "timestamp": "..." }`

## Swagger / OpenAPI

- Swagger UI: `GET /api/docs`
- OpenAPI JSON: `GET /api/swagger.json`

各関数の OpenAPI 定義は、ハンドラーの近くで宣言して登録する構成です。

- `*_SCHEMA` と `*_OPERATION` を関数近くに置く
- `@register_openapi(...)` を `@app.route(...)` と一緒に付ける

これで関数追加時に、中央の `paths` を手で増やす必要はありません。
