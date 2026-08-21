# 管理系レイヤ (rg-mgmt-mindbox) を適用する

## Trigger

**システムを運用するためのもの** (Key Vault + E2E trace 復号鍵 / バックアップ Storage / Log Analytics / 予算) を、アプリ系とは別の RG に作るとき。**一度きりの手動オペ**で、`provision.sh` にも `deploy.yml` にも入っていません ([ADR 0056](../adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D1 / [#302](https://github.com/yomote/mind-inbox/issues/302))。

移行後に `enableLogAnalytics` を切り替えて流し直すときも、同じ手順です。

> **Cosmos / OpenAI / Speech はここには来ません。** これらは「アプリそのもの」なのでアプリ系 RG (`rg-dev-mind-inbox`) に残します (2026-08-12 / 2026-08-14 の PO 裁定)。Cosmos のデータは RG を移して守るのではなく、**この RG の非公開 Storage へバックアップして戻せるようにします** (ADR 0056 D2 / 経路の実装は ADR 0046 D9)。

## ワンショットキット (PO 作業 約 10 分 / 推奨)

[`cicd/scripts/mgmt-bootstrap/`](../../cicd/scripts/mgmt-bootstrap/README.md) が、下の Steps 1〜5 と Verification の大半に加えて、**GitHub 設定管理 (mgmt) 層 ([`cicd/github/terraform/`](../../cicd/github/terraform/README.md)) の資格情報の準備**までを 1 本にまとめています ([#387](https://github.com/yomote/mind-inbox/issues/387) / [#390](https://github.com/yomote/mind-inbox/issues/390) / PO 裁定 2026-08-16: キット化可・セキュリティ最優先)。手作業でやる場合や中身の理解には、下の Steps がそのまま生きています。

### PO の 3 手

1. **GitHub App を手動フォームで作る** — <https://github.com/settings/apps/new> に[キットの README の「GitHub App を作る」](../../cicd/scripts/mgmt-bootstrap/README.md#github-app-を作る-手動フォーム--ここが正典)の値を写して **Create GitHub App**。**設定値の正典はその README の表**で、ここには写しません (二重管理してずれるため)。要点だけ: name `mind-inbox-settings-mgmt` / Webhook の **Active を外す** / Repository permissions は **Administration = Read and write** と **Metadata = Read-only** の 2 つだけ / **Only on this account**

   > 以前あった manifest flow のワンクリックページ (`create-github-app.html`) は**削除しました** — code 変換まで到達しないと App が登録されず、実際に作成されませんでした ([#497](https://github.com/yomote/mind-inbox/issues/497) / PO 裁定 2026-08-17)。

2. **App ID を控え、インストールして pem を取る** — App の settings ページで **App ID** を控え、**Install App** でこのリポジトリだけにインストールし、**Generate a private key** で pem をダウンロード。このとき **Private keys の一覧に自分が作った鍵以外が無いこと**も確認する (見覚えの無い鍵があれば Delete で即失効)
3. **スクリプトを流す**:

   ```bash
   cicd/scripts/mgmt-bootstrap/bootstrap.sh \
     --pem ~/Downloads/<app名>.<日付>.private-key.pem \
     --app-id <App ID> \
     --budget-email <通知先メール>
   ```

   what-if と terraform plan を目視確認しながら進みます (確認プロンプトあり)。plan が **import-only** (`0 to add, 0 to change, 0 to destroy`) であることを見たら、`--tf-apply` を足して再実行すると terraform の初回 apply (**state への取り込みだけで、GitHub の実設定は変更しない**) まで終わります。**差分が 1 つでもある plan は `--tf-apply` でも適用されません** — その差分の裁定は #387 (`enforce_admins`) で、キットが黙って倒してはいけない判断だからです。**再実行は冪等で安全**です。

   終わったら、スクリプトが最後に出す手順どおり**ローカルの pem を削除**します (自動では消しません — 格納が「成功に見えて壊れていた」場合に鍵を失うため、削除の確定だけ人間が行います)。

### 何が緑になれば成功か

スクリプト自身が fail-closed で検証します (1 つでも落ちれば exit≠0 で「何が済んで何が済んでいないか」の台帳が出ます):

- デプロイ `Succeeded` / 全資源に層タグ / 鍵が非エクスポート / 予算が実在して通知先あり (下の Verification 1・2・3・5 と同じ判定)
- Key Vault にシークレット `github-app-mgmt-private-key` が入った (照合はタグの sha256 — 値は読み戻さない)
- pem ↔ App ID の対応・インストール先・`administration=write` を GitHub API で実測
- terraform plan の集計行が `Plan: N to import, 0 to add, 0 to change, 0 to destroy.`

キットが**検証しないもの** (手動で確認):

- [ ] 撤収ガードの 2 本 (下の Verification 4 — 対話するため手動のまま)
- [ ] `--tf-apply` まで行った場合、以後の `terraform plan` も同じ import-only で回ること

### 失敗時の巻き戻し

| どこで失敗したか                | 巻き戻し                                                                                                                                                                                              |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Azure の apply                  | 下の Rollback 節 (個別リソース削除 / `recoverKeyVault`)。キットの再実行は冪等                                                                                                                         |
| pem の格納 (誤った鍵を入れた等) | 正しい pem で再実行すると**新バージョン**として格納される (旧バージョンは Key Vault に残る)。**鍵そのものを失効させたいときは GitHub 側** — App settings の Private keys から該当鍵を Delete (即失効) |
| App を作り直したい              | App settings から Delete GitHub App (インストールも消える)。新 App で 3 手をやり直し、pem は新バージョンとして格納される                                                                              |
| terraform apply (import-only)   | GitHub の実設定は変更していないので巻き戻し対象なし。一時作業ディレクトリ (tfstate) を消すだけ (スクリプトが場所を表示)                                                                               |

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

- **Key Vault の data-plane ロールは control-plane とは別に要ります。** サブスクリプション Owner でも**自動では付きません** — 実測 (PO / 2026-08-17) では Verification 3 が `ForbiddenByRbac` (`Assignment: (not found)`) で止まりました ([#499](https://github.com/yomote/mind-inbox/issues/499))。

  | 要る権限               | 何に使うか                                        | 付けるロール (vault スコープ) |
  | ---------------------- | ------------------------------------------------- | ----------------------------- |
  | 鍵メタデータの読み取り | Verification 3 (`az keyvault key show`)           | **Key Vault Reader**          |
  | シークレットの読み書き | キットの pem 格納 (`az keyvault secret show/set`) | **Key Vault Secrets Officer** |

  ```bash
  # 例: 自分に付ける (付与には Owner / User Access Administrator が要ります。反映まで数分)
  az role assignment create --role "Key Vault Reader" \
    --assignee "$(az ad signed-in-user show --query id -o tsv)" \
    --scope "$(az keyvault show -n kv-dev-mindbox --query id -o tsv)"
  ```

  2 つまとめてなら **Key Vault Administrator** でも通りますが、鍵マテリアルまで触れるので上の 2 つを推奨します。**Vault は手順 5 の apply で作られる**ので、付与できるのは apply の後です。

- **キット (bootstrap.sh) を使う場合はさらに**: 上の data-plane ロール 2 つ (キットは apply の直後に**使う前**の確認を入れており、無ければ付与コマンドを名指しして止まります。ただし確認は**読み取りだけ**なので、**シークレットの書き込み可否は pem 格納を実際に叩くまで未検証**です — そこでも付与コマンドが表示されます) / **Terraform 1.5+** / **GitHub リポジトリの admin** (App の作成・インストールは PO 本人にしかできない)。**キットは PO のローカルでだけ実行する** — pem (長期クレデンシャル) をサンドボックスに持ち込まない (ADR 0031)
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

6. E2E trace 復号鍵の URI を控える。**控えるのは版つき (`e2eTraceKeyUriWithVersion`) のほう**です ([#301](https://github.com/yomote/mind-inbox/issues/301) / [裁定記録](../adr/archive/operations/e2e-artifacts-are-secret-by-default.md) D5 / D9)。

   ```bash
   # 控えるのはこちら。末尾がバージョン ID で、e2e-artifacts.pub.json の keyVersion の出どころ
   az deployment group show -g rg-mgmt-mindbox -n main-mgmt \
     --query properties.outputs.e2eTraceKeyUriWithVersion.value -o tsv

   # 版なし。Vault と鍵名の確認用にだけ使う
   az deployment group show -g rg-mgmt-mindbox -n main-mgmt \
     --query properties.outputs.e2eTraceKeyUri.value -o tsv
   ```

   > ⚠️ **復号は「`.enc` に記録されたバージョン」を `--version` (または版つき `--id`) で指定します。** 版なし URI を渡すと Key Vault は**常に最新バージョン**で処理するので、**ローテーション後に旧バージョンで wrap された artifact が開けなくなります** — しかも失敗するのは「証拠が要る」と気づいた瞬間で、そのとき元の trace はもうありません。版なし URI は Vault と鍵名の確認用と割り切ってください。

   **適用が済んだら [`e2e-trace-keys.md`](e2e-trace-keys.md) の「管理系 RG の適用後」の手順が有効になります** — この時点でエージェントも `az keyvault key decrypt` で trace を復号してよくなり、公開鍵ファイルを `e2e-artifacts.pub.json` (PEM + 鍵バージョン) に差し替える作業が始まります。

## Verification

実行後、次がすべて満たされていることを確認:

- [ ] デプロイが `Succeeded` (下の 1)
- [ ] **作ったリソース全部に層タグが付いている** (下の 2) — このタグが撤収ガードの判定入力なので、**付いていないものはアプリ系と見なされて撤収で消えます**
- [ ] 鍵が**エクスポート不可**で作られている (下の 3 が**空**か `false` を返すこと — 空が正常。後述)
- [ ] **撤収ガードが管理系 RG を拒否する** (下の 4 が **2 回とも** exit 3 で、何も消えないこと) — 2 本目は `MGMT_RG` を別名に向けて `ALLOW_PROTECTED_DELETE=true` を足した場合で、**組み込みの保護が設定で外せない**ことを見ています
- [ ] **月次予算が実在する** (下の 5 が `budget-mgmt-mindbox` を 1 件返し、`contacts` が空でないこと) — 空 (`[]`) なら `budgetContactEmails` を渡し忘れており、**管理系 RG のコストがどの予算にも載っていません**

```bash
# 1. デプロイの結果
az deployment group show -g rg-mgmt-mindbox -n main-mgmt \
  --query properties.provisioningState -o tsv

# 2. 層タグ (layer 列が空のものは撤収ガードから見えていない)
az resource list -g rg-mgmt-mindbox \
  --query "[].{name:name,layer:tags.mindInboxLayer}" -o table

# 3. 鍵がエクスポート不可か。**空が正常** (下の注記)。exportable は attributes の下で、
#    key.exportable は存在しないパス -- 常に空を返すので判定に使えない (#499)
az keyvault key show --vault-name kv-dev-mindbox -n e2e-artifacts \
  --query attributes.exportable -o tsv

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

**Verification 3 は空が正常です。** Key Vault は**エクスポート不可の鍵に `exportable` 属性を持たせません** (省略が既定)。したがって `--query attributes.exportable` は**空を返すのが正常**で、`false` と一致するかで判定すると正常な鍵で必ず止まります。**落とすのは明示的に `true` が返ったときだけ**です ([#499](https://github.com/yomote/mind-inbox/issues/499) 発見 2 / キット側の判定は [`check_key_vault.py`](../../cicd/scripts/mgmt-bootstrap/check_key_vault.py))。

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

### Verification 3 が `ForbiddenByRbac` (`Assignment: (not found)`) で落ちる

- 原因: **Key Vault の data-plane ロールが無い。** control-plane のロール (Contributor / Owner) には含まれず、サブスクリプション Owner でも自動では付きません (実測 / [#499](https://github.com/yomote/mind-inbox/issues/499))
- 対処: Prerequisites の表のロール (**Key Vault Reader** / **Key Vault Secrets Officer**) を vault スコープで自分に付与してから再実行する。反映まで数分かかることがあります。キット (`bootstrap.sh`) は apply の直後に**使う前**の確認を入れており、足りなければ付与コマンドを名指しして止まります

### Verification 3 が空を返す

- 原因: **異常ではありません。** エクスポート不可の鍵は `attributes` に `exportable` を持ちません (省略が既定)
- 対処: 判定は「**明示的に `true` なら異常**」で行ってください。`false` との一致で判定すると正常な鍵で止まります。`--query key.exportable` は**存在しないパス**なので、鍵の状態に関係なく常に空です (誤りの実例 / [#499](https://github.com/yomote/mind-inbox/issues/499) 発見 2)

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

- ADR: [0056 管理系 / アプリ系とバックアップによるデータ保護](../adr/0056-management-and-app-layers-with-backup-based-data-protection.md) (**層の定義の正典**。Accepted / 2026-08-15 PO 裁定) / [0046 環境は宣言から再構築できる](../adr/0046-environment-rebuildable-from-declaration.md) D6/D9 (D1 は 0056 が supersede 済み / 2026-08-15 発効) / [E2E artifact は既定で秘密 (2026-08-12 の裁定記録)](../adr/archive/operations/e2e-artifacts-are-secret-by-default.md) D5 (**ADR ではありません** — #385 で運用文書へ退避。鍵の運用手順の正典は [`e2e-trace-keys.md`](e2e-trace-keys.md)) / [0003 2 フェーズ Bicep](../adr/0003-two-phase-bicep.md)
- 関連 Runbook: [`claude-web-azure-access.md`](claude-web-azure-access.md) / [`cosmos-persistence.md`](cosmos-persistence.md) / [`github-terraform.md`](github-terraform.md) (キットが資格情報を用意する先の GitHub 設定 mgmt 層)
- キット: [`cicd/scripts/mgmt-bootstrap/`](../../cicd/scripts/mgmt-bootstrap/README.md) ([#387](https://github.com/yomote/mind-inbox/issues/387) / [#390](https://github.com/yomote/mind-inbox/issues/390))
- 宣言とパラメータの説明: [`cicd/iac/README.md`](../../cicd/iac/README.md#1-5-管理系レイヤrg-mgmt-mindbox--一度きり)
- 撤収ガード: `cicd/scripts/env/` ([README](../../cicd/scripts/env/README.md))
