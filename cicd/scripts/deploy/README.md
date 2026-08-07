# Deploy scripts (manual)

IaC はリソース作成まで（SWA/Functions/SQL…）。
このフォルダは **フロント/バックエンド成果物を手動でデプロイ**するためのスクリプトです。

## 共通前提

- `az` (Azure CLI) でログイン済み
- リソースグループ `RG` と、IaC のデプロイ名 `DEPLOYMENT` が分かる（通常 `main-bootstrap`）

デフォルト:

- `RG=rg-dev-mind-inbox`
- `DEPLOYMENT=main-bootstrap`

## Frontend (SWA)

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-frontend.sh
```

- SWA の deployment token は `az staticwebapp secrets list` から取得し、`swa deploy` に渡します。
- SWA Free は静的ファイルの匿名配信のみ (#69 / ADR 0013)。認可の門は Functions EasyAuth 側にあり、
  SWA の app settings に認証情報は置きません（SWA Free はカスタム認証非対応）。
- 認証まわりのビルド時変数 `VITE_BFF_BASE_URL` / `VITE_ENTRA_CLIENT_ID` / `VITE_ENTRA_TENANT_ID` は
  deployment outputs から自動解決されます（env で明示指定して上書きも可）。
  Entra の値が解決できない場合は「認証無効ビルド」を警告付きで出します。
  詳細は runbook [`entra-spa-auth-and-budget.md`](../../../docs/runbooks/entra-spa-auth-and-budget.md)。

## Backend (Azure Functions)

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-backend.sh
```

- `backend/` をビルドし、production dependencies のみ残した zip を作成して
  `az functionapp deployment source config-zip` で反映します。

## All

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-all.sh
```

`deploy-all.sh` は成果物デプロイ専用です（IaC は実行しません）。
Entra 認証の有効化/更新は、先に `main-config.bicep` デプロイを実行してください。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

- `main-config` / `main-bootstrap` の outputs から、自動作成した Entra アプリ登録を検出できた場合は先に削除します。
- 既存の手動管理 Entra アプリを残したい場合は `DELETE_ENTRA_APP=false` を付けてください。
