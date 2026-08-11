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

## ロール割り当ての「養子縁組」(provision.sh / #262)

`provision.sh` の bootstrap は、ai-agent MI → Cognitive Services OpenAI User の
ロール割り当てを **既存のものに合わせて宣言し直す**。理由は、割り当ての一意性が
名前ではなく `principal + role + scope` で決まるため。スクリプト時代に作られた
ランダム GUID 名の割り当てが残っている環境で bicep が別名で同じ組み合わせを宣言すると、
ARM が `RoleAssignmentExists` を返して **bootstrap ごと落ちる**（= dev が古いまま止まる）。

解決は 2 段構え。**1 段目が空振りしても止まらない**ことが要点:

1. `az role assignment list --scope <OpenAI アカウント>` の結果から、
   `principal / role / scope` が一致する既存名を選ぶ。判定は
   [`role_assignment.py`](role_assignment.py) の純粋関数（テスト済み・**大文字小文字は無視**。
   ARM が返す scope の綴りは揺れるため素の `==` は当てにならない）
2. それでも `RoleAssignmentExists` で落ちたら、ARM のエラー本文が返す既存 ID を
   ダッシュ付き GUID に直して **1 度だけデプロイをやり直す**

ログには必ずどちらを通ったかが出る（`==> 既存ロール割り当てを養子縁組: ...` /
`==> RoleAssignmentExists — ARM が返した既存 ID ... でやり直します`）。
どちらも出ずに落ちている場合は、養子縁組ではなく**別の原因**を疑うこと。

削除→再作成で名前を揃える案は採らない: 削除に Owner 相当の権限が要る上、
剥奪〜再付与の間 ai-agent が OpenAI を呼べない瞬断が出る。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

- `main-config` / `main-bootstrap` の outputs から、自動作成した Entra アプリ登録を検出できた場合は先に削除します。
- 既存の手動管理 Entra アプリを残したい場合は `DELETE_ENTRA_APP=false` を付けてください。
