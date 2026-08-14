# CLAUDE.md — cicd

**このファイルは `cicd/` を触るときだけ読まれる。全セッション共通の規約は root の [`CLAUDE.md`](../CLAUDE.md)。**

IaC (Bicep) / デプロイスクリプト / 運用スクリプト。運用手順の正典は [`docs/runbooks/`](../docs/runbooks/README.md)。

## Two-Phase IaC (Bicep)

[ADR 0003](../docs/adr/0003-two-phase-bicep.md)。順序を入れ替えない (config は bootstrap の出力に依存する)。

0. **mgmt (管理系)** — `cicd/iac/main-mgmt.bicep`: **別 RG (`rg-mgmt-mindbox`) に一度きり**。Key Vault (E2E trace の非エクスポート鍵) / バックアップ Storage / Log Analytics / 予算 ([ADR 0046](../docs/adr/0046-environment-rebuildable-from-declaration.md) D1)
1. **bootstrap** — `cicd/iac/main-bootstrap.bicep`: SWA / Function App / Cosmos / OpenAI / Speech / Container App environment を作る (SQL 一式は `enableSql=true` のときだけ。ACR は無い)
2. **config** — `cicd/iac/main-config.bicep`: Entra ID 認証とシークレットを配線する (bootstrap の後に流す)

**層の軸は「消えると困るか」ではなく「運用のためか / アプリそのものか」** — Cosmos / OpenAI / Speech は**アプリ系に残す**。Cosmos のデータは RG を移して守るのではなく、管理系 RG の非公開 Storage へバックアップして戻せるようにする (ADR 0046 D9。**public リポジトリなので git データブランチには出さない**)。

**管理系とアプリ系は RG をまたぐ resource 参照をしない** — 管理系の output をアプリ系の parameter に渡す。**撤収 (`cleanup-env.sh`) の対象はアプリ系だけ**で、管理系 RG は削除できない (判定は `cicd/scripts/env/persistent_layer_guard.py`)。

**管理系のリソースには層タグ `mindInboxLayer=management` を刻む** — Key Vault / Storage / Log Analytics はアプリ系にも同じ型が居るので、撤収ガードは**型ではなくこのタグ**で層を見分ける。`main-mgmt.bicep` にリソースを足したら `tags: managementLayerTags` も付ける (付け忘れると撤収で黙って消える)。

**Cosmos が居る RG の撤収は当面拒否される** (`data-restore-unproven`)。置き場所の誤りではなく、**復元を 1 回も通していない**ための暫定措置 (ADR 0018)。復元を通したら、この一律拒否をバックアップ鮮度の確認に差し替える — **差し替えないと週次プロビジョンテストが毎回 override を要求し、逃げ道が常用になってガードが死ぬ** ([Runbook](../docs/runbooks/mgmt-layer-apply.md))。

**リソース命名**: `{resourcetype}-{env}-{appname}` — 例 `func-dev-mindbox` / `swa-dev-mindbox`。環境は `dev` / `stg` / `prod`、既定の appName は `mind-box`。

## デプロイスクリプト

```bash
cicd/scripts/deploy/deploy-all.sh              # Frontend + BFF
cicd/scripts/deploy/deploy-frontend.sh         # SWA へ静的配信 (VITE_* をビルド時に焼き込む)
cicd/scripts/deploy/deploy-backend.sh          # BFF を Functions へ zip deploy
cicd/scripts/deploy/deploy-ai-agent.sh         # ghcr の事前ビルド image を Container App に差し替え
cicd/scripts/deploy/deploy-voicevox-wrapper.sh # 同上 (VOICEVOX wrapper)
cicd/scripts/smoke-test/smoke-test.sh          # デプロイ後の疎通確認
```

## コストと公開面で覆さない前提

「知らずに壊すと待機課金が乗る / 公開面が開く」もの。覆すなら該当 ADR を読んでから。

- **SWA は Free、フロントは Functions を直叩き** — SWA の linked backend は使わない (Standard 課金になる)。認可は Functions EasyAuth の 401 が担う ([ADR 0013](../docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [Runbook](../docs/runbooks/entra-spa-auth-and-budget.md))
- **image は ghcr に事前ビルド、デプロイは不変 sha タグの差し替え** — ACR は作らない。**`:latest` を使わない** (何が動いているか特定できなくなる) ([ADR 0025](../docs/adr/0025-deploy-container-images-by-immutable-sha-tag.md) / [Runbook](../docs/runbooks/ghcr-images.md))
- **Container Apps は scale-to-zero** — AKS に戻さない ([ADR 0002](../docs/adr/0002-container-apps-not-aks.md))
- **Container Apps は組み込み認証で閉じる** — IP 許可リスト方式に戻さない ([ADR 0017](../docs/adr/0017-container-apps-access-via-auth-gate.md))
- **SQL は既定で作らない** (`enableSql=false`) — 有効化すると VNet + Private Endpoint 一式が付き、待機課金が常時乗る。永続化は Cosmos ([ADR 0030](../docs/adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md))

## スクリプトを足すとき

- **`cicd/scripts/` 配下の Python テストは root の `npm run test:scripts` に登録する。** 登録しないと CI で走らない (`package.json` の `test:scripts` にディレクトリを 1 つ足す)。
- **判定ロジックをシェルや workflow の中に埋めない** — 純粋関数に切り出して pytest で押さえる。`if` が YAML の中にあるとテストできない。
- **握り潰しを足すときは、それで何が見えなくなるかをコメントに書く** (`2>/dev/null` / `|| true` / 空の `catch`)。取得や検証に失敗したものを「異常なし」として出さない — 成功と区別できる形 (`未検証: 理由` / status を error / run を落とす) にする。
- **自動化を足したら [`cicd/scripts/status-page/watchers.json`](scripts/status-page/watchers.json) に 1 行足す。** 足せない自動化は作らない。新設の必須条件は「動いたら痕跡がリポジトリに残ること」— 異常時だけ喋る設計にすると、沈黙と正常が区別できなくなる ([Runbook](../docs/runbooks/status-page.md))。
