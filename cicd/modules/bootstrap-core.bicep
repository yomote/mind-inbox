targetScope = 'resourceGroup'

@description('Application name used for Azure resource naming (e.g., mind-box).')
param appName string = 'mind-box'

@allowed([
  'dev'
  'stg'
  'prod'
])
@description('Environment short name used for Azure resource naming.')
param environmentName string = 'dev'

@description('Azure region')
param location string = resourceGroup().location

@allowed([
  'westus2'
  'centralus'
  'eastus2'
  'westeurope'
  'eastasia'
])
@description('Azure Static Web Apps region (Static Web Apps supports a limited set of regions)')
param staticSiteLocation string = 'eastasia'

@description('Static Web Apps name (must be globally unique)')
param staticSiteName string = toLower('swa-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@allowed([
  'Free'
  'Standard'
])
@description('Static Web Apps SKU. 既定 Free (ADR 0013 / #69): linked backend は Standard 専用なので、フロントは Functions を直叩きし、認可は Functions 側の EasyAuth が担う。')
param staticSiteSkuName string = 'Free'

@description('Repository URL for Static Web Apps (optional; set to empty to create without repo linkage)')
param staticSiteRepositoryUrl string = ''

@description('Repository branch for Static Web Apps (used when repository URL is set)')
param staticSiteBranch string = 'main'

@description('Optional repository token (used only when repository URL is set)')
@secure()
param staticSiteRepositoryToken string = ''

@description('Enable Entra ID built-in authentication for Static Web Apps (requires app settings and staticwebapp.config.json).')
param enableStaticSiteEntraAuth bool = false

@description('Auto-create/update Entra app registration via deployment script when SWA Entra auth is enabled.')
param autoCreateStaticSiteEntraAppRegistration bool = false

@description('Display name for the auto-created Entra app registration.')
param staticSiteEntraAppDisplayName string = 'app-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-swa'

@description('User Assigned Managed Identity resource ID used by deployment script for Entra app automation.')
param staticSiteEntraBootstrapUserAssignedIdentityResourceId string = ''

@description('User Assigned Managed Identity client ID used by deployment script for az login --identity.')
param staticSiteEntraBootstrapUserAssignedIdentityClientId string = ''

@minValue(1)
@maxValue(5)
@description('Validity period (years) for auto-generated Entra app client secret.')
param staticSiteEntraAppSecretYears int = 2

@description('Entra tenant ID used by Static Web Apps auth (used in staticwebapp.config.json openIdIssuer).')
param staticSiteEntraTenantId string = subscription().tenantId

@description('Entra app (client) ID for Static Web Apps built-in auth.')
param staticSiteEntraClientId string = ''

@secure()
@description('Entra app client secret for Static Web Apps built-in auth.')
param staticSiteEntraClientSecret string = ''

@description('Apply Function App EasyAuth lockdown in bootstrap (recommended: false; manage in main-config).')
param applyFunctionAuthLockdown bool = false

// -------------------- Function App 認可 (EasyAuth / Entra) — #69 --------------------
// SWA Free には linked backend が無く、フロントは Functions を別オリジンから直叩きする。
// SWA の認証は SWA が配る静的ファイルにしか効かないため、課金の芯 (OpenAI) を持つ
// Functions 側に認可の門を置く。未認証は関数コード起動前に 401 (ADR 0013)。
@description('Entra (Azure AD) app client ID used by the SPA and accepted by Function App EasyAuth. 空なら EasyAuth は構成しない。')
param functionAuthEntraClientId string = ''

@description('Container Apps の認証の門 (ADR 0017) の audience となる Entra app client ID。BFF はこの audience の Managed Identity トークンを付けて下流を呼ぶ。空なら AUDIENCE 設定を出力しない (= BFF はトークンを付けず、門が立っていれば 401 になる)。')
param containerAppsGateClientId string = ''

// BFF → 下流の base URL。ai-agent / vv-wrap の Container App は #188 でこのテンプレートに
// 取り込んだため、enable*Aca=true なら FQDN をリソース参照から直接導出する (下の
// effective*BaseUrl)。この 2 つのパラメータは **enable*Aca=false のときのフォールバック**
// (テンプレート外で立てた CA を BFF に配線したい場合) としてだけ残る。
// deploy-backend.sh の再結線は FQDN 変化への追従・フォールバックとして引き続き残る。
@description('BFF が呼ぶ ai-agent の base URL。enableAiAgentAca=true なら無視され、テンプレート内の Container App の FQDN が使われる。')
param aiAgentBaseUrl string = ''

@description('BFF が呼ぶ voicevox-wrapper の base URL (エンジン直ではなく wrapper)。enableVoicevoxWrapperAca=true なら無視され、テンプレート内の Container App の FQDN が使われる。')
param voicevoxWrapperBaseUrl string = ''

@description('Entra tenant ID. 単一テナント限定 (この tenant の identity のみ許可)。')
param functionAuthEntraTenantId string = tenant().tenantId

@description('Extra CORS origins allowed to call the Function App (SWA の既定ホスト名は自動で許可される)。')
param functionExtraCorsOrigins array = []

// -------------------- 予算アラート (二重防御) — #69 --------------------
// 認可を破られても・設定を緩めても、請求で気づけるようにする (ADR 0013)。
@description('Create a monthly budget alert on this resource group.')
param enableBudgetAlert bool = true

@description('Monthly budget amount in the billing currency (JPY サブスクなら円)。')
param budgetAmount int = 3000

@description('Email addresses notified when budget thresholds are crossed.')
param budgetContactEmails array = []

// Azure の Consumption Budget は作成後に startDate を変更できない。
// utcNow() を既定にすると毎デプロイで再計算され、月をまたいだ再デプロイ (ADR 0013 の
// main マージ自動デプロイ) で既存 budget の更新が startDate 不一致で失敗しうる。
// そのため **固定値** を既定にし、parameters ファイルで明示管理する。
@description('Budget start date (first day of a month, yyyy-MM-dd)。作成後は変更不可なので固定値で持つ。')
param budgetStartDate string = '2026-08-01'

@description('Azure Functions app name (must be globally unique)')
param functionAppName string = toLower('func-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Storage account name for Azure Functions (lowercase, 3-24 chars, numbers only after prefix)')
param functionStorageAccountName string = toLower('st${take('${environmentName}${replace(replace(appName, '-', ''), '_', '')}func', 22)}')

@description('App Service plan name for Azure Functions')
param functionPlanName string = 'asp-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-func'

@description('Azure region for Azure Functions resources (storage/plan/app). Defaults to staticSiteLocation to avoid quota constraints in resource group region.')
param functionLocation string = staticSiteLocation

@description('Enable VOICEVOX on Azure Container Apps (Serverless GPU).')
param enableVoicevoxAca bool = false

@allowed([
  'cpu'
  'gpu'
])
@description('VOICEVOX tier (ADR 0010). cpu = Consumption プロファイルで速く安く立てる（既定）/ gpu = T4 で喋りが速い。cpu 時は GPU プロファイル・GPU イメージを使わない。')
param voicevoxTier string = 'cpu'

@description('Azure region for VOICEVOX Container Apps environment/app.')
param voicevoxLocation string = location

// -------------------- 共有 Container Apps Environment (#68 / ADR 0013) --------------------
// 以前は ai-agent / voicevox / vv-wrapper で CAE を 3 つ作っていた。CAE の作成は 1 つ数分かかり、
// フル構築の所要を押し上げていたため dev では 1 環境に相乗りする。
// トレードオフ: 障害ドメインを共有する (1 環境の不調が全サービスに及ぶ)。dev では許容。
@description('Shared Container Apps Environment name (ai-agent / voicevox / vv-wrapper が相乗りする)。')
param containerAppsEnvironmentName string = toLower('cae-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Region for the shared Container Apps Environment. VOICEVOX GPU のリージョン制約が最も厳しいため既定は voicevoxLocation に合わせる。')
param containerAppsLocation string = voicevoxLocation

@description('Container App name for VOICEVOX engine.')
param voicevoxContainerAppName string = toLower('ca-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-voicevox')

@description('VOICEVOX engine image (GPU).')
param voicevoxImage string = 'voicevox/voicevox_engine:nvidia-latest'

@description('Container Apps GPU workload profile name used by VOICEVOX app.')
param voicevoxWorkloadProfileName string = 'voicevox-gpu-t4'

@description('Container Apps GPU workload profile type used by VOICEVOX app.')
param voicevoxWorkloadProfileType string = 'Consumption-GPU-NC8as-T4'

@minValue(1)
@description('CPU cores for VOICEVOX container app (for T4 profile, use 8).')
param voicevoxCpu int = 8

@description('Memory for VOICEVOX container app (for T4 profile, use 56Gi).')
param voicevoxMemory string = '56Gi'

@minValue(0)
@description('Minimum replicas for VOICEVOX container app. 0 enables scale-to-zero.')
param voicevoxMinReplicas int = 0

@minValue(1)
@description('Maximum replicas for VOICEVOX container app.')
param voicevoxMaxReplicas int = 1

// -------------------- Azure OpenAI params --------------------
@description('Enable Azure OpenAI account and model deployment.')
param enableOpenAi bool = false

@description('Azure region for Azure OpenAI. Must be a region where OpenAI is available (e.g. eastus, japaneast, swedencentral).')
param openAiLocation string = location

@description('Azure OpenAI account name (must be globally unique).')
param openAiAccountName string = toLower('oai-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@allowed([
  'S0'
])
@description('Azure OpenAI account SKU.')
param openAiSkuName string = 'S0'

@description('Model deployment name (used as the deployment identifier in API calls).')
param openAiDeploymentName string = 'gpt-4o'

@description('OpenAI model name.')
param openAiModelName string = 'gpt-4o'

@description('OpenAI model version.')
param openAiModelVersion string = '2024-11-20'

@minValue(1)
@description('Deployment capacity in units of 1,000 tokens-per-minute (TPM). Subject to regional quota.')
param openAiCapacity int = 10

// -------------------- Azure Speech params (ADR 0023 / #121) --------------------
@description('Enable Azure Speech (server STT). F0 = 無料枠 月 5h、超過時は停止で課金なし。')
param enableSpeech bool = false

@description('Azure region for the Speech service.')
param speechLocation string = location

@description('Speech account name (custom subdomain にも使うため globally unique)。')
param speechAccountName string = toLower('spch-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@allowed([
  'F0'
  'S0'
])
@description('Speech SKU。F0 (無料枠) から開始 — 有料化 (S0) は design-gate を通す (ADR 0023)。')
param speechSkuName string = 'F0'

// -------------------- Cosmos DB params (ADR 0030 / #165) --------------------
// FR-4「再起動・再ログインで消えない」の実体。BFF (Functions) だけが触る単一ストア。
// 既定 false は他の enable* (enableSql / enableVoicevoxAca / enableAiAgentAca 等) と揃えたもの。
// dev 環境は cicd/iac/main-bootstrap.parameters.json で明示的に有効化する。
@description('Provision Cosmos DB (NoSQL) for Problem / history persistence (ADR 0030 D1)。')
param enableCosmos bool = false

@description('Cosmos DB account name (globally unique / 3-44 chars, lowercase)。')
param cosmosAccountName string = toLower('cosmos-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Cosmos DB の保管リージョン。ADR 0030 D6 で Japan East 固定 (NFR-1「保管リージョンの明確化」)。Functions は East Asia のままでよい。')
param cosmosLocation string = 'japaneast'

@description('無料枠 (1,000 RU/s + 25 GB / アカウント生涯) にオプトインする。**作成時のみ指定可**で、後から有効化できない。枠が既に消費済みだとデプロイ自体が失敗する = 「デプロイ成功 = 無料枠取得成功」と読める (ADR 0030 D2)。')
param enableCosmosFreeTier bool = true

@description('serverless で作る (無料枠は serverless 対象外なので enableCosmosFreeTier=false のときのフォールバック / ADR 0030 D2)。')
param cosmosServerless bool = false

@description('Cosmos DB database name。')
param cosmosDatabaseName string = 'mindinbox'

@minValue(400)
@description('DB 共有スループット (RU/s)。**既定 400 は意図的**: 無料枠が取れれば 1,000 RU/s まで無料だが、取れなかった場合 400 RU/s でも月 ¥3,500 相当で予算 ¥3,000 (ADR 0013) を超える。上げる前にコストを確認すること。serverless では無視される。')
param cosmosThroughput int = 400

@minValue(400)
@description('アカウント全体のスループット上限 (RU/s)。無料枠 1,000 を超えて課金が始まらないための構造的な歯止め。serverless では指定しない。')
param cosmosTotalThroughputLimit int = 1000

// コンテナ名はコンテナ宣言と BFF の app settings の両方で使う。ズレると
// 「デプロイは通るが実行時に 404」になるので 1 箇所で持つ。
var cosmosContainerNames = [
  'problems'
  'history'
]
var cosmosProblemsContainerName = cosmosContainerNames[0]
var cosmosHistoryContainerName = cosmosContainerNames[1]

// ai-agent 側の短命データ (#188 / ADR 0030 D4 の見直し)。器はここ、利用者は
// ai-agent Container App (下の aiAgentEnvVars)。名前の既定は ai-agent の
// config.py (COSMOS_*_CONTAINER) と揃えている。
// TTL は _ts (最終更新) 起点のスライディング — save のたびに寿命が延びる。
// 削除処理を書かないための仕組み (ADR 0030 動作検証 4 の対象)。
var cosmosSessionsContainerName = 'sessions'
var cosmosApprovalsContainerName = 'approvals'
var cosmosCheckpointsContainerName = 'checkpoints'
var cosmosAiAgentContainers = [
  {
    // 会話セッション (ChatHistory)。寿命 7 日 = 1 回の「座り」の目安 (#188)
    name: cosmosSessionsContainerName
    partitionKeyPath: '/id'
    defaultTtl: 604800
  }
  {
    // 承認レコード。寿命 1 時間 = 承認 1 往復の目安 (#188)
    name: cosmosApprovalsContainerName
    partitionKeyPath: '/id'
    defaultTtl: 3600
  }
  {
    // MAF workflow checkpoint。承認レコードと同寿命 (どちらか欠けたら /approve は
    // 再開不能なので寿命を揃える)。partition key は MAF CosmosCheckpointStorage の
    // 仕様で /workflow_name 固定。解決済み分は ai-agent が即時 delete し、
    // 残り (完了 run の checkpoint 等) はこの TTL が掃除する
    name: cosmosCheckpointsContainerName
    partitionKeyPath: '/workflow_name'
    defaultTtl: 3600
  }
]

// -------------------- AI Agent Container App params --------------------
// NOTE: ACR は廃止（#67 / ADR 0013）。image は GitHub Actions で ghcr に事前ビルドし、
// Container App は ghcr の public image を pull する（待機 ¥750/月 の ACR を除去）。
@description('Enable AI Agent on Azure Container Apps.')
param enableAiAgentAca bool = false

@description('Container App name for AI Agent.')
param aiAgentContainerAppName string = toLower('ca-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-ai-agent')

// -------------------- VOICEVOX Wrapper Container App params --------------------
@description('Enable VOICEVOX Wrapper on Azure Container Apps.')
param enableVoicevoxWrapperAca bool = false

@description('Container App name for VOICEVOX Wrapper.')
param voicevoxWrapperContainerAppName string = toLower('ca-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-vv-wrap')

// -------------------- ghcr image 座標 (ai-agent / vv-wrap 共通) --------------------
// #188 で両 Container App をテンプレートに取り込んだ。image は build-images.yml が
// ghcr へ事前ビルド済みのものを pull する (ADR 0013 / ADR 0025)。
@description('ghcr の image 座標のベース (ghcr.io/<owner>/<repo>)。この下に /ai-agent, /voicevox-wrapper がある。')
param ghcrImageRepository string = 'ghcr.io/yomote/mind-inbox'

// ADR 0025: 反映の主経路は **不変 sha タグ** (CD が build-images の sha-<full-sha> を
// provision.sh 経由でここに渡す)。既定 latest は「初回 bootstrap を CD 外で叩いた」とき
// のフォールバック。bicep を**単独適用**すると稼働中の sha タグがこの値へ戻り得るが、
// 直後に deploy-*.sh が走れば sha タグへ再収束する (provision.sh の順序 /
// aiAgentBaseUrl の再結線と同じ「bicep 適用後は deploy スクリプトを続けて実行」の規律)。
@description('ai-agent / voicevox-wrapper の image タグ。CD は sha-<full-sha> を渡す (ADR 0025)。')
param containerImageTag string = 'latest'

var aiAgentImage = '${ghcrImageRepository}/ai-agent:${containerImageTag}'
var voicevoxWrapperImage = '${ghcrImageRepository}/voicevox-wrapper:${containerImageTag}'

@allowed([
  'B1'
  'S1'
  'Y1'
  'EP1'
])
@description('Functions plan SKU. Use Y1 (Consumption) to avoid ElasticPremium quota requirements. Use EP1 only if you have quota and need VNet Integration.')
param functionPlanSkuName string = 'Y1'

@description('Enable VNet integration for the Function App. Requires a plan that supports VNet Integration (e.g., EP1).')
param enableFunctionVnetIntegration bool = false

@description('VNet name')
param vnetName string = 'vnet-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}'

@description('VNet address space')
param vnetAddressPrefix string = '10.10.0.0/16'

@description('Subnet for Private Endpoints')
param peSubnetName string = 'snet-${environmentName}-pe'
param peSubnetPrefix string = '10.10.10.0/24'

@description('Subnet reserved for app integration (Functions/App Service VNet Integration etc.)')
param appSubnetName string = 'snet-${environmentName}-app'
param appSubnetPrefix string = '10.10.20.0/24'

@description('Log Analytics workspace name')
param lawName string = 'law-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-ops'

@description('Provision the Azure SQL stack (SQL Server/DB + admin Key Vault + Private Endpoint/DNS/VNet link + diagnostics). Default false: v1 はアプリが SQL を一切参照せず in-memory のみで動く (ADR 0013)。永続化が要る Phase 2 (Redis + Cosmos) で必要になれば true にする。')
param enableSql bool = false

@description('SQL server name (must be globally unique)')
param sqlServerName string = toLower('sql-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('SQL admin login')
param sqlAdminUser string = 'sqladmin'

@description('Name of the Azure Key Vault that stores the SQL administrator password (lowercase, 3-24 chars).')
param sqlAdminKeyVaultName string = toLower('kv-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-sql')

@description('Name of the Key Vault secret that stores the SQL administrator password.')
param sqlAdminPasswordSecretName string = 'sql-admin-password'

@description('SQL database name')
param sqlDatabaseName string = 'sqldb-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}'

@allowed([
  'Basic'
  'S0'
  'S1'
  'S2'
])
@description('DB SKU (simple DTU model for starters)')
param sqlDbSkuName string = 'S0'

@secure()
@description('SQL admin password. If omitted, a GUID-based value is auto-generated per deployment.')
param sqlAdminPassword string = newGuid()

// -------------------- Log Analytics --------------------
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    sku: {
      name: 'PerGB2018'
    }
  }
}

// -------------------- VNet --------------------
// 使い道は enableSql (SQL Private Endpoint) と enableFunctionVnetIntegration の 2 つだけ。
// どちらも false (dev の既定) なら作らない — 未使用の VNet が構成図に浮くのを防ぐ。
// Container Apps 環境はこの VNet を使わない (既定のマネージドネットワークで動く)。
var vnetEnabled = enableSql || enableFunctionVnetIntegration

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = if (vnetEnabled) {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: peSubnetName
        properties: {
          addressPrefix: peSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: appSubnetName
        properties: union(
          {
            addressPrefix: appSubnetPrefix
          },
          enableFunctionVnetIntegration
            ? {
                delegations: [
                  {
                    name: 'delegation-web'
                    properties: {
                      serviceName: 'Microsoft.Web/serverFarms'
                    }
                  }
                ]
              }
            : {}
        )
      }
    ]
  }
}

// 条件リソース (vnet) のプロパティ参照を避け、resourceId() で組み立てる
// (SPEECH_RESOURCE_ID / COSMOS_ENDPOINT と同じ流儀)。
var peSubnetResourceId = resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, peSubnetName)
var appSubnetResourceId = resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, appSubnetName)

// -------------------- Private DNS Zone (SQL) --------------------
var sqlServerHostnameSuffix = startsWith(environment().suffixes.sqlServerHostname, '.')
  ? substring(environment().suffixes.sqlServerHostname, 1)
  : environment().suffixes.sqlServerHostname

var sqlPrivateDnsZoneName = 'privatelink.${sqlServerHostnameSuffix}'

resource sqlPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = if (enableSql) {
  name: sqlPrivateDnsZoneName
  location: 'global'
}

resource sqlPrivateDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = if (enableSql) {
  parent: sqlPrivateDnsZone
  name: '${vnetName}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: resourceId('Microsoft.Network/virtualNetworks', vnetName)
    }
    registrationEnabled: false
  }
  dependsOn: [
    vnet
  ]
}

// -------------------- Key Vault for SQL admin password --------------------
@description('Set to true if a soft-deleted Key Vault with the same name already exists and needs to be recovered.')
param recoverSqlAdminKeyVault bool = false

resource sqlAdminKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' = if (enableSql) {
  name: sqlAdminKeyVaultName
  location: location
  properties: union(
    {
      tenantId: subscription().tenantId
      sku: {
        family: 'A'
        name: 'standard'
      }
      enableRbacAuthorization: true
      publicNetworkAccess: 'Enabled'
      softDeleteRetentionInDays: 90
      enablePurgeProtection: true
    },
    recoverSqlAdminKeyVault ? { createMode: 'recover' } : {}
  )
}

resource sqlAdminPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (enableSql) {
  parent: sqlAdminKeyVault
  name: sqlAdminPasswordSecretName
  properties: {
    value: sqlAdminPassword
  }
}

// -------------------- Azure SQL Server & DB --------------------
resource sqlServer 'Microsoft.Sql/servers@2024-05-01-preview' = if (enableSql) {
  name: sqlServerName
  location: location
  properties: {
    administratorLogin: sqlAdminUser
    administratorLoginPassword: sqlAdminPassword
    version: '12.0'
    publicNetworkAccess: 'Disabled' // important
    minimalTlsVersion: '1.2'
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2024-05-01-preview' = if (enableSql) {
  parent: sqlServer
  name: sqlDatabaseName
  location: location
  sku: {
    name: sqlDbSkuName
    tier: (sqlDbSkuName == 'Basic') ? 'Basic' : 'Standard'
  }
  properties: {
    collation: 'Japanese_CI_AS'
  }
}

// -------------------- Private Endpoint for SQL Server --------------------
// groupId for SQL Server private link is typically "sqlServer"
resource sqlPe 'Microsoft.Network/privateEndpoints@2024-01-01' = if (enableSql) {
  name: 'pe-${sqlServerName}'
  location: location
  properties: {
    subnet: {
      id: peSubnetResourceId
    }
    privateLinkServiceConnections: [
      {
        name: 'pls-sql'
        properties: {
          privateLinkServiceId: sqlServer.id
          groupIds: [
            'sqlServer'
          ]
        }
      }
    ]
  }
  dependsOn: [
    vnet
  ]
}

// DNS Zone Group to auto-create A records in privatelink zone
resource sqlPeDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (enableSql) {
  parent: sqlPe
  name: 'pdzg-sql'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cfg'
        properties: {
          privateDnsZoneId: sqlPrivateDnsZone.id
        }
      }
    ]
  }
}

// -------------------- Diagnostic settings (example) --------------------
// Categories differ by resource & configuration.
// You can list them with:
//   az monitor diagnostic-settings categories list --resource <resourceId> -o table
//
// Below is a SAFE skeleton: enable a few typical categories once you confirm names.

resource diagSqlServer 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (enableSql) {
  name: 'diag-${sqlServerName}'
  scope: sqlServer
  properties: {
    workspaceId: law.id
    logs: [
      // Replace categories with ones available in your tenant
      // { category: 'SQLSecurityAuditEvents', enabled: true }
      // { category: 'DevOpsOperationsAudit', enabled: true }
    ]
    metrics: [
      // Example:
      // { category: 'AllMetrics', enabled: true }
    ]
  }
}

// -------------------- Azure Functions (App Service Plan + Function App) --------------------
resource functionStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: functionStorageAccountName
  location: functionLocation
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

var functionStorageKeys = functionStorage.listKeys()
var functionStorageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${functionStorage.name};AccountKey=${functionStorageKeys.keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
var functionContentShare = toLower('content-${replace(functionAppName, '_', '-')}-func')

resource functionPlan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: functionPlanName
  location: functionLocation
  kind: (functionPlanSkuName == 'Y1') ? 'functionapp' : 'linux'
  sku: (functionPlanSkuName == 'Y1')
    ? {
        name: 'Y1'
        tier: 'Dynamic'
      }
    : (functionPlanSkuName == 'EP1')
        ? {
            name: 'EP1'
            tier: 'ElasticPremium'
            capacity: 1
          }
        : (functionPlanSkuName == 'S1')
            ? {
                name: 'S1'
                tier: 'Standard'
              }
            : {
                name: 'B1'
                tier: 'Basic'
              }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-11-01' = {
  name: functionAppName
  location: functionLocation
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: union(
    {
      serverFarmId: functionPlan.id
      httpsOnly: true
      siteConfig: union(
        {
          linuxFxVersion: 'Node|22'
          minTlsVersion: '1.2'
          ftpsState: 'Disabled'
          // SWA Free 化でフロントは別オリジンになるため CORS が要る (#69)。
          // 注意: CORS はブラウザ側の規約であって認可ではない。守りは EasyAuth の 401。
          cors: {
            allowedOrigins: union(
              ['https://${staticSite.properties.defaultHostname}'],
              functionExtraCorsOrigins
            )
            supportCredentials: false
          }
          // 注意: bicep の appSettings は**全置換**。ここに無い設定は再デプロイで消えるため、
          // 恒久設定は az で後付けせず必ずここで宣言する (#94/#107 と同族の教訓)。
          // AI_AGENT_BASE_URL / VOICEVOX_BASE_URL もパラメータで宣言する (下記)。
          // deploy-backend.sh の再結線は FQDN 変化への追従・フォールバックとして残る。
          appSettings: concat(
            [
              {
                name: 'AzureWebJobsStorage'
                value: functionStorageConnectionString
              }
              {
                name: 'FUNCTIONS_EXTENSION_VERSION'
                value: '~4'
              }
              {
                name: 'FUNCTIONS_WORKER_RUNTIME'
                value: 'node'
              }
              {
                name: 'WEBSITE_NODE_DEFAULT_VERSION'
                value: '~22'
              }
              {
                name: 'WEBSITE_RUN_FROM_PACKAGE'
                value: '1'
              }
              {
                name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING'
                value: functionStorageConnectionString
              }
              {
                name: 'WEBSITE_CONTENTSHARE'
                value: functionContentShare
              }
            ],
            // 認証の門 (ADR 0017 / #86) の audience。BFF はこの audience の
            // Managed Identity トークンを取得して下流 Container Apps を呼ぶ (#104)
            empty(containerAppsGateClientId)
              ? []
              : [
                  {
                    name: 'AI_AGENT_AUDIENCE'
                    value: 'api://${containerAppsGateClientId}'
                  }
                  {
                    name: 'VOICEVOX_AUDIENCE'
                    value: 'api://${containerAppsGateClientId}'
                  }
                ],
            // 下流 base URL。enable*Aca=true ならテンプレート内 Container App の FQDN を
            // 直接参照する (#188 — 古い FQDN が bicep 単独適用で復活する問題はこれで消えた)。
            // false ならパラメータ値のフォールバック (param 定義箇所のコメント参照)。
            empty(effectiveAiAgentBaseUrl)
              ? []
              : [
                  {
                    name: 'AI_AGENT_BASE_URL'
                    value: effectiveAiAgentBaseUrl
                  }
                ],
            empty(effectiveVoicevoxWrapperBaseUrl)
              ? []
              : [
                  {
                    name: 'VOICEVOX_BASE_URL'
                    value: effectiveVoicevoxWrapperBaseUrl
                  }
                ],
            // Azure Speech (ADR 0023)。未設定なら BFF の speech.issueToken が
            // available:false を返し、フロントは Web Speech にフォールバックする。
            enableSpeech
              ? [
                  {
                    name: 'SPEECH_RESOURCE_ID'
                    value: resourceId('Microsoft.CognitiveServices/accounts', speechAccountName)
                  }
                  {
                    name: 'SPEECH_REGION'
                    value: speechLocation
                  }
                ]
              : [],
            // Cosmos DB (ADR 0030)。**COSMOS_ENDPOINT の有無が BFF の分岐点** — 未設定なら
            // in-memory リポジトリのまま動く (ローカル / テストの既定 = D7)。
            // キー・接続文字列は渡さない (disableLocalAuth: true / D3)。エンドポイントは
            // アカウント名から決まる固定形なので、条件リソースを参照せず組み立てる
            // (SPEECH_RESOURCE_ID が resourceId() を使うのと同じ流儀)。
            enableCosmos
              ? [
                  {
                    name: 'COSMOS_ENDPOINT'
                    value: 'https://${cosmosAccountName}.documents.azure.com:443/'
                  }
                  {
                    name: 'COSMOS_DATABASE'
                    value: cosmosDatabaseName
                  }
                  {
                    name: 'COSMOS_PROBLEMS_CONTAINER'
                    value: cosmosProblemsContainerName
                  }
                  {
                    name: 'COSMOS_HISTORY_CONTAINER'
                    value: cosmosHistoryContainerName
                  }
                ]
              : []
          )

        },
        enableFunctionVnetIntegration ? { vnetRouteAllEnabled: true } : {}
      )
    },
    enableFunctionVnetIntegration ? { virtualNetworkSubnetId: appSubnetResourceId } : {}
  )
  // VNet への依存が要るのは VNet 統合を有効にした時だけ (そのとき vnetEnabled も true)。
  // ソースは意図を示すため条件分岐形を保つこと (無条件 [vnet] に「単純化」しない)。
  // 注意: ARM JSON には条件付き dependsOn が存在しないため、bicep build はこの三項式を
  // 無条件の dependsOn に平坦化して emit する (2026-08-10 に最小 repro で実測)。
  // それでもデプロイが安全なのは ARM の仕様による:
  // "When a conditional resource isn't deployed, Azure Resource Manager automatically
  //  removes it from the required dependencies." (learn.microsoft.com resource-dependency)
  dependsOn: enableFunctionVnetIntegration ? [vnet] : []
}

// VOICEVOX tier(ADR 0010) による実効値。gpu 以外は Consumption ベースの CPU 構成にフォールバック。
var voicevoxIsGpu = voicevoxTier == 'gpu'
var voicevoxEffectiveImage = voicevoxIsGpu ? voicevoxImage : 'voicevox/voicevox_engine:cpu-latest'
var voicevoxEffectiveCpu = voicevoxIsGpu ? voicevoxCpu : 2
var voicevoxEffectiveMemory = voicevoxIsGpu ? voicevoxMemory : '4Gi'
// GPU 時のみ GPU ワークロードプロファイルを足す。cpu 時は組み込みの Consumption だけ使う
// （ACA の Consumption は環境に1つの特別枠。CPU アプリは workloadProfileName を指定しない）。
var voicevoxGpuWorkloadProfiles = [
  {
    name: 'Consumption'
    workloadProfileType: 'Consumption'
  }
  {
    name: voicevoxWorkloadProfileName
    workloadProfileType: voicevoxWorkloadProfileType
  }
]

// 共有 CAE (#68): ai-agent / voicevox / vv-wrapper が 1 つの環境に相乗りする。
// どれか 1 つでも有効なら作る。VOICEVOX が gpu tier のときだけ GPU ワークロードプロファイルを足す
// （CPU 側のアプリは workloadProfileName を指定せず組み込みの Consumption を使うので影響なし）。
var containerAppsEnabled = enableVoicevoxAca || enableAiAgentAca || enableVoicevoxWrapperAca

resource sharedManagedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = if (containerAppsEnabled) {
  name: containerAppsEnvironmentName
  location: containerAppsLocation
  properties: union(
    {
      appLogsConfiguration: {
        destination: 'log-analytics'
        logAnalyticsConfiguration: {
          customerId: law.properties.customerId
          sharedKey: law.listKeys().primarySharedKey
        }
      }
    },
    (enableVoicevoxAca && voicevoxIsGpu) ? { workloadProfiles: voicevoxGpuWorkloadProfiles } : {}
  )
}

resource voicevoxContainerApp 'Microsoft.App/containerApps@2024-03-01' = if (enableVoicevoxAca) {
  name: voicevoxContainerAppName
  // Container App は環境と同じリージョンに置く必要があるため、共有 CAE の location に揃える (#68)。
  location: containerAppsLocation
  properties: union(
    {
      managedEnvironmentId: sharedManagedEnvironment.id
      configuration: {
        activeRevisionsMode: 'Single'
        // internal ingress (#86 / ADR 0017)。VOICEVOX エンジンの呼び出し元は
        // **同じ CAE に居る vv-wrapper だけ**なので、外部 ingress を持つ理由が無い。
        // external: false にすると FQDN が `*.internal.<region>.azurecontainerapps.io` になり、
        // 環境の外からは名前解決すらできない = 到達経路そのものが消える。
        //
        // IP 許可リストは採らない。一度 `sharedManagedEnvironment.properties.staticIp` を
        // 許可する形で実装したが、実測の結果それは **inbound 用の IP** (4.190.8.64) で、
        // 呼び出し元の **egress IP** (outboundIpAddresses = 130.33.178.89) とは別物だった。
        // 正しい egress IP は各 Container App の outboundIpAddresses にしか無く、
        // 自リソース定義内から自分の property を参照するのは循環参照になるため書けない。
        // ADR 0017 が Option D (IP 許可リスト) を却下した理由でもある。
        //
        // vv-wrapper 側の接続先は deployment output `voicevoxBaseUrl` から毎回配線される
        // (provision.sh が bootstrap → deploy-voicevox-wrapper.sh の順で走る) ため、
        // internal 化で FQDN が変わっても次のデプロイで追随する。
        ingress: {
          external: false
          targetPort: 50021
          transport: 'http'
        }
      }
      template: {
        containers: [
          {
            name: 'voicevox-engine'
            image: voicevoxEffectiveImage
            env: [
              {
                name: 'MALLOC_ARENA_MAX'
                value: '2'
              }
            ]
            resources: {
              cpu: voicevoxEffectiveCpu
              memory: voicevoxEffectiveMemory
            }
          }
        ]
        scale: {
          minReplicas: voicevoxMinReplicas
          maxReplicas: voicevoxMaxReplicas
        }
      }
    },
    voicevoxIsGpu ? { workloadProfileName: voicevoxWorkloadProfileName } : {}
  )
}

// -------------------- Azure OpenAI --------------------
@description('Set to true if a soft-deleted Cognitive Services account with the same name already exists.')
param restoreOpenAiAccount bool = false

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
    restoreOpenAiAccount ? { restore: true } : {}
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

// -------------------- Azure Speech (ADR 0023 / #121) --------------------
// 音声入力のサーバー STT。認証は静的シークレット 0 (ADR 0006 系譜):
//   - disableLocalAuth: true でサブスクリプションキーを無効化
//   - BFF (Functions) の System Assigned MI に Speech User ロールを付与し、
//     BFF が Entra トークン由来の一時トークン (aad#...) をフロントへ発行する
// F0 は超過時「課金」ではなく「停止」なので予算事故が構造的に起きない (ADR 0013 と両立)。
resource speechAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' = if (enableSpeech) {
  name: speechAccountName
  location: speechLocation
  kind: 'SpeechServices'
  sku: {
    name: speechSkuName
  }
  properties: {
    // Entra 認証 (aad# authorization token) には custom subdomain が必須
    customSubDomainName: speechAccountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

// Cognitive Services Speech User — トークン取得 + Speech 利用の最小ロール
var cognitiveServicesSpeechUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'f2dc8367-1007-4938-bd23-fe263f013447'
)

resource speechRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableSpeech) {
  name: guid(resourceGroup().id, speechAccountName, functionAppName, 'speech-user')
  scope: speechAccount
  properties: {
    roleDefinitionId: cognitiveServicesSpeechUserRoleId
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// -------------------- AI Agent / VOICEVOX Wrapper Container Apps (#188) --------------------
// 以前は「デプロイスクリプトが作成/更新する (テンプレート外)」だったが、#188 で取り込んだ。
// 理由: 宣言が無いと SystemAssigned MI を安定して付けられず (Cosmos の data plane ロールの
// 前提)、auth gate 等の手動設定が再デプロイで剥がれる (debrief #2 で voicevox の設定が
// 3 回消えた事例 / ADR 0017「誰が宣言しているか」)。
// image の反映主経路は従来どおり deploy-*.sh の sha タグ差し替え (ADR 0025) — bicep は
// containerImageTag パラメータで同じタグを宣言し、単独適用時のロールバック窓は
// 直後の deploy-*.sh 実行で収束する (containerImageTag の param コメント参照)。

var aiAgentEnvVars = concat(
  [
    {
      name: 'AZURE_OPENAI_ENDPOINT'
      value: enableOpenAi ? (openAiAccount.?properties.?endpoint ?? '') : ''
    }
    {
      name: 'AZURE_OPENAI_DEPLOYMENT'
      value: openAiDeploymentName
    }
    {
      name: 'USE_MANAGED_IDENTITY'
      value: 'true'
    }
    {
      name: 'LOG_LEVEL'
      value: 'INFO'
    }
  ],
  // Cosmos (ADR 0030 / #188)。**COSMOS_ENDPOINT の有無が ai-agent の分岐点** — 未設定なら
  // in-memory のまま動く (BFF と同じ流儀)。キーは渡さない (disableLocalAuth: true / D3)。
  enableCosmos
    ? [
        {
          name: 'COSMOS_ENDPOINT'
          value: 'https://${cosmosAccountName}.documents.azure.com:443/'
        }
        {
          name: 'COSMOS_DATABASE'
          value: cosmosDatabaseName
        }
        {
          name: 'COSMOS_SESSIONS_CONTAINER'
          value: cosmosSessionsContainerName
        }
        {
          name: 'COSMOS_APPROVALS_CONTAINER'
          value: cosmosApprovalsContainerName
        }
        {
          name: 'COSMOS_CHECKPOINTS_CONTAINER'
          value: cosmosCheckpointsContainerName
        }
      ]
    : []
)

resource aiAgentContainerApp 'Microsoft.App/containerApps@2024-03-01' = if (enableAiAgentAca) {
  name: aiAgentContainerAppName
  location: containerAppsLocation
  // SystemAssigned MI: OpenAI (Cognitive Services OpenAI User) と Cosmos
  // (Built-in Data Contributor) の data plane 認証に使う。静的シークレット 0 の系譜。
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: sharedManagedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      // external ingress + 組み込み認証の門 (ADR 0017 / 下の authConfig)。
      // IP 許可リストには戻さない。
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'ai-agent'
          image: aiAgentImage
          env: aiAgentEnvVars
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      // scale-to-zero (ADR 0002)。値は deploy-ai-agent.sh の従来値と同一。
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}

resource voicevoxWrapperContainerApp 'Microsoft.App/containerApps@2024-03-01' = if (enableVoicevoxWrapperAca) {
  name: voicevoxWrapperContainerAppName
  location: containerAppsLocation
  // 今は MI を使う相手が居ないが、ai-agent と宣言を揃える (identity を宣言しないと
  // 後から手動付与した MI が bicep 再適用で剥がれる)。
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: sharedManagedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'voicevox-wrapper'
          image: voicevoxWrapperImage
          env: [
            {
              // エンジンは internal ingress (#86) — 同じ CAE 内の FQDN を直接参照する
              name: 'VOICEVOX_ENGINE_BASE_URL'
              value: enableVoicevoxAca
                ? 'https://${voicevoxContainerApp.?properties.?configuration.?ingress.?fqdn ?? ''}'
                : ''
            }
            {
              name: 'LOG_LEVEL'
              value: 'INFO'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
}

// 組み込み認証の門 (ADR 0017 / #86)。deploy-*.sh の `az containerapp auth` と同内容を
// 宣言に固定する — 手動適用だけだと環境再作成で剥がれる (#188 の動機)。
// gate app (containerAppsGateClientId) が未設定なら作らない (スクリプト側が WARN を出す)。
resource aiAgentAuthConfig 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (enableAiAgentAca && !empty(containerAppsGateClientId)) {
  parent: aiAgentContainerApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'Return401'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: 'https://login.microsoftonline.com/${functionAuthEntraTenantId}/v2.0'
          clientId: containerAppsGateClientId
        }
        validation: {
          allowedAudiences: [
            'api://${containerAppsGateClientId}'
            containerAppsGateClientId
          ]
        }
      }
    }
  }
}

resource voicevoxWrapperAuthConfig 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (enableVoicevoxWrapperAca && !empty(containerAppsGateClientId)) {
  parent: voicevoxWrapperContainerApp
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'Return401'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: 'https://login.microsoftonline.com/${functionAuthEntraTenantId}/v2.0'
          clientId: containerAppsGateClientId
        }
        validation: {
          allowedAudiences: [
            'api://${containerAppsGateClientId}'
            containerAppsGateClientId
          ]
        }
      }
    }
  }
}

// BFF が呼ぶ下流 base URL の実効値 (#188)。enable*Aca=true ならテンプレート内の FQDN。
var effectiveAiAgentBaseUrl = enableAiAgentAca
  ? 'https://${aiAgentContainerApp.?properties.?configuration.?ingress.?fqdn ?? ''}'
  : aiAgentBaseUrl
var effectiveVoicevoxWrapperBaseUrl = enableVoicevoxWrapperAca
  ? 'https://${voicevoxWrapperContainerApp.?properties.?configuration.?ingress.?fqdn ?? ''}'
  : voicevoxWrapperBaseUrl

// Cognitive Services OpenAI User — ai-agent MI の LLM 呼び出し (deploy-ai-agent.sh が
// az role assignment で足していたものを宣言に固定)。
var cognitiveServicesOpenAiUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
)

// 名前は guid() の決定的計算 = 宣言と実体が常に一致する (#297)。実行時パラメータで
// 既存名を渡す「養子縁組」(#262 / #278) は撤去した — 渡し続けないと再発する恒久的な依存で、
// 実際に渡し損ねて再発したため。スクリプト時代の手動割り当てが残っている環境では、
// **1 回だけ人手で削除**してから流す (割り当ての一意性は名前ではなく principal+role+scope
// なので、別名の残骸があると ARM が RoleAssignmentExists で拒否する / Issue #297)。
resource aiAgentOpenAiRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableOpenAi && enableAiAgentAca) {
  name: guid(resourceGroup().id, openAiAccountName, aiAgentContainerAppName, 'openai-user')
  scope: openAiAccount
  properties: {
    roleDefinitionId: cognitiveServicesOpenAiUserRoleId
    principalId: aiAgentContainerApp.?identity.?principalId ?? ''
    principalType: 'ServicePrincipal'
  }
}

// -------------------- Cosmos DB (ADR 0030 / #165) --------------------
// Problem / 履歴の永続化ストア。FR-4「再起動・再ログインで消えない」の実体。
//
// 守りの構成 (ADR 0030 D3):
//   - disableLocalAuth: true でアカウントキーを**無効化**する。接続文字列は
//     app settings にも Key Vault にも置かない = 「鍵が漏れる経路」が構造的に無い
//   - data plane は Functions の System Assigned MI + 組み込みロールのみ (下の sqlRoleAssignment)
//   - Private Endpoint は張れない (VNet が要るが Functions Y1 は VNet 統合非対応 / ADR 0017)。
//     守りは disableLocalAuth + Entra RBAC の 1 層に依存する — これは ADR 0030 が
//     Negative Consequence として明示的に受け入れたトレードオフ
//
// コスト (ADR 0013 の予算 ¥3,000/月):
//   - enableCosmosFreeTier=true は**作成時オプトイン専用**。枠が消費済みならデプロイが失敗し、
//     知らないうちに課金構成で作られることがない (「デプロイ成功 = 無料枠取得成功」)
//   - totalThroughputLimit で無料枠 1,000 RU/s を超えられないようにする
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = if (enableCosmos) {
  name: cosmosAccountName
  // 保管リージョンは Japan East 固定 (ADR 0030 D6 / NFR-1)。Functions (East Asia) との
  // クロスリージョン往復は増えるが、呼び出しは全件一覧か id 点引きだけで回数が少ない。
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
      // 無料枠は serverless 対象外。serverless に倒すときは必ず false になる。
      enableFreeTier: enableCosmosFreeTier && !cosmosServerless
      // D3: アカウントキーを殺す。これ以降、data plane は Entra トークンでしか通らない。
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
    // serverless に totalThroughputLimit は指定できない (プロビジョニング概念が無い)。
    cosmosServerless
      ? {}
      : {
          capacity: {
            totalThroughputLimit: cosmosTotalThroughputLimit
          }
        }
  )
}

// DB は共有スループット。コンテナ 2 つで別々に RU を確保すると最小 400×2 になり
// 無料枠でも上限に近づくため、DB レベルで 1 本にまとめる。
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

// パーティションキーは `/userId` (ADR 0030 D5)。クエリは「自分の全件」と「id 点引き」の
// 2 種類しか無く、クロスパーティションクエリが 1 つも発生しない。
// コンテナ名は上の cosmosContainerNames が唯一の定義 (app settings と共有する)。
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
        // TTL は既定オフ (-1 でも 0 でもない = 未設定)。Problem も履歴も消してはいけない。
        // 短命データ (会話セッション / 承認レコード / checkpoint) は下の TTL 付き
        // コンテナ群 (#188 / D4 の見直し) — こちらと混ぜないこと。
      }
    }
  }
]

// ai-agent 側の短命データ用 TTL コンテナ (#188)。定義は cosmosAiAgentContainers 参照。
// @batchSize(1) + dependsOn: Cosmos の control plane は同一アカウントへの並行操作で
// 409/429 になることがあるため、既存コンテナ群に続けて 1 つずつ作る。
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

// Cosmos DB Built-in Data Contributor (data plane)。read/write は許すが、
// DB / コンテナの作成・削除 (control plane) はできない。器の宣言は bicep が持つ。
//
// これは `Microsoft.Authorization/roleAssignments` (control plane) とは**別の仕組み**で、
// Portal の「アクセス制御 (IAM)」には出てこない。ここを control plane の共同作成者で
// 代用しようとしても data plane は通らない。
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'
var cosmosAccountResourceId = resourceId('Microsoft.DocumentDB/databaseAccounts', cosmosAccountName)

resource cosmosDataRoleAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (enableCosmos) {
  parent: cosmosAccount
  // 名前は GUID 必須。同じ (アカウント, プリンシパル, ロール) で冪等になるよう guid() で作る。
  name: guid(cosmosAccountName, functionAppName, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccountResourceId}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: functionApp.identity.principalId
    // アカウント全体。BFF は problems / history を触るので分ける意味が無い。
    scope: cosmosAccountResourceId
  }
}

// ai-agent MI にも同じ data plane ロールを付ける (#188)。スコープは**アカウント全体では
// なく短命データ用コンテナ (sessions / approvals / checkpoints) 単位**に限定する —
// problems / history (機微データ) は ADR 0030 D4 のとおり BFF のみが触る。D4 は覆さず、
// 権限を用途に一致させることで整合させる (PR #261 Codex P1)。
// 経路はキーではなく MI + RBAC で、アカウントキーは disableLocalAuth: true で存在しない。
// 必要操作 (read/upsert/query/delete items) はコンテナスコープの Data Contributor で
// 全て満たされる (コンテナの作成は bicep が担い、実行時に create しない — cosmos.py 参照)。
// NOTE: 既存環境にアカウント全体スコープの旧割り当てが残っている場合、incremental
// デプロイでは消えないので手で削除する (az cosmosdb sql role assignment delete)。
@batchSize(1)
resource cosmosDataRoleAssignmentAiAgent 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [
  for c in cosmosAiAgentContainers: if (enableCosmos && enableAiAgentAca) {
    parent: cosmosAccount
    name: guid(cosmosAccountName, aiAgentContainerAppName, cosmosDataContributorRoleId, c.name)
    properties: {
      roleDefinitionId: '${cosmosAccountResourceId}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
      principalId: aiAgentContainerApp.?identity.?principalId ?? ''
      // コンテナ単位スコープ (/dbs/<db>/colls/<container>)
      scope: '${cosmosAccountResourceId}/dbs/${cosmosDatabaseName}/colls/${c.name}'
    }
    // Cosmos の sqlRoleAssignments は同一アカウントで並列作成できない (409 conflict)
    // ことがあるため、BFF 分に続けて @batchSize(1) で 1 つずつ直列に作る。
    // スコープ先のコンテナが先に存在している必要があるため TTL コンテナ群にも依存。
    dependsOn: [
      cosmosDataRoleAssignment
      cosmosAiAgentTtlContainers
    ]
  }
]

// -------------------- Static Web Apps (frontend) --------------------
var staticSiteRepoProps = (staticSiteRepositoryUrl != '')
  ? {
      repositoryUrl: staticSiteRepositoryUrl
      branch: staticSiteBranch
      repositoryToken: staticSiteRepositoryToken
      buildProperties: {
        appLocation: 'frontend'
        apiLocation: 'backend'
        outputLocation: 'dist'
      }
    }
  : {}

var staticSiteAutoCreateEntraApp = enableStaticSiteEntraAuth && autoCreateStaticSiteEntraAppRegistration
var useManagedIdentityForEntraBootstrap = !empty(staticSiteEntraBootstrapUserAssignedIdentityResourceId) && !empty(staticSiteEntraBootstrapUserAssignedIdentityClientId)

resource staticSite 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticSiteName
  location: staticSiteLocation
  sku: {
    name: staticSiteSkuName
  }
  properties: union(
    {
      allowConfigFileUpdates: true
    },
    staticSiteRepoProps
  )
}

// Optional: automate Entra app registration + client secret creation for SWA auth.
// Requires sufficient Entra permissions for the deployment principal (e.g., App admin).
resource staticSiteEntraAppBootstrap 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (staticSiteAutoCreateEntraApp) {
  name: 'ds-entra-swa-${uniqueString(resourceGroup().id, staticSite.name)}'
  location: location
  kind: 'AzureCLI'
  identity: useManagedIdentityForEntraBootstrap
    ? {
        type: 'UserAssigned'
        userAssignedIdentities: {
          '${staticSiteEntraBootstrapUserAssignedIdentityResourceId}': {}
        }
      }
    : null
  properties: {
    azCliVersion: '2.64.0'
    timeout: 'PT30M'
    cleanupPreference: 'OnSuccess'
    retentionInterval: 'P1D'
    environmentVariables: [
      {
        name: 'APP_NAME'
        value: staticSiteEntraAppDisplayName
      }
      {
        name: 'SWA_HOSTNAME'
        value: staticSite.properties.defaultHostname
      }
      {
        name: 'TENANT_ID'
        value: staticSiteEntraTenantId
      }
      {
        name: 'SECRET_YEARS'
        value: string(staticSiteEntraAppSecretYears)
      }
      {
        name: 'UAMI_CLIENT_ID'
        value: staticSiteEntraBootstrapUserAssignedIdentityClientId
      }
    ]
    scriptContent: '''
      set -euo pipefail

      if [ -z "${UAMI_CLIENT_ID:-}" ]; then
        echo "ERROR: staticSiteEntraBootstrapUserAssignedIdentityClientId is required when autoCreateStaticSiteEntraAppRegistration=true" >&2
        exit 1
      fi

      az login --identity --username "$UAMI_CLIENT_ID" --allow-no-subscriptions >/dev/null

      APP_ID="$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)"
      if [ -z "$APP_ID" ]; then
        APP_ID="$(az ad app create \
          --display-name "$APP_NAME" \
          --sign-in-audience AzureADMyOrg \
          --query appId -o tsv)"
      fi

      CALLBACK_URL="https://$SWA_HOSTNAME/.auth/login/aad/callback"
      LOGOUT_CALLBACK_URL="https://$SWA_HOSTNAME/.auth/logout/aad/callback"
      APP_OBJECT_ID="$(az ad app show --id "$APP_ID" --query id -o tsv)"
      az ad app update \
        --id "$APP_ID" \
        --web-redirect-uris "$CALLBACK_URL" \
        --enable-id-token-issuance true >/dev/null

      PATCH_BODY="{\"web\":{\"redirectUris\":[\"$CALLBACK_URL\"],\"logoutUrl\":\"$LOGOUT_CALLBACK_URL\",\"implicitGrantSettings\":{\"enableIdTokenIssuance\":true}}}"
      az rest \
        --method PATCH \
        --url "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
        --headers "Content-Type=application/json" \
        --body "$PATCH_BODY" >/dev/null

      az ad sp show --id "$APP_ID" >/dev/null 2>&1 || az ad sp create --id "$APP_ID" >/dev/null

      SECRET_NAME="swa-auth-$(date +%Y%m%d%H%M%S)"
      CLIENT_SECRET="$(az ad app credential reset \
        --id "$APP_ID" \
        --append \
        --display-name "$SECRET_NAME" \
        --years "$SECRET_YEARS" \
        --query password -o tsv)"

      cat > "$AZ_SCRIPTS_OUTPUT_PATH" <<JSON
      {
        "clientId": "$APP_ID",
        "clientSecret": "$CLIENT_SECRET",
        "tenantId": "$TENANT_ID",
        "appObjectId": "$APP_OBJECT_ID"
      }
      JSON
    '''
  }
}

var autoCreatedStaticSiteEntraClientId = staticSiteAutoCreateEntraApp
  ? string(staticSiteEntraAppBootstrap!.properties.outputs.clientId)
  : ''

var autoCreatedStaticSiteEntraClientSecret = staticSiteAutoCreateEntraApp
  ? string(staticSiteEntraAppBootstrap!.properties.outputs.clientSecret)
  : ''

var autoCreatedStaticSiteEntraTenantId = staticSiteAutoCreateEntraApp
  ? string(staticSiteEntraAppBootstrap!.properties.outputs.tenantId)
  : staticSiteEntraTenantId

var autoCreatedStaticSiteEntraAppObjectId = staticSiteAutoCreateEntraApp
  ? string(staticSiteEntraAppBootstrap!.properties.outputs.appObjectId)
  : ''

var effectiveStaticSiteEntraClientId = staticSiteAutoCreateEntraApp
  ? autoCreatedStaticSiteEntraClientId
  : staticSiteEntraClientId

var effectiveStaticSiteEntraClientSecret = staticSiteAutoCreateEntraApp
  ? autoCreatedStaticSiteEntraClientSecret
  : staticSiteEntraClientSecret

var effectiveStaticSiteEntraTenantId = staticSiteAutoCreateEntraApp
  ? autoCreatedStaticSiteEntraTenantId
  : staticSiteEntraTenantId

var staticSiteEntraAuthEnabled = enableStaticSiteEntraAuth && (staticSiteAutoCreateEntraApp || (!empty(staticSiteEntraClientId) && !empty(staticSiteEntraClientSecret)))

// App settings consumed by frontend/staticwebapp.config.json for Entra ID built-in auth.
resource staticSiteAppSettings 'Microsoft.Web/staticSites/config@2023-12-01' = if (staticSiteEntraAuthEnabled) {
  parent: staticSite
  name: 'appsettings'
  properties: {
    AZURE_CLIENT_ID: effectiveStaticSiteEntraClientId
    AZURE_CLIENT_SECRET: effectiveStaticSiteEntraClientSecret
    AZURE_TENANT_ID: effectiveStaticSiteEntraTenantId
  }
}

// Link the Function App as the SWA backend (so /api/* routes to Azure Functions)
resource staticSiteBackend 'Microsoft.Web/staticSites/linkedBackends@2025-03-01' = if (staticSiteSkuName == 'Standard') {
  parent: staticSite
  name: 'api'
  properties: {
    backendResourceId: functionApp.id
    region: functionLocation
  }
}

// -------------------- Function App Authentication (EasyAuth) --------------------
// 認可の門はここ (#69 / ADR 0013)。SWA Free には linked backend が無く、フロントは
// 別オリジンから直叩きするため、SWA 側の認証では API を守れない。課金の芯 (OpenAI) は
// この Functions の先にあるので、未認証は関数コードが起動する前に 401 で弾く。
// 単一テナント限定: issuer をこの tenant に固定し、audience を SPA の client ID に限定する。
var functionEasyAuthEnabled = applyFunctionAuthLockdown && !empty(functionAuthEntraClientId)

resource functionAuthSettingsV2 'Microsoft.Web/sites/config@2023-12-01' = if (functionEasyAuthEnabled) {
  parent: functionApp
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
      runtimeVersion: '~1'
    }
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
      // Functions の管理 API (/admin/*) を認証の対象外にする。
      // 除外しないと EasyAuth が Azure 自身の管理呼び出しまで 401 で弾き、
      //   - `az functionapp deployment source config-zip` の検証段階が Bad Request で落ちる
      //   - `az functionapp function list` が Bad Request になり関数を列挙できない
      // （2026-08-06 に実環境で発生。デプロイ自体は成功しているのに CD が赤くなる）。
      // /admin/* は元々 **master key 必須** で、EasyAuth を外しても無認可では叩けない。
      excludedPaths: [
        '/admin/*'
      ]
    }
    httpSettings: {
      requireHttps: true
      routes: {
        apiPrefix: '/.auth'
      }
      forwardProxy: {
        convention: 'NoProxy'
      }
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          // 単一テナント issuer。他テナントの identity は検証で落ちる。
          openIdIssuer: 'https://login.microsoftonline.com/${functionAuthEntraTenantId}/v2.0'
          clientId: functionAuthEntraClientId
        }
        validation: {
          // SPA が自分自身の client ID 宛に取ったトークンを受ける (api://<id> と <id> の両表記)。
          allowedAudiences: [
            functionAuthEntraClientId
            'api://${functionAuthEntraClientId}'
          ]
        }
      }
      // Explicitly disable all other providers to avoid partially-enabled configs.
      azureStaticWebApps: {
        enabled: false
      }
      facebook: {
        enabled: false
      }
      google: {
        enabled: false
      }
      gitHub: {
        enabled: false
      }
      twitter: {
        enabled: false
      }
      apple: {
        enabled: false
      }
      legacyMicrosoftAccount: {
        enabled: false
      }
    }
    login: {
      preserveUrlFragmentsForLogins: false
      tokenStore: {
        enabled: false
      }
    }
  }
}

// -------------------- 予算アラート (二重防御) — #69 --------------------
// 認可が破られても・設定を緩めても請求で気づけるようにする最後の砦 (ADR 0013)。
// 50% で予兆、80% で警戒、100% で超過。通知先が空なら作らない (通知の無い予算は無意味)。
resource budget 'Microsoft.Consumption/budgets@2023-05-01' = if (enableBudgetAlert && !empty(budgetContactEmails)) {
  name: 'budget-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}'
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

output logAnalyticsWorkspaceId string = law.id
output logAnalyticsCustomerId string = law.properties.customerId
output sqlEnabled bool = enableSql
output sqlServerFqdn string = enableSql ? '${sqlServerName}.${sqlServerHostnameSuffix}' : ''
output sqlDatabase string = enableSql ? sqlDatabaseName : ''
output vnetId string = vnetEnabled ? resourceId('Microsoft.Network/virtualNetworks', vnetName) : ''
output peSubnetId string = vnetEnabled ? peSubnetResourceId : ''
output appSubnetId string = vnetEnabled ? appSubnetResourceId : ''
output sqlAdminKeyVaultName string = enableSql ? sqlAdminKeyVaultName : ''

output staticSiteDefaultHostname string = staticSite.properties.defaultHostname
output functionAppDefaultHostname string = functionApp.properties.defaultHostName

// #69: フロントのビルド時 env とスモークテストがこの3つを参照する。
output staticSiteSkuName string = staticSiteSkuName
output functionEasyAuthEnabled bool = functionEasyAuthEnabled
output functionAuthEntraClientId string = functionAuthEntraClientId
output containerAppsGateClientId string = containerAppsGateClientId
output functionAuthEntraTenantId string = functionEasyAuthEnabled ? functionAuthEntraTenantId : ''
output budgetAlertEnabled bool = enableBudgetAlert && !empty(budgetContactEmails)

output staticSiteName string = staticSite.name
output staticSiteEntraAuthEnabled bool = staticSiteEntraAuthEnabled
output staticSiteEntraClientId string = effectiveStaticSiteEntraClientId
output staticSiteEntraAppAutoCreated bool = staticSiteAutoCreateEntraApp
output staticSiteEntraAppObjectId string = autoCreatedStaticSiteEntraAppObjectId
output voicevoxEnabled bool = enableVoicevoxAca
output voicevoxContainerAppName string = enableVoicevoxAca ? voicevoxContainerApp.name : ''
output voicevoxBaseUrl string = enableVoicevoxAca
  ? 'https://${voicevoxContainerApp.?properties.?configuration.?ingress.?fqdn ?? ''}'
  : ''
output speechEnabled bool = enableSpeech
output speechAccountName string = enableSpeech ? speechAccountName : ''
output speechRegion string = enableSpeech ? speechLocation : ''
output openAiEnabled bool = enableOpenAi
output openAiEndpoint string = enableOpenAi ? (openAiAccount.?properties.?endpoint ?? '') : ''
output openAiAccountName string = enableOpenAi ? openAiAccountName : ''
output openAiDeploymentName string = enableOpenAi ? openAiDeploymentName : ''
output aiAgentEnabled bool = enableAiAgentAca
output aiAgentContainerAppName string = enableAiAgentAca ? aiAgentContainerAppName : ''
// #68: CAE を 1 つに統合したので、両サービスとも同じ環境名を返す。
// output 名は据え置き — deploy-*.sh はこの名前を読むだけで変更不要。
output containerAppsEnvironmentName string = containerAppsEnabled ? containerAppsEnvironmentName : ''
output aiAgentContainerAppsEnvironmentName string = enableAiAgentAca ? containerAppsEnvironmentName : ''
output voicevoxWrapperEnabled bool = enableVoicevoxWrapperAca
output voicevoxWrapperContainerAppName string = enableVoicevoxWrapperAca ? voicevoxWrapperContainerAppName : ''
output voicevoxWrapperContainerAppsEnvironmentName string = enableVoicevoxWrapperAca ? containerAppsEnvironmentName : ''
// #188: 両 Container App をテンプレートに取り込んだので FQDN も output で引ける
// (名前は param aiAgentBaseUrl / voicevoxWrapperBaseUrl と衝突しないよう App を挟む)。
output aiAgentAppBaseUrl string = enableAiAgentAca
  ? 'https://${aiAgentContainerApp.?properties.?configuration.?ingress.?fqdn ?? ''}'
  : ''
output voicevoxWrapperAppBaseUrl string = enableVoicevoxWrapperAca
  ? 'https://${voicevoxWrapperContainerApp.?properties.?configuration.?ingress.?fqdn ?? ''}'
  : ''

// Cosmos DB (ADR 0030)。runbook / 検証スクリプトがアカウント名と DB 名をここから引く。
output cosmosEnabled bool = enableCosmos
output cosmosAccountName string = enableCosmos ? cosmosAccountName : ''
output cosmosEndpoint string = enableCosmos ? 'https://${cosmosAccountName}.documents.azure.com:443/' : ''
output cosmosDatabaseName string = enableCosmos ? cosmosDatabaseName : ''
output cosmosLocation string = enableCosmos ? cosmosLocation : ''
// デプロイが成功していれば、この値が true のとき無料枠を実際に取得できている
// (枠が消費済みなら enableFreeTier: true の作成は失敗するため / ADR 0030 D2)。
output cosmosFreeTierEnabled bool = enableCosmos && enableCosmosFreeTier && !cosmosServerless
output cosmosServerless bool = enableCosmos && cosmosServerless
