targetScope = 'resourceGroup'

// ============================================================================
// 持続層 (ADR 0046 D1 / Issue #302)
// ============================================================================
//
// **このテンプレートが作るリソースは、環境の撤収で消してはいけないもの**だけ。
// 適用先は環境層とは**別の RG** (既定 `rg-shared-mindbox`) で、
// `cicd/scripts/env/cleanup-env.sh` はこの RG を削除できない
// (`persistent_layer_guard.py` が構造的に拒否する)。
//
// 2-phase (ADR 0003) の順序は壊さない。持続層は bootstrap の**前**に一度だけ流す:
//
//   shared (この file / 一度きり) → bootstrap (環境層) → config (認証結線)
//
// 環境層は持続層を **output → parameter** で参照する (RG をまたぐ resource 参照は
// しない)。つまり持続層を先に流していないと環境層は結線できない、という一方向の依存。
//
// ----------------------------------------------------------------------------
// 段階適用 (enable* の既定値がなぜこうなっているか)
// ----------------------------------------------------------------------------
//
// **既定で作るのは「まだどこにも無いもの」だけ** = Key Vault (+ 鍵) とバックアップ Storage。
// Cosmos / OpenAI / Speech / Log Analytics は**既定 false** で、宣言だけ置いてある。
// 理由は、これらが現在 `rg-dev-mind-inbox` に**実在している**ため:
//
//   - Cosmos の無料枠 (`enableFreeTier`) は **1 サブスクに 1 つ**。移行前に true にすると
//     デプロイが落ちる。落ちるのは正しい (ADR 0030 D2「デプロイ成功 = 無料枠取得成功」)
//   - Speech F0 も **1 サブスクに 1 つ**
//   - 同名の Cognitive Services / Key Vault が soft-delete で残っていると名前衝突する
//
// **移行 (RG 間の移動) は本テンプレートの担当ではない。** ここは「移動先の宣言」であり、
// 実際の移動はダウンタイムと再結線を伴う別作業 (Issue #302 の「段階的に切り出す」)。
// 移行が済んだ環境では parameters で該当の enable* を true にする。
//
// 命名は環境層と揃える (`{type}-{env}-{appname}`)。**RG が変わっても名前は変えない** —
// 名前が変わると環境層の parameters と app settings が全部追従を要求されるため。
// 「持続層 = 環境をまたいで共有」ではない (それはユーザーデータが混ざる / #302 コメント)。

@description('Application name used for Azure resource naming (e.g., mind-box).')
param appName string = 'mind-box'

@allowed([
  'dev'
  'stg'
  'prod'
])
@description('Environment short name used for Azure resource naming. 持続層 RG は環境で共有するが、**リソース名は環境ごとに分ける** (データを混ぜない)。')
param environmentName string = 'dev'

@description('既定のリージョン。個別に指定しないリソースはここに作る。')
param location string = resourceGroup().location

var appNameCompact = replace(replace(appName, '-', ''), '_', '')

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

@description('持続層に Log Analytics を作る。**既定 false** — 環境層 (bootstrap) に既存の workspace があるため、移行が済むまで二重に作らない。')
param enableLogAnalytics bool = false

@description('Log Analytics workspace 名。環境層と同じ命名を使う (移行で名前を変えない)。')
param lawName string = 'law-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-ops'

@minValue(30)
@maxValue(730)
@description('保持日数。31 日以内は保持料金ゼロ (環境層と同じ既定)。')
param lawRetentionInDays int = 30

@description('1 日あたりの取り込み上限 (GB)。ここに当たると当日の収集が止まる = 課金ではなく可視性を失う。')
param lawDailyQuotaGb string = '0.15'

// -------------------- Cosmos DB (移行対象 / 既定 off) --------------------

@description('持続層に Cosmos を作る。**既定 false** — 無料枠は 1 サブスクに 1 つで、環境層の既存アカウントが枠を持っている。移行が済むまで true にしない。')
param enableCosmos bool = false

@description('Cosmos アカウント名 (グローバル一意 / 3-44 文字 / 小文字)。')
param cosmosAccountName string = toLower('cosmos-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Cosmos の保管リージョン。ADR 0030 D6 で Japan East 固定。')
param cosmosLocation string = 'japaneast'

@description('無料枠にオプトインする。**作成時のみ指定可**。枠が消費済みならデプロイが失敗する (= 成功したときだけ無料枠が取れている)。')
param enableCosmosFreeTier bool = true

@description('serverless で作る (無料枠は serverless 対象外)。')
param cosmosServerless bool = false

@description('Cosmos database 名。')
param cosmosDatabaseName string = 'mindinbox'

@minValue(400)
@description('DB 共有スループット (RU/s)。serverless では無視される。')
param cosmosThroughput int = 400

@minValue(400)
@description('アカウント全体の RU/s 上限。無料枠 1,000 を超えて課金が始まらないための歯止め。')
param cosmosTotalThroughputLimit int = 1000

// コンテナ定義は環境層 (`cicd/modules/bootstrap-core.bicep`) と**同じ形**にしてある。
// ズレると移行後に「デプロイは通るが実行時 404」になる。移行が済んだら片方を消す。
var cosmosContainerNames = [
  'problems'
  'history'
]
var cosmosAiAgentContainers = [
  {
    name: 'sessions'
    partitionKeyPath: '/id'
    defaultTtl: 604800
  }
  {
    name: 'approvals'
    partitionKeyPath: '/id'
    defaultTtl: 3600
  }
  {
    name: 'checkpoints'
    partitionKeyPath: '/workflow_name'
    defaultTtl: 3600
  }
]

// -------------------- Azure OpenAI / Speech (移行対象 / 既定 off) --------------------

@description('持続層に Azure OpenAI を作る。**既定 false** — 環境層に既存アカウントがあり、同名は soft-delete と衝突する。')
param enableOpenAi bool = false

@description('OpenAI アカウント名。')
param openAiAccountName string = toLower('oai-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('OpenAI のリージョン。')
param openAiLocation string = 'japaneast'

@allowed([
  'S0'
])
@description('OpenAI SKU。')
param openAiSkuName string = 'S0'

@description('OpenAI デプロイ名 (アプリが指す名前)。')
param openAiDeploymentName string = 'gpt-4o'

@description('モデル名。')
param openAiModelName string = 'gpt-4o'

@description('モデルバージョン。')
param openAiModelVersion string = '2024-11-20'

@minValue(1)
@description('デプロイの容量 (1,000 TPM 単位)。')
param openAiCapacity int = 10

@description('同名の OpenAI アカウントが soft-delete 状態のとき true で復旧する。')
param restoreOpenAiAccount bool = false

@description('持続層に Speech を作る。**既定 false** — F0 は 1 サブスクに 1 つ。')
param enableSpeech bool = false

@description('Speech アカウント名。')
param speechAccountName string = toLower('spch-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Speech のリージョン。')
param speechLocation string = 'japaneast'

@allowed([
  'F0'
  'S0'
])
@description('Speech SKU。F0 は超過時に課金ではなく停止するので予算事故が構造的に起きない (ADR 0013)。')
param speechSkuName string = 'F0'

// -------------------- 予算 (ADR 0013) --------------------
//
// **持続層を別 RG に出すと、環境層 RG に張ってある budget の射程から Cosmos と OpenAI が
// 外れる。** ここに同じ歯止めを置かないと、分離そのものがコスト可視性の後退になる。

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
      // **purge protection は持続層の構造的な保険**。true にすると、たとえ
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
// ⚠️ **ユーザーデータの置き場所はここに限定する。** Problem / Mention は PO 個人の
// 悩みそのもので、このリポジトリは public。ADR 0041 の「git データブランチ」方式を
// 流用してはいけない (#302 コメント)。

resource backupStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = if (enableBackupStorage) {
  name: backupStorageAccountName
  location: backupStorageLocation
  sku: {
    // LRS。バックアップの目的は「環境層を壊しても戻せる」であって地域災害対策ではない。
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

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (enableLogAnalytics) {
  name: lawName
  location: location
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
// Cosmos DB (移行対象 / ADR 0030)
// ============================================================================

resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = if (enableCosmos) {
  name: cosmosAccountName
  location: cosmosLocation
  kind: 'GlobalDocumentDB'
  properties: union(
    {
      databaseAccountOfferType: 'Standard'
      locations: [
        {
          locationName: cosmosLocation
          failoverPriority: 0
          isZoneRedundant: false
        }
      ]
      enableFreeTier: enableCosmosFreeTier && !cosmosServerless
      // ADR 0030 D3: アカウントキーを殺す。data plane は Entra トークンだけが通る。
      disableLocalAuth: true
      publicNetworkAccess: 'Enabled'
      minimalTlsVersion: 'Tls12'
      capabilities: cosmosServerless
        ? [
            {
              name: 'EnableServerless'
            }
          ]
        : []
    },
    cosmosServerless
      ? {}
      : {
          capacity: {
            totalThroughputLimit: cosmosTotalThroughputLimit
          }
        }
  )
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = if (enableCosmos) {
  parent: cosmosAccount
  name: cosmosDatabaseName
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
    options: cosmosServerless
      ? {}
      : {
          throughput: cosmosThroughput
        }
  }
}

resource cosmosContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [
  for containerName in cosmosContainerNames: if (enableCosmos) {
    parent: cosmosDatabase
    name: containerName
    properties: {
      resource: {
        id: containerName
        partitionKey: {
          paths: [
            '/userId'
          ]
          kind: 'Hash'
        }
        // TTL は付けない。Problem も履歴も消してはいけない。
      }
    }
  }
]

// Cosmos の control plane は同一アカウントへの並行操作で 409/429 になることがあるため
// 1 つずつ直列に作る (環境層と同じ)。
@batchSize(1)
resource cosmosAiAgentTtlContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [
  for c in cosmosAiAgentContainers: if (enableCosmos) {
    parent: cosmosDatabase
    name: c.name
    properties: {
      resource: {
        id: c.name
        partitionKey: {
          paths: [
            c.partitionKeyPath
          ]
          kind: 'Hash'
        }
        defaultTtl: c.defaultTtl
      }
    }
    dependsOn: [
      cosmosContainers
    ]
  }
]

// data plane のロール割り当て (BFF / ai-agent の MI) は**環境層が持つ**。
// principal が環境層のリソース (Function App / Container App) の MI なので、
// 環境を作り直すたびに変わる = 持続層のライフサイクルではない。

// ============================================================================
// Azure OpenAI / Speech (移行対象)
// ============================================================================

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = if (enableOpenAi) {
  name: openAiAccountName
  location: openAiLocation
  kind: 'OpenAI'
  sku: {
    name: openAiSkuName
  }
  properties: union(
    {
      customSubDomainName: openAiAccountName
      publicNetworkAccess: 'Enabled'
      disableLocalAuth: false
    },
    restoreOpenAiAccount
      ? {
          restore: true
        }
      : {}
  )
}

resource openAiDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = if (enableOpenAi) {
  parent: openAiAccount
  name: openAiDeploymentName
  sku: {
    name: 'Standard'
    capacity: openAiCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: openAiModelName
      version: openAiModelVersion
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
}

resource speechAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = if (enableSpeech) {
  name: speechAccountName
  location: speechLocation
  kind: 'SpeechServices'
  sku: {
    name: speechSkuName
  }
  properties: {
    // Entra 認証 (aad# authorization token) には custom subdomain が必須。
    customSubDomainName: speechAccountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

// ============================================================================
// 予算
// ============================================================================

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = if (enableBudgetAlert && !empty(budgetContactEmails)) {
  name: 'budget-shared-${appNameCompact}'
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
// outputs — 環境層 (bootstrap) へ parameter で渡す値
// ============================================================================
//
// RG をまたぐ resource 参照はしない。ここに出ていない値は環境層から見えない。

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

output cosmosEnabled bool = enableCosmos
output cosmosAccountName string = enableCosmos ? cosmosAccountName : ''
output cosmosEndpoint string = cosmosAccount.?properties.documentEndpoint ?? ''
output cosmosDatabaseName string = enableCosmos ? cosmosDatabaseName : ''
output cosmosAccountResourceId string = enableCosmos ? cosmosAccount.id : ''

output openAiEnabled bool = enableOpenAi
output openAiAccountName string = enableOpenAi ? openAiAccountName : ''
output openAiEndpoint string = openAiAccount.?properties.endpoint ?? ''
output openAiDeploymentName string = enableOpenAi ? openAiDeploymentName : ''
output openAiAccountResourceId string = enableOpenAi ? openAiAccount.id : ''

output speechEnabled bool = enableSpeech
output speechAccountName string = enableSpeech ? speechAccountName : ''
output speechRegion string = enableSpeech ? speechLocation : ''
output speechAccountResourceId string = enableSpeech ? speechAccount.id : ''

output budgetAlertEnabled bool = enableBudgetAlert && !empty(budgetContactEmails)
