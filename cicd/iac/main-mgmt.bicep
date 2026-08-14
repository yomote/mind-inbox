targetScope = 'resourceGroup'

// ============================================================================
// 管理系レイヤ (ADR 0056 D1 / Issue #302)
// ============================================================================
//
// **このテンプレートが作るのは「システムを運用するためのもの」だけ**で、
// アプリそのものは 1 つも作らない。適用先はアプリ系とは**別の RG**
// (既定 `rg-mgmt-mindbox`) で、`cicd/scripts/env/cleanup-env.sh` はこの RG を
// 削除できない (`persistent_layer_guard.py` が構造的に拒否する)。
//
// ----------------------------------------------------------------------------
// 層の分け方 — 「消えると困るか」ではなく「運用のためか / アプリそのものか」
// ----------------------------------------------------------------------------
//
// | 層         | RG                  | 中身                                                    | 撤収          |
// | ---------- | ------------------- | ------------------------------------------------------- | ------------- |
// | 管理系     | `rg-mgmt-mindbox`   | Key Vault / Log Analytics / バックアップ Storage / 予算 | ❌ 触らない   |
// | アプリ系   | `rg-{env}-mind-inbox` | Cosmos / OpenAI / Speech / Container Apps / Functions / SWA | ✅ 使い捨て |
//
// **Cosmos / OpenAI / Speech はアプリ系に残す。** これらは「アプリそのもの」であって
// 運用のための道具ではない (2026-08-12 / 2026-08-14 の PO 裁定 — #302)。
// 消えると困る Cosmos のデータは、**RG を移して守るのではなくバックアップで戻せるようにする**:
// 管理系 RG の非公開 Storage へ自前エクスポートする (ADR 0056 D2 / 経路の実装は ADR 0046 D9)。
// これで「アプリ系 RG は使い捨て」を貫ける。
//
// ⚠️ **ユーザーデータを git データブランチに出さない。** ADR 0041 (UX 観測) の前例が
// あるが、このリポジトリは public で、Problem / Mention は PO 個人の悩みそのもの。
// 出力先は管理系 RG の非公開 Storage に限定する (#302 コメント)。
//
// ⚠️ **復元したことのないバックアップはバックアップではない** (ADR 0018)。
// エクスポートを作ったら、空の Cosmos へ復元して Problem が戻ることを 1 回通すまで
// 完遂としない。それが済むまで、撤収ガードは「Cosmos が居る RG の撤収」を
// 暫定的に拒否し続ける (ADR 0056 D3 / `persistent_layer_guard.py` の
// DATA_BEARING_RESOURCE_TYPES)。
//
// ----------------------------------------------------------------------------
// 適用の位置づけ
// ----------------------------------------------------------------------------
//
// 2-phase (ADR 0003) の順序は壊さない。管理系は bootstrap の**前**に一度だけ流す:
//
//   mgmt (この file / 一度きり) → bootstrap (アプリ系) → config (認証結線)
//
// アプリ系は管理系を **output → parameter** で参照する (RG をまたぐ resource 参照は
// しない)。つまり管理系を先に流していないとアプリ系は結線できない、という一方向の依存。
//
// 命名はアプリ系と揃える (`{type}-{env}-{appname}`)。**RG が変わっても名前は変えない** —
// 名前が変わるとアプリ系の parameters と app settings が全部追従を要求されるため。
// 「管理系 = 環境をまたいで共有」ではない (それはユーザーデータが混ざる / #302 コメント)。

@description('Application name used for Azure resource naming (e.g., mind-box).')
param appName string = 'mind-box'

@allowed([
  'dev'
  'stg'
  'prod'
])
@description('Environment short name used for Azure resource naming. 管理系 RG は環境で共有するが、**リソース名は環境ごとに分ける** (データを混ぜない)。')
param environmentName string = 'dev'

@description('既定のリージョン。個別に指定しないリソースはここに作る。')
param location string = resourceGroup().location

var appNameCompact = replace(replace(appName, '-', ''), '_', '')

// -------------------- 層タグ (撤収ガードの一次ソース) --------------------
//
// **このタグは飾りではなく、`cleanup-env.sh` の判定入力**。
// Key Vault / Storage / Log Analytics はアプリ系にも同じ型が居る (SQL 管理者用 vault /
// Function App の実行 storage / アプリ系の workspace) ため、**型だけでは層を区別できない**。
// 型で一括りにすると正当なアプリ系の撤収まで常に拒否され、`ALLOW_PROTECTED_DELETE=true`
// が常用になってガードが意味を失う。逆に型から外すとバックアップ Storage が黙って消える。
// そこで「管理系として作ったものにはこのタグが付いている」を機械可読な事実にしておく。
//
// このタグが付いた資源は、**どの RG に居ても** `persistent_layer_guard.py` が管理系と
// 判定して撤収を拒否する (誤ってアプリ系 RG へこのテンプレートを流した場合も含む)。
// タグ名・値を変えるときは `persistent_layer_guard.py` の LAYER_TAG_KEY /
// LAYER_TAG_MANAGEMENT_VALUE も同じ PR で直すこと。
var managementLayerTags = {
  mindInboxLayer: 'management'
  mindInboxEnvironment: environmentName
}

// -------------------- Key Vault (新設 / ADR 0045 D5 / #301) --------------------

@description('Key Vault を作る。E2E trace の復号鍵 (非エクスポート) の器。')
param enableKeyVault bool = true

@description('Key Vault 名 (グローバル一意 / 3-24 文字)。soft-delete 中の同名が居ると作成が失敗する。')
param keyVaultName string = toLower('kv-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Key Vault のリージョン。')
param keyVaultLocation string = location

@description('同名の Key Vault が soft-delete 状態で残っているとき true にして復旧する。')
param recoverKeyVault bool = false

// E2E trace の封筒暗号 (#301 / ADR 0045 D5 改訂)。
// **secret ではなく鍵オブジェクトとして持つ**のが判断の核。secret にすると値そのものを
// 読み出せてしまい、「サンドボックスに長期クレデンシャルを置かない」(ADR 0031) を破る。
// 鍵オブジェクト + 非エクスポートなら、エージェントができるのは
// 「Key Vault の中で復号してもらう」だけで、鍵の複製を持ち出せない。
@description('E2E trace 復号用の RSA 鍵を宣言する。')
param enableE2eTraceKey bool = true

@description('E2E trace 復号用の鍵名。cicd/keys/ の公開鍵ファイル名と揃える。')
param e2eTraceKeyName string = 'e2e-artifacts'

@allowed([
  2048
  3072
  4096
])
@description('E2E trace 復号鍵の鍵長。既定 3072 は NIST SP 800-57 の「2031 年以降も可」の下限。')
param e2eTraceKeySize int = 3072

@description('Key Vault Crypto User を与える principal (object) ID の一覧。CI には渡さない — CI は公開鍵で暗号化するだけで復号しない。空なら誰にも付けない (=後から付ける)。')
param keyVaultCryptoUserPrincipalIds array = []

@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
@description('keyVaultCryptoUserPrincipalIds の principal 種別。')
param keyVaultCryptoUserPrincipalType string = 'User'

// -------------------- バックアップ保管ストレージ (新設 / ADR 0046 D9) --------------------

@description('Cosmos の自前エクスポート先 Storage を作る。')
param enableBackupStorage bool = true

@description('バックアップ用 Storage アカウント名 (グローバル一意 / 3-24 文字 / 小文字英数のみ)。')
param backupStorageAccountName string = toLower('st${take('${environmentName}${replace(replace(appName, '-', ''), '_', '')}bak', 22)}')

@description('バックアップ用 Storage のリージョン。')
param backupStorageLocation string = location

@description('バックアップを置く blob コンテナ名。')
param backupContainerName string = 'cosmos-backup'

@minValue(1)
@maxValue(365)
@description('削除した blob を復旧できる日数。')
param backupBlobRetentionDays int = 30

// -------------------- Log Analytics (移行対象 / 既定 off) --------------------

@description('管理系に Log Analytics を作る。**既定 false** — アプリ系 (bootstrap) に既存の workspace があるため、移行が済むまで二重に作らない。')
param enableLogAnalytics bool = false

@description('Log Analytics workspace 名。アプリ系と同じ命名を使う (移行で名前を変えない)。')
param lawName string = 'law-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-ops'

@minValue(30)
@maxValue(730)
@description('保持日数。31 日以内は保持料金ゼロ (アプリ系と同じ既定)。')
param lawRetentionInDays int = 30

@description('1 日あたりの取り込み上限 (GB)。ここに当たると当日の収集が止まる = 課金ではなく可視性を失う。')
param lawDailyQuotaGb string = '0.15'

// -------------------- 予算 (ADR 0013) --------------------
//
// 管理系 RG のコストは小さい (Key Vault + Storage + 任意で LAW) が、**歯止めが
// どこにも無い RG を作らない**。アプリ系 RG の予算はアプリ系 RG に張ったままで、
// ここはこの RG の分だけを見る。

@description('この RG に月次予算アラートを作る。')
param enableBudgetAlert bool = true

@description('月額予算 (請求通貨。JPY サブスクなら円)。')
param budgetAmount int = 3000

@description('閾値超過を通知するメールアドレス。空なら budget を作らない (通知先の無いアラートは沈黙と同じ)。')
param budgetContactEmails array = []

@description('予算の開始日 (月初 / yyyy-MM-dd)。作成後は変更できないので固定値で持つ。')
param budgetStartDate string = '2026-09-01'

// ============================================================================
// Key Vault
// ============================================================================

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = if (enableKeyVault) {
  name: keyVaultName
  location: keyVaultLocation
  tags: managementLayerTags
  properties: union(
    {
      tenantId: subscription().tenantId
      sku: {
        family: 'A'
        name: 'standard'
      }
      // アクセスポリシーではなく RBAC。ロールの持ち主を bicep 1 本に寄せる。
      enableRbacAuthorization: true
      publicNetworkAccess: 'Enabled'
      networkAcls: {
        bypass: 'AzureServices'
        defaultAction: 'Allow'
      }
      softDeleteRetentionInDays: 90
      // **purge protection は管理系の構造的な保険**。true にすると、たとえ
      // cleanup-env.sh の purge フラグを立てても soft-delete を消せなくなる
      // (Azure 側が拒否する)。一度 true にすると戻せない — それが狙い。
      enablePurgeProtection: true
    },
    recoverKeyVault
      ? {
          createMode: 'recover'
        }
      : {}
  )
}

// E2E trace の復号鍵。**非エクスポート**。
//
// `attributes.exportable` を明示的に false にしてある。既定も false だが、
// 「書いていないから false」と「false だと宣言した」は読み手にとって別物で、
// ここは取り違えると設計 (ADR 0045 D5 改訂) が崩れる 1 行なので明示する。
// exportable を true にするには release policy が要る = うっかり true にはできない。
//
// keyOps に `encrypt` / `wrapKey` (公開鍵側の操作) を含めているのは、CI が
// openssl でオフライン暗号化する経路が壊れたときに Key Vault 側でも同じことが
// できるようにするため。秘密側は `decrypt` / `unwrapKey` の 2 つだけ。
resource e2eTraceKey 'Microsoft.KeyVault/vaults/keys@2023-07-01' = if (enableKeyVault && enableE2eTraceKey) {
  parent: keyVault
  name: e2eTraceKeyName
  properties: {
    kty: 'RSA'
    keySize: e2eTraceKeySize
    keyOps: [
      'encrypt'
      'decrypt'
      'wrapKey'
      'unwrapKey'
    ]
    attributes: {
      enabled: true
      exportable: false
    }
  }
}

// Key Vault Crypto User — 鍵で暗号操作はできるが、**鍵の一覧・作成・削除はできない**。
// 復号する人 (PO / device-code のエージェント) に与えるのはこれだけで足りる。
var keyVaultCryptoUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '12338af0-0e69-4776-bea7-57ae8d297424'
)

resource keyVaultCryptoUserAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for principalId in keyVaultCryptoUserPrincipalIds: if (enableKeyVault) {
    name: guid(resourceGroup().id, keyVaultName, string(principalId), 'kv-crypto-user')
    scope: keyVault
    properties: {
      roleDefinitionId: keyVaultCryptoUserRoleId
      principalId: principalId
      principalType: keyVaultCryptoUserPrincipalType
    }
  }
]

// ============================================================================
// バックアップ保管ストレージ (ADR 0046 D9)
// ============================================================================
//
// **層を分ける目的の中心はここ。** アプリ系 RG を使い捨てにできるのは、消えて困る
// ものが「別の RG に避難している」からではなく、**戻せるから**。その戻し先がこれ。
//
// ⚠️ **ユーザーデータの置き場所はここに限定する。** Problem / Mention は PO 個人の
// 悩みそのもので、このリポジトリは public。ADR 0041 の「git データブランチ」方式を
// 流用してはいけない (#302 コメント)。

resource backupStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (enableBackupStorage) {
  name: backupStorageAccountName
  location: backupStorageLocation
  tags: managementLayerTags
  sku: {
    // LRS。バックアップの目的は「アプリ系を壊しても戻せる」であって地域災害対策ではない。
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    // Hot のまま置く。Cool は保管が安い代わりに 30 日以内の削除・上書きに早期削除料金が
    // かかり、週次でバックアップを回す使い方 (ADR 0046 D9) と噛み合わない。
    // データ量が小さいので Hot でも待機コストはほぼ乗らない。
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    // 共有キー (アカウントキー) を無効化。Cosmos の disableLocalAuth と同じ思想で、
    // 「鍵が漏れる経路」を構造的に無くす。読み書きは Entra + RBAC のみ。
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource backupBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = if (enableBackupStorage) {
  parent: backupStorage
  name: 'default'
  properties: {
    // 「消したつもりが本番だった」を戻せるようにする。バックアップ自体の soft-delete。
    deleteRetentionPolicy: {
      enabled: true
      days: backupBlobRetentionDays
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: backupBlobRetentionDays
    }
  }
}

resource backupContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = if (enableBackupStorage) {
  parent: backupBlobService
  name: backupContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ============================================================================
// Log Analytics (移行対象)
// ============================================================================
//
// 診断ログの履歴は「運用のためのもの」なので管理系。ただし現在の workspace は
// アプリ系 (bootstrap) に実在するので、移行が済むまで既定 false のまま。

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (enableLogAnalytics) {
  name: lawName
  location: location
  tags: managementLayerTags
  properties: {
    retentionInDays: lawRetentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    sku: {
      name: 'PerGB2018'
    }
    workspaceCapping: {
      dailyQuotaGb: json(lawDailyQuotaGb)
    }
  }
}

// ============================================================================
// 予算
// ============================================================================

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = if (enableBudgetAlert && !empty(budgetContactEmails)) {
  name: 'budget-mgmt-${appNameCompact}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      forecasted80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Forecasted'
        contactEmails: budgetContactEmails
      }
      actual50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: budgetContactEmails
      }
      actual100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: budgetContactEmails
      }
    }
  }
}

// ============================================================================
// outputs — アプリ系 (bootstrap) やバックアップスクリプトへ渡す値
// ============================================================================
//
// RG をまたぐ resource 参照はしない。ここに出ていない値はアプリ系から見えない。

output keyVaultEnabled bool = enableKeyVault
output keyVaultName string = enableKeyVault ? keyVaultName : ''
output keyVaultUri string = keyVault.?properties.vaultUri ?? ''
output e2eTraceKeyEnabled bool = enableKeyVault && enableE2eTraceKey
output e2eTraceKeyName string = (enableKeyVault && enableE2eTraceKey) ? e2eTraceKeyName : ''
// 鍵の URI (kid)。`az keyvault key decrypt --id <kid>` にそのまま渡せる。
output e2eTraceKeyUri string = e2eTraceKey.?properties.keyUri ?? ''

output backupStorageEnabled bool = enableBackupStorage
output backupStorageAccountName string = enableBackupStorage ? backupStorageAccountName : ''
output backupContainerName string = enableBackupStorage ? backupContainerName : ''

output logAnalyticsEnabled bool = enableLogAnalytics
output logAnalyticsWorkspaceId string = enableLogAnalytics ? law.id : ''
output logAnalyticsWorkspaceName string = enableLogAnalytics ? lawName : ''

// **この output は「budget が実在するか」ではない。** その回に渡したパラメータの写しなので、
// budget を作ったあと budgetContactEmails を省いて流し直すと、budget は incremental で
// 残ったまま false になる。実在の確認は `az consumption budget list` 側で行うこと
// (docs/runbooks/mgmt-layer-apply.md の Verification 5)。
output budgetAlertEnabled bool = enableBudgetAlert && !empty(budgetContactEmails)
