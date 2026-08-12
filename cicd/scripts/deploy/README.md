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

## Container Apps (ai-agent / voicevox-wrapper)

```bash
cd cicd
# IMAGE_TAG は sha-<full-sha> を明示指定すること (:latest は no-op になる, #107)
RG=<your-rg> DEPLOYMENT=<deployment-name> IMAGE_TAG=sha-<full-sha> ./scripts/deploy/deploy-ai-agent.sh
RG=<your-rg> DEPLOYMENT=<deployment-name> IMAGE_TAG=sha-<full-sha> ./scripts/deploy/deploy-voicevox-wrapper.sh
```

タグの決め方・ロールバック・据え置きの確認手順は runbook
[`ghcr-images.md`](../../../docs/runbooks/ghcr-images.md) が真実です。

## All

```bash
cd cicd
RG=<your-rg> DEPLOYMENT=<deployment-name> ./scripts/deploy/deploy-all.sh
```

`deploy-all.sh` は成果物デプロイ専用です（IaC は実行しません）。
Entra 認証の有効化/更新は、先に `main-config.bicep` デプロイを実行してください。

## ロール割り当ての持ち主は bicep 1 本 (#261 / #297)

**ロール割り当ては bicep が宣言する。シェルからは作らない。**
`az role assignment create` をデプロイスクリプトに書かないこと — 二重宣言が
`RoleAssignmentExists` を招く。

理由: 割り当ての一意性は**名前ではなく `principal + role + scope`** で決まる。
シェルが名前を指定せずランダム GUID 名で作ると、bicep が自分の `guid()` 名で
同じ組み合わせを宣言した瞬間に ARM が `RoleAssignmentExists` を返し、
**bootstrap ごと落ちる**（= dev が古いまま止まる / #262）。

- 宣言の場所: [`cicd/modules/bootstrap-core.bicep`](../../modules/bootstrap-core.bicep) の
  `aiAgentOpenAiRoleAssignment`（ai-agent MI → Cognitive Services OpenAI User）
- 名前: `guid(...)` の決定的計算。実行時にパラメータで名前を渡す仕掛けは持たない
  （既存名を渡し続ける「養子縁組」は #278 で入れたが、**渡し損ねた瞬間に再発する**
  恒久的な依存になるため #297 で撤去した）
- `deploy-ai-agent.sh` は MI が付いていることを**確認するだけ**（付与はしない）

### 前提条件: 古い手動割り当てが残っていないこと (#297)

スクリプト時代に作られた割り当てが残っている環境では、bicep の宣言が
`RoleAssignmentExists` で拒否される。**初回に 1 回だけ人手で削除**する
（削除には Owner 相当の権限が要るため CD からは実行しない）:

```bash
# 対象を確認（ai-agent の MI principalId で絞る）
az role assignment list --scope "$(az cognitiveservices account show \
  -g <rg> -n oai-<env>-<app> --query id -o tsv)" -o table

az role assignment delete --ids <対象の割り当て ID>
```

削除後は bicep が `guid()` の決定的な名前で作り直し、以後は宣言と実体が常に一致する。

`provision.sh` は失敗ログに `RoleAssignmentExists` を見つけると、この削除手順を
名指しで出す（`::error::RoleAssignmentExists — ...`）。その文言が出ていない失敗は
**別の原因**を疑うこと。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

- `main-config` / `main-bootstrap` の outputs から、自動作成した Entra アプリ登録を検出できた場合は先に削除します。
- 既存の手動管理 Entra アプリを残したい場合は `DELETE_ENTRA_APP=false` を付けてください。
