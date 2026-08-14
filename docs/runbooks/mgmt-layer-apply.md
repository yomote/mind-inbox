# 管理系レイヤ (rg-mgmt-mindbox) を適用する

## Trigger

**システムを運用するためのもの** (Key Vault + E2E trace 復号鍵 / バックアップ Storage / Log Analytics / 予算) を、アプリ系とは別の RG に作るとき。**一度きりの手動オペ**で、`provision.sh` にも `deploy.yml` にも入っていません ([ADR 0056](../adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D1 / [#302](https://github.com/yomote/mind-inbox/issues/302))。

移行後に `enableLogAnalytics` を切り替えて流し直すときも、同じ手順です。

> **Cosmos / OpenAI / Speech はここには来ません。** これらは「アプリそのもの」なのでアプリ系 RG (`rg-dev-mind-inbox`) に残します (2026-08-12 / 2026-08-14 の PO 裁定)。Cosmos のデータは RG を移して守るのではなく、**この RG の非公開 Storage へバックアップして戻せるようにします** (ADR 0056 D2 / 経路の実装は ADR 0046 D9)。

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

  権限を分けたい場合は、**空のまま適用して**あとから Crypto User を付ける運用にしてください (適用は Contributor、付与だけを User Access Administrator が行う)。`main-mgmt.parameters.json` の既定は空なので、通常はこちらになります。

- `az` (Azure CLI) と Bicep CLI。サンドボックスからは device-code で入る → [`claude-web-azure-access.md`](claude-web-azure-access.md)
- 宣言と値: [`cicd/iac/main-mgmt.bicep`](../../cicd/iac/main-mgmt.bicep) / [`cicd/iac/main-mgmt.parameters.json`](../../cicd/iac/main-mgmt.parameters.json)
- **`enable*` の既定値を確認してから流す** — 既定で作るのは「まだどこにも無いもの」だけ。理由は [`cicd/iac/README.md`](../../cicd/iac/README.md#1-5-管理系レイヤrg-mgmt-mindbox--一度きり)

## Steps

1. ログインとサブスクリプションの確認。

   ```bash
   az account show
   ```

2. 管理系の RG を作る (無ければ)。

   ```bash
   az group create -n rg-mgmt-mindbox -l japaneast
   ```

3. コンパイルを通す。

   ```bash
   cd cicd/iac
   az bicep build --file main-mgmt.bicep
   ```

4. **what-if で差分を見る。** ここで作られるものが想定どおりか確かめてから 5 に進む。

   ```bash
   az deployment group what-if \
     -g rg-mgmt-mindbox -n main-mgmt \
     -f main-mgmt.bicep -p @main-mgmt.parameters.json \
     -p budgetContactEmails='["<your-email@example.com>"]'
   ```

5. 適用する。**`budgetContactEmails` を初回に必ず渡すこと** — 下の理由で、渡さないと予算アラートが作られません。

   ```bash
   az deployment group create \
     -g rg-mgmt-mindbox -n main-mgmt \
     -f main-mgmt.bicep -p @main-mgmt.parameters.json \
     -p budgetContactEmails='["<your-email@example.com>"]'
   ```

   > ⚠️ **通知先メールは PII なので `main-mgmt.parameters.json` には commit しません** (`main-bootstrap` と同じ扱い / [`entra-spa-auth-and-budget.md`](entra-spa-auth-and-budget.md))。空のままだと `main-mgmt.bicep` の `enableBudgetAlert && !empty(budgetContactEmails)` により **budget リソース自体が作られません** (通知先の無いアラートは沈黙と同じなので、意図的にそうしてあります)。
   >
   > **RG を 1 つ増やすと、そこは既存のどの予算の射程にも入りません。** ここで渡し忘れると、管理系 RG のコスト (Key Vault / Storage / 有効化した場合の Log Analytics) が**どの予算にも載っていない**状態になります ([ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md))。アプリ系 RG の予算はアプリ系 RG に張ったままです。
   >
   > budget は作成後 ARM の incremental デプロイで残るので、**2 回目以降は省略しても消えません**。ただし省略した回の deployment output `budgetAlertEnabled` は `false` になります (output はその回に渡したパラメータの写しで、budget が実在するかとは無関係)。**確認には output ではなく実リソースを見てください** (Verification 5)。

6. E2E trace 復号鍵の URI (kid) を控える。`az keyvault key decrypt --id <kid>` にそのまま渡せます ([#301](https://github.com/yomote/mind-inbox/issues/301) / [裁定記録](../adr/archive/operations/e2e-artifacts-are-secret-by-default.md) D5)。

   ```bash
   az deployment group show -g rg-mgmt-mindbox -n main-mgmt \
     --query properties.outputs.e2eTraceKeyUri.value -o tsv
   ```

   **適用が済んだら [`cicd/keys/README.md`](../../cicd/keys/README.md) の「管理系 RG の適用後」の手順が有効になります** — この時点でエージェントも `az keyvault key decrypt` で trace を復号してよくなり、公開鍵ファイルを `e2e-artifacts.pub.json` (PEM + 鍵バージョン) に差し替える作業が始まります。

## Verification

実行後、次がすべて満たされていることを確認:

- [ ] デプロイが `Succeeded` (下の 1)
- [ ] **作ったリソース全部に層タグが付いている** (下の 2) — このタグが撤収ガードの判定入力なので、**付いていないものはアプリ系と見なされて撤収で消えます**
- [ ] 鍵が**エクスポート不可**で作られている (下の 3 が `false` を返すこと)
- [ ] **撤収ガードが管理系 RG を拒否する** (下の 4 が **2 回とも** exit 3 で、何も消えないこと) — 2 本目は `MGMT_RG` を別名に向けて `ALLOW_PROTECTED_DELETE=true` を足した場合で、**組み込みの保護が設定で外せない**ことを見ています
- [ ] **月次予算が実在する** (下の 5 が `budget-mgmt-mindbox` を 1 件返し、`contacts` が空でないこと) — 空 (`[]`) なら `budgetContactEmails` を渡し忘れており、**管理系 RG のコストがどの予算にも載っていません**

```bash
# 1. デプロイの結果
az deployment group show -g rg-mgmt-mindbox -n main-mgmt \
  --query properties.provisioningState -o tsv

# 2. 層タグ (layer 列が空のものは撤収ガードから見えていない)
az resource list -g rg-mgmt-mindbox \
  --query "[].{name:name,layer:tags.mindInboxLayer}" -o table

# 3. 鍵がエクスポート不可か
az keyvault key show --vault-name kv-dev-mindbox -n e2e-artifacts \
  --query key.exportable -o tsv

# 4. 撤収ガードが管理系 RG を拒否するか。**逃げ道が無いことまで見る** --
#    MGMT_RG を別名に向けて override を足しても exit 3 で何も消えないこと。
cd cicd && RG=rg-mgmt-mindbox ./scripts/env/cleanup-env.sh; echo "exit=$?"
cd cicd && RG=rg-mgmt-mindbox MGMT_RG=rg-somewhere-else ALLOW_PROTECTED_DELETE=true \
  ./scripts/env/cleanup-env.sh; echo "exit=$?"

# 5. 月次予算が「実在するか」。deployment output (budgetAlertEnabled) は見ない --
#    あれは最後に流したときのパラメータの写しで、2 回目以降 budgetContactEmails を
#    省略すると budget は残ったまま false になる (= 誤って「消えた」と読める)。
az consumption budget list -g rg-mgmt-mindbox \
  --query "[?name=='budget-mgmt-mindbox'].{name:name,amount:amount,contacts:notifications.actual50.contactEmails}" \
  -o json
```

`[]` が返ったら budget は**存在しません** (下の「予算アラートが来ない」を参照)。`az consumption` が使えないサブスクリプション / CLI バージョンでは ARM を直接叩きます (存在しなければ `BudgetNotFound` で落ちるので、**沈黙と区別できます**)。

```bash
az rest --method get --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-mgmt-mindbox/providers/Microsoft.Consumption/budgets/budget-mgmt-mindbox?api-version=2023-05-01" \
  --query "{name:name,amount:properties.amount,contacts:properties.notifications.actual50.contactEmails}" -o json
```

## 撤収ガードとの関係 (いま暫定なのはどこか)

`cleanup-env.sh` の判定 (`cicd/scripts/env/persistent_layer_guard.py`) が止めるものは 2 種類あり、**片方は恒久、片方は暫定**です。

| 判定コード                     | 何を止めるか                                    | 性質                                                      |
| ------------------------------ | ----------------------------------------------- | --------------------------------------------------------- |
| `target-is-management-rg`      | 管理系 RG そのものの削除                        | **恒久**。どのフラグでも通らない (`MGMT_RG` でも外せない) |
| `management-resources-present` | 層タグ / 名指しの管理系リソースが居る RG の撤収 | **恒久** (`ALLOW_PROTECTED_DELETE` は可)                  |
| `data-restore-unproven`        | Cosmos が居る RG の撤収                         | **暫定**                                                  |

**`data-restore-unproven` は「Cosmos がアプリ系に居るのが間違い」という意味ではありません。** アプリ系に居るのが正しい姿で、止めている理由は **バックアップからの復元をまだ 1 回も通していない**ことだけです ([ADR 0018](../adr/archive/operations/runtime-verification-in-the-loop.md) 「復元したことのないバックアップはバックアップではない」)。

### 実証が済んだら何をするか (やらないとガードが死ぬ)

1. Cosmos → 管理系 Storage へのエクスポートと、**空の Cosmos への復元**を 1 回通す (ADR 0046 D9)
2. `persistent_layer_guard.py` の `DATA_BEARING_RESOURCE_TYPES` による**一律拒否をやめ**、「直近のバックアップが十分に新しいか」を材料に取る判定へ差し替える
3. 「バックアップが古い / 取れていない」を**拒否側**に倒す (取れなかったものを「異常なし」と書かない)

**2 をやらずに 1 だけ済ませると、週次プロビジョンテスト (ADR 0046 D9 / 第 3 段階) が毎回 `ALLOW_PROTECTED_DELETE=true` を要求します。** 逃げ道が常用になった時点でこのガードは何も守りません。

## Rollback

**管理系は `cleanup-env.sh` では消せません** (どのフラグでも通りません)。適用をやり直したい場合:

1. 個別のリソースを消す (RG ごとではなく)。Key Vault は `enablePurgeProtection: true` なので、**soft-delete を purge できません** — 同名で作り直したいなら `recoverKeyVault=true` で復旧します。
2. 作り直しではなく値の変更で済むなら、parameters を直して 4 → 5 を流し直す (宣言なので冪等)。

## Common Issues

### `already exists in soft-deleted state` / `FlagMustBeSetForRestore` (Key Vault)

- 原因: 同名の Key Vault が soft-delete で残っている
- 対処: `main-mgmt.parameters.json` で `recoverKeyVault=true` にして流し直す

### `AuthorizationFailed` / `does not have authorization to perform action 'Microsoft.Authorization/roleAssignments/write'`

- 原因: `keyVaultCryptoUserPrincipalIds` を指定しているが、実行者が Contributor しか持っていない (Contributor はロール割り当てを作れない)
- 対処: Owner 単独か、**Contributor に User Access Administrator / RBAC Administrator を足して**流し直す。または `keyVaultCryptoUserPrincipalIds` を空にして適用し、Crypto User の付与だけを権限のある人に依頼する

### `AuthorizationFailed` が deployment 自体で出る (`Microsoft.Resources/deployments/write` など)

- 原因: 逆のパターン。**User Access Administrator / RBAC Administrator しか持っていない** — これらは**ロール割り当て専用**で、Key Vault や Storage、deployment を作る権限がない
- 対処: Contributor (または Owner) を足す。この 2 つは片方だけでは足りない (Prerequisites の表を参照)

### 予算アラートが来ない / Verification 5 が `[]` を返す

- 原因: `budgetContactEmails` が空のまま適用した。**通知先の無い予算はアラートとして無意味なので、budget リソース自体を作らない**設計 (`main-mgmt.bicep` の `enableBudgetAlert && !empty(budgetContactEmails)`)
- 対処: 手順 5 を `-p budgetContactEmails='["<your-email@example.com>"]'` 付きで流し直す (冪等)。**管理系 RG は既存のどの予算の射程にも入っていない**ので、放置するとこの RG のコストに歯止めがどこにも無くなります

### `budgetAlertEnabled` が `false` なのに budget は生きている

- 原因: **異常ではありません。** output は「その回のデプロイに `budgetContactEmails` を渡したか」を写しているだけで、budget リソースの実在とは無関係です。budget は ARM の incremental デプロイで残るため、2 回目以降に省略すると **budget は生きたまま output だけ `false`** になります
- 対処: 判断材料にしないでください。**実在の確認は Verification 5** (`az consumption budget list` / `az rest`) で行います。通知先を変えたいときだけ、新しい `budgetContactEmails` を付けて流し直します

### Log Analytics が二重になる

- 原因: `enableLogAnalytics=true` にしたが、アプリ系 (bootstrap) の workspace がまだ生きている
- 対処: 既定 `false` のままにする。移行は「アプリ系の宣言を落として管理系に寄せる」別作業 ([#302](https://github.com/yomote/mind-inbox/issues/302))

### 適用したのにアプリ系が管理系を見ていない

- 原因: アプリ系は RG をまたぐ resource 参照をせず、**output → parameter** で受け取る設計
- 対処: 管理系の output を bootstrap の parameters に渡す配線が要る (別作業 / [#302](https://github.com/yomote/mind-inbox/issues/302))

## Related

- ADR: [0056 管理系 / アプリ系とバックアップによるデータ保護](../adr/0056-management-and-app-layers-with-backup-based-data-protection.md) (**層の定義の正典**。Proposed — Status を動かすのは PO) / [0046 環境は宣言から再構築できる](../adr/0046-environment-rebuildable-from-declaration.md) D6/D9 (D1 は 0056 が Accept され次第 supersede) / [E2E artifact は既定で秘密 (2026-08-12 の裁定記録)](../adr/archive/operations/e2e-artifacts-are-secret-by-default.md) D5 (**ADR ではありません** — #385 で運用文書へ退避。鍵の運用手順の正典は [`cicd/keys/README.md`](../../cicd/keys/README.md)) / [0003 2 フェーズ Bicep](../adr/0003-two-phase-bicep.md)
- 関連 Runbook: [`claude-web-azure-access.md`](claude-web-azure-access.md) / [`cosmos-persistence.md`](cosmos-persistence.md)
- 宣言とパラメータの説明: [`cicd/iac/README.md`](../../cicd/iac/README.md#1-5-管理系レイヤrg-mgmt-mindbox--一度きり)
- 撤収ガード: `cicd/scripts/env/` ([README](../../cicd/scripts/env/README.md))
