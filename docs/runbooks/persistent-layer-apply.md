# 持続層 (rg-shared-mindbox) を適用する

## Trigger

環境の撤収で消してはいけないもの (Key Vault + E2E trace 復号鍵 / バックアップ Storage / Cosmos / OpenAI / Speech / Log Analytics) を、環境層とは別の RG に作るとき。**一度きりの手動オペ**で、`provision.sh` にも `deploy.yml` にも入っていません ([ADR 0046](../adr/0046-environment-rebuildable-from-declaration.md) D1 / [#302](https://github.com/yomote/mind-inbox/issues/302))。

移行後に `enable*` を切り替えて流し直すときも、同じ手順です。

## Prerequisites

- **必要なロールは `keyVaultCryptoUserPrincipalIds` を指定するかで変わります。**

  | `keyVaultCryptoUserPrincipalIds` | 必要なロール                                                                                                                    |
  | -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
  | 空 (既定)                        | **Contributor**                                                                                                                 |
  | 1 つ以上を指定                   | **Owner** 単独、または **Contributor + (User Access Administrator / Role Based Access Control Administrator)** の**組み合わせ** |

  **2 つの権限が要るのがポイントです。**

  | 要る権限                                         | 何に使うか                                         | 持っているロール                                               |
  | ------------------------------------------------ | -------------------------------------------------- | -------------------------------------------------------------- |
  | リソースと deployment の作成                     | Key Vault / Storage / `az deployment group create` | Owner / **Contributor**                                        |
  | `Microsoft.Authorization/roleAssignments` の作成 | Crypto User のロール割り当て                       | Owner / **User Access Administrator** / **RBAC Administrator** |

  **どちらか片方だけでは手順 5 が落ちます。** Contributor だけならロール割り当てで、User Access Administrator / RBAC Administrator だけならリソース作成 (deployment 自体) で失敗します。

  権限を分けたい場合は、**空のまま適用して**あとから Crypto User を付ける運用にしてください (適用は Contributor、付与だけを User Access Administrator が行う)。`main-shared.parameters.json` の既定は空なので、通常はこちらになります。

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
     -f main-shared.bicep -p @main-shared.parameters.json \
     -p budgetContactEmails='["<your-email@example.com>"]'
   ```

5. 適用する。**`budgetContactEmails` を初回に必ず渡すこと** — 下の理由で、渡さないと予算アラートが作られません。

   ```bash
   az deployment group create \
     -g rg-shared-mindbox -n main-shared \
     -f main-shared.bicep -p @main-shared.parameters.json \
     -p budgetContactEmails='["<your-email@example.com>"]'
   ```

   > ⚠️ **通知先メールは PII なので `main-shared.parameters.json` には commit しません** (`main-bootstrap` と同じ扱い / [`entra-spa-auth-and-budget.md`](entra-spa-auth-and-budget.md))。空のままだと `main-shared.bicep` の `enableBudgetAlert && !empty(budgetContactEmails)` により **budget リソース自体が作られません** (通知先の無いアラートは沈黙と同じなので、意図的にそうしてあります)。
   >
   > **持続層を別 RG に出すと、環境層 RG に張ってある予算の射程から Cosmos / OpenAI が外れます。** ここで渡し忘れると、あとで `enableCosmos` / `enableOpenAi` を `true` にした時点で**どちらの RG の予算にも載っていない**状態になります ([ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md))。
   >
   > budget は作成後 ARM の incremental デプロイで残るので、**2 回目以降は省略しても消えません**。ただし省略した回の deployment output `budgetAlertEnabled` は `false` になります (output はその回に渡したパラメータの写しで、budget が実在するかとは無関係)。**確認には output ではなく実リソースを見てください** (Verification 5)。

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
- [ ] **月次予算が実在する** (下の 5 が `budget-shared-mindbox` を 1 件返し、`contacts` が空でないこと) — 空 (`[]`) なら `budgetContactEmails` を渡し忘れており、**持続層のコストがどの予算にも載っていません**

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

# 5. 月次予算が「実在するか」。deployment output (budgetAlertEnabled) は見ない --
#    あれは最後に流したときのパラメータの写しで、2 回目以降 budgetContactEmails を
#    省略すると budget は残ったまま false になる (= 誤って「消えた」と読める)。
az consumption budget list -g rg-shared-mindbox \
  --query "[?name=='budget-shared-mindbox'].{name:name,amount:amount,contacts:notifications.actual50.contactEmails}" \
  -o json
```

`[]` が返ったら budget は**存在しません** (下の「予算アラートが来ない」を参照)。`az consumption` が使えないサブスクリプション / CLI バージョンでは ARM を直接叩きます (存在しなければ `BudgetNotFound` で落ちるので、**沈黙と区別できます**)。

```bash
az rest --method get --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-shared-mindbox/providers/Microsoft.Consumption/budgets/budget-shared-mindbox?api-version=2023-05-01" \
  --query "{name:name,amount:properties.amount,contacts:properties.notifications.actual50.contactEmails}" -o json
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
- 対処: Owner 単独か、**Contributor に User Access Administrator / RBAC Administrator を足して**流し直す。または `keyVaultCryptoUserPrincipalIds` を空にして適用し、Crypto User の付与だけを権限のある人に依頼する

### `AuthorizationFailed` が deployment 自体で出る (`Microsoft.Resources/deployments/write` など)

- 原因: 逆のパターン。**User Access Administrator / RBAC Administrator しか持っていない** — これらは**ロール割り当て専用**で、Key Vault や Storage、deployment を作る権限がない
- 対処: Contributor (または Owner) を足す。この 2 つは片方だけでは足りない (Prerequisites の表を参照)

### 予算アラートが来ない / Verification 5 が `[]` を返す

- 原因: `budgetContactEmails` が空のまま適用した。**通知先の無い予算はアラートとして無意味なので、budget リソース自体を作らない**設計 (`main-shared.bicep` の `enableBudgetAlert && !empty(budgetContactEmails)`)
- 対処: 手順 5 を `-p budgetContactEmails='["<your-email@example.com>"]'` 付きで流し直す (冪等)。**`enableCosmos` / `enableOpenAi` を `true` にする前に必ず直すこと** — 持続層は環境層 RG の予算の射程外なので、放置するとコストの歯止めがどこにも無くなります

### `budgetAlertEnabled` が `false` なのに budget は生きている

- 原因: **異常ではありません。** output は「その回のデプロイに `budgetContactEmails` を渡したか」を写しているだけで、budget リソースの実在とは無関係です。budget は ARM の incremental デプロイで残るため、2 回目以降に省略すると **budget は生きたまま output だけ `false`** になります
- 対処: 判断材料にしないでください。**実在の確認は Verification 5** (`az consumption budget list` / `az rest`) で行います。通知先を変えたいときだけ、新しい `budgetContactEmails` を付けて流し直します

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
