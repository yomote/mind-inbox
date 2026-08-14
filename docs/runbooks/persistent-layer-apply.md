# 持続層 (rg-shared-mindbox) を適用する

## Trigger

環境の撤収で消してはいけないもの (Key Vault + E2E trace 復号鍵 / バックアップ Storage / Cosmos / OpenAI / Speech / Log Analytics) を、環境層とは別の RG に作るとき。**一度きりの手動オペ**で、`provision.sh` にも `deploy.yml` にも入っていません ([ADR 0046](../adr/0046-environment-rebuildable-from-declaration.md) D1 / [#302](https://github.com/yomote/mind-inbox/issues/302))。

移行後に `enable*` を切り替えて流し直すときも、同じ手順です。

## Prerequisites

- **必要なロールは `keyVaultCryptoUserPrincipalIds` を指定するかで変わります。**

  | `keyVaultCryptoUserPrincipalIds` | 必要なロール                                                                                       | なぜ                                                                                                                                     |
  | -------------------------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
  | 空 (既定)                        | **Contributor**                                                                                    | リソースを作るだけ                                                                                                                       |
  | 1 つ以上を指定                   | **Owner** / **User Access Administrator** / **Role Based Access Control Administrator** のいずれか | `Microsoft.Authorization/roleAssignments` を作るため。**組み込みの Contributor にはロール割り当ての作成権限が無く、手順 5 が失敗します** |

  空のまま適用して**あとから Crypto User を付ける**運用も可能です (その付与だけを権限のある人がやる)。

- `az` (Azure CLI) と Bicep CLI。サンドボックスからは device-code で入る → [`claude-web-azure-access.md`](claude-web-azure-access.md)
- 宣言と値: [`cicd/iac/main-shared.bicep`](../../cicd/iac/main-shared.bicep) / [`cicd/iac/main-shared.parameters.json`](../../cicd/iac/main-shared.parameters.json)
- **`enable*` の既定値を確認してから流す** — 既定で作るのは「まだどこにも無いもの」だけ。理由は [`cicd/iac/README.md`](../../cicd/iac/README.md#1-5-持続層rg-shared-mindbox--一度きり)

## Steps

1. ログインとサブスクリプションの確認。

   ```bash
   az account show
   ```

2. 持続層の RG を作る (無ければ)。

   ```bash
   az group create -n rg-shared-mindbox -l japaneast
   ```

3. コンパイルを通す。

   ```bash
   cd cicd/iac
   az bicep build --file main-shared.bicep
   ```

4. **what-if で差分を見る。** ここで作られるものが想定どおりか確かめてから 5 に進む。

   ```bash
   az deployment group what-if \
     -g rg-shared-mindbox -n main-shared \
     -f main-shared.bicep -p @main-shared.parameters.json
   ```

5. 適用する。

   ```bash
   az deployment group create \
     -g rg-shared-mindbox -n main-shared \
     -f main-shared.bicep -p @main-shared.parameters.json
   ```

6. E2E trace 復号鍵の URI (kid) を控える。`az keyvault key decrypt --id <kid>` にそのまま渡せます ([#301](https://github.com/yomote/mind-inbox/issues/301) / ADR 0045 D5 — **本文は未マージ** (PR #332) なのでリンクは張っていません)。

   ```bash
   az deployment group show -g rg-shared-mindbox -n main-shared \
     --query properties.outputs.e2eTraceKeyUri.value -o tsv
   ```

## Verification

実行後、次がすべて満たされていることを確認:

- [ ] デプロイが `Succeeded` (下の 1)
- [ ] **作ったリソース全部に層タグが付いている** (下の 2) — このタグが撤収ガードの判定入力なので、**付いていないものは環境層と見なされて撤収で消えます**
- [ ] 鍵が**エクスポート不可**で作られている (下の 3 が `false` を返すこと)
- [ ] **撤収ガードが持続層 RG を拒否する** (下の 4 が exit 3 で、何も消えないこと)

```bash
# 1. デプロイの結果
az deployment group show -g rg-shared-mindbox -n main-shared \
  --query properties.provisioningState -o tsv

# 2. 層タグ (layer 列が空のものは撤収ガードから見えていない)
az resource list -g rg-shared-mindbox \
  --query "[].{name:name,layer:tags.mindInboxLayer}" -o table

# 3. 鍵がエクスポート不可か
az keyvault key show --vault-name kv-dev-mindbox -n e2e-artifacts \
  --query key.exportable -o tsv

# 4. 撤収ガードが持続層 RG を拒否するか
cd cicd && RG=rg-shared-mindbox ./scripts/env/cleanup-env.sh; echo "exit=$?"
```

## Rollback

**持続層は `cleanup-env.sh` では消せません** (どのフラグでも通りません)。適用をやり直したい場合:

1. 個別のリソースを消す (RG ごとではなく)。Key Vault は `enablePurgeProtection: true` なので、**soft-delete を purge できません** — 同名で作り直したいなら `recoverKeyVault=true` で復旧します。
2. 作り直しではなく値の変更で済むなら、parameters を直して 4 → 5 を流し直す (宣言なので冪等)。

## Common Issues

### `already exists in soft-deleted state` / `FlagMustBeSetForRestore` (Key Vault)

- 原因: 同名の Key Vault が soft-delete で残っている
- 対処: `main-shared.parameters.json` で `recoverKeyVault=true` にして流し直す

### `AuthorizationFailed` / `does not have authorization to perform action 'Microsoft.Authorization/roleAssignments/write'`

- 原因: `keyVaultCryptoUserPrincipalIds` を指定しているが、実行者が Contributor しか持っていない (Contributor はロール割り当てを作れない)
- 対処: Owner / User Access Administrator / RBAC Administrator のいずれかで流し直すか、`keyVaultCryptoUserPrincipalIds` を空にして適用し、Crypto User の付与だけを権限のある人に依頼する

### Cosmos の無料枠 / Speech F0 でデプロイが落ちる

- 原因: **1 サブスクに 1 つ**しか取れない枠を、環境層の既存アカウントが持っている
- 対処: 落ちるのが正しい ([ADR 0030](../adr/0030-persistence-on-cosmos-db-single-store-behind-bff.md) D2「デプロイ成功 = 無料枠取得成功」)。移行が済むまで `enableCosmos` / `enableSpeech` は `false` のままにする

### 適用したのに環境層が持続層を見ていない

- 原因: 環境層は RG をまたぐ resource 参照をせず、**output → parameter** で受け取る設計
- 対処: 持続層の output を bootstrap の parameters に渡す配線が要る (別作業 / [#302](https://github.com/yomote/mind-inbox/issues/302))

## Related

- ADR: [0046 環境は宣言から再構築できる](../adr/0046-environment-rebuildable-from-declaration.md) D1/D6/D9 / ADR 0045 D5 (E2E artifact は既定で秘密 — 未マージ / PR #332) / [0003 2 フェーズ Bicep](../adr/0003-two-phase-bicep.md)
- 関連 Runbook: [`claude-web-azure-access.md`](claude-web-azure-access.md) / [`cosmos-persistence.md`](cosmos-persistence.md)
- 宣言とパラメータの説明: [`cicd/iac/README.md`](../../cicd/iac/README.md#1-5-持続層rg-shared-mindbox--一度きり)
- 撤収ガード: `cicd/scripts/env/` ([README](../../cicd/scripts/env/README.md))
