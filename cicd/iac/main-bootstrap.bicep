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

@allowed([
  'westus2'
  'centralus'
  'eastus2'
  'westeurope'
  'eastasia'
])
@description('Azure Static Web Apps region')
param staticSiteLocation string = 'eastasia'

@description('Azure region for Azure Functions resources')
param functionLocation string = staticSiteLocation

@description('Enable VOICEVOX on Azure Container Apps (Serverless GPU).')
param enableVoicevoxAca bool = false

@allowed([
  'cpu'
  'gpu'
])
@description('VOICEVOX tier (ADR 0010). cpu = 速く安く（Consumption, 既定）/ gpu = T4 で喋りが速い。')
param voicevoxTier string = 'cpu'

@description('Azure region for VOICEVOX Container Apps resources.')
param voicevoxLocation string = functionLocation

@description('共有 Container Apps Environment のリージョン (#68)。CAE は 1 つに統合済み。既定は voicevoxLocation (GPU のリージョン制約が最も厳しいため)。')
param containerAppsLocation string = voicevoxLocation

@allowed([
  'B1'
  'S1'
  'Y1'
  'EP1'
])
@description('Functions plan SKU')
param functionPlanSkuName string = 'Y1'

// -------------------- SWA SKU / Functions 認可 / 予算 (#69, ADR 0013) --------------------
@allowed([
  'Free'
  'Standard'
])
@description('Static Web Apps SKU. 既定 Free: linked backend を捨てて待機 ¥0 にし、認可は Functions の EasyAuth が担う。')
param staticSiteSkuName string = 'Free'

@description('Apply Function App EasyAuth lockdown (未認証は 401)。functionAuthEntraClientId と併用する。')
param applyFunctionAuthLockdown bool = false

@description('Entra app client ID (SPA)。EasyAuth の audience かつ MSAL のクライアント。空なら EasyAuth 未構成。')
param functionAuthEntraClientId string = ''

@description('Container Apps の認証の門 (ADR 0017) の audience となる Entra app client ID。空なら BFF は下流へトークンを付けない。')
param containerAppsGateClientId string = ''

@description('BFF が呼ぶ ai-agent Container App の base URL。appSettings 全置換で消えないよう bicep で宣言する。空なら deploy-backend.sh の再結線に任せる。')
param aiAgentBaseUrl string = ''

@description('BFF が呼ぶ voicevox-wrapper Container App の base URL。appSettings 全置換で消えないよう bicep で宣言する。空なら deploy-backend.sh の再結線に任せる。')
param voicevoxWrapperBaseUrl string = ''

@description('Entra tenant ID。既定はデプロイ先テナント (単一テナント限定)。')
param functionAuthEntraTenantId string = tenant().tenantId

@description('Function App の CORS 追加許可オリジン (SWA 既定ホスト名は自動許可)。')
param functionExtraCorsOrigins array = []

@description('月次予算アラートを作る。')
param enableBudgetAlert bool = true

@description('月次予算額 (請求通貨)。')
param budgetAmount int = 3000

@description('予算アラートの通知先メール。空なら予算を作らない。')
param budgetContactEmails array = []

@description('予算の開始日 (月初, yyyy-MM-dd)。作成後は変更不可のため固定値で管理する。')
param budgetStartDate string = '2026-08-01'

// -------------------- Azure OpenAI --------------------
@description('Enable Azure OpenAI account and model deployment.')
param enableOpenAi bool = false

@description('Azure region for Azure OpenAI (e.g. japaneast, eastus, swedencentral).')
param openAiLocation string = functionLocation

// -------------------- Azure Speech (ADR 0023) --------------------
@description('Enable Azure Speech (server STT, F0 無料枠から — ADR 0023)。')
param enableSpeech bool = false

@description('Azure region for the Speech service.')
param speechLocation string = functionLocation

// -------------------- AI Agent Container App --------------------
// NOTE: ACR は廃止（#67 / ADR 0013）。image は ghcr に事前ビルド（build-images.yml）。
@description('Enable AI Agent on Azure Container Apps.')
param enableAiAgentAca bool = false

@description('ai-agent / voicevox-wrapper の image タグ。CD (provision.sh) は build-images の sha-<full-sha> を渡す (ADR 0025)。既定 latest は CD 外の初回 bootstrap 用フォールバック。')
param containerImageTag string = 'latest'

@description('image 座標のベース (<registry>/<owner>/<repo>)。この下に /ai-agent, /voicevox-wrapper がある。CD (provision.sh) は IMAGE_REGISTRY / IMAGE_REPO から解決した値を渡し、deploy-*.sh と座標がズレないようにする (PR #261 Codex P2)。既定は本家 ghcr の座標。')
param ghcrImageRepository string = 'ghcr.io/yomote/mind-inbox'

// -------------------- VOICEVOX Wrapper Container App --------------------
@description('Enable VOICEVOX Wrapper on Azure Container Apps.')
param enableVoicevoxWrapperAca bool = false

// -------------------- Cosmos DB (ADR 0030 / #165) --------------------
// 既定 false は他の enable* と揃えたもの。dev 環境は main-bootstrap.parameters.json で有効化する。
@description('Provision Cosmos DB (NoSQL) — Problem / 履歴の永続化ストア (ADR 0030)。既定 false / dev は parameters.json で true。')
param enableCosmos bool = false

@description('Cosmos DB の保管リージョン。ADR 0030 D6 で Japan East 固定 (NFR-1「保管リージョンの明確化」)。')
param cosmosLocation string = 'japaneast'

@description('無料枠 (1,000 RU/s + 25 GB) にオプトインする。**作成時のみ有効**・後から変更不可。枠が消費済みならデプロイが失敗する (= 課金構成で黙って作られない)。')
param enableCosmosFreeTier bool = true

@description('serverless で作る (ADR 0030 D2 のフォールバック)。無料枠は serverless 対象外。')
param cosmosServerless bool = false

@minValue(400)
@description('DB 共有スループット (RU/s)。既定 400 は予算 ¥3,000 (ADR 0013) を踏まえた値 — 上げる前にコストを確認すること。')
param cosmosThroughput int = 400

@description('Provision the Azure SQL stack. Default false: v1 は in-memory のみで SQL 未使用 (ADR 0013)。永続化は Cosmos DB (ADR 0030) が担うため、SQL は引き続き不要。')
param enableSql bool = false

@description('Set to true if a soft-deleted Key Vault with the same name already exists.')
param recoverSqlAdminKeyVault bool = false

@description('Name of the Azure Key Vault that stores the SQL administrator password (lowercase, 3-24 chars). 既存 soft-deleted vault と被る場合は別名にする。')
param sqlAdminKeyVaultName string = toLower('kv-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-sql')

@description('Set to true if a soft-deleted Azure OpenAI account with the same name already exists.')
param restoreOpenAiAccount bool = false

@description('Full infra bootstrap deployment (without SWA Entra auth setup).')
module infra '../modules/bootstrap-core.bicep' = {
  params: {
    appName: appName
    environmentName: environmentName
    staticSiteLocation: staticSiteLocation
    functionLocation: functionLocation
    functionPlanSkuName: functionPlanSkuName
    staticSiteSkuName: staticSiteSkuName
    applyFunctionAuthLockdown: applyFunctionAuthLockdown
    functionAuthEntraClientId: functionAuthEntraClientId
    containerAppsGateClientId: containerAppsGateClientId
    aiAgentBaseUrl: aiAgentBaseUrl
    voicevoxWrapperBaseUrl: voicevoxWrapperBaseUrl
    functionAuthEntraTenantId: functionAuthEntraTenantId
    functionExtraCorsOrigins: functionExtraCorsOrigins
    enableBudgetAlert: enableBudgetAlert
    budgetAmount: budgetAmount
    budgetContactEmails: budgetContactEmails
    budgetStartDate: budgetStartDate
    enableVoicevoxAca: enableVoicevoxAca
    voicevoxTier: voicevoxTier
    containerAppsLocation: containerAppsLocation
    voicevoxLocation: voicevoxLocation
    enableStaticSiteEntraAuth: false
    autoCreateStaticSiteEntraAppRegistration: false
    enableOpenAi: enableOpenAi
    openAiLocation: openAiLocation
    enableSpeech: enableSpeech
    speechLocation: speechLocation
    enableAiAgentAca: enableAiAgentAca
    enableVoicevoxWrapperAca: enableVoicevoxWrapperAca
    containerImageTag: containerImageTag
    ghcrImageRepository: ghcrImageRepository
    enableCosmos: enableCosmos
    cosmosLocation: cosmosLocation
    enableCosmosFreeTier: enableCosmosFreeTier
    cosmosServerless: cosmosServerless
    cosmosThroughput: cosmosThroughput
    enableSql: enableSql
    recoverSqlAdminKeyVault: recoverSqlAdminKeyVault
    sqlAdminKeyVaultName: sqlAdminKeyVaultName
    restoreOpenAiAccount: restoreOpenAiAccount
  }
}

output staticSiteName string = infra.outputs.staticSiteName
output staticSiteDefaultHostname string = infra.outputs.staticSiteDefaultHostname
output functionAppDefaultHostname string = infra.outputs.functionAppDefaultHostname
output staticSiteSkuName string = infra.outputs.staticSiteSkuName
output functionEasyAuthEnabled bool = infra.outputs.functionEasyAuthEnabled
output functionAuthEntraClientId string = infra.outputs.functionAuthEntraClientId
output containerAppsGateClientId string = infra.outputs.containerAppsGateClientId
output functionAuthEntraTenantId string = infra.outputs.functionAuthEntraTenantId
output budgetAlertEnabled bool = infra.outputs.budgetAlertEnabled
output sqlEnabled bool = infra.outputs.sqlEnabled
output sqlServerFqdn string = infra.outputs.sqlServerFqdn
output sqlDatabase string = infra.outputs.sqlDatabase
output staticSiteEntraClientId string = infra.outputs.staticSiteEntraClientId
output staticSiteEntraAppAutoCreated bool = infra.outputs.staticSiteEntraAppAutoCreated
output staticSiteEntraAppObjectId string = infra.outputs.staticSiteEntraAppObjectId
output voicevoxContainerAppName string = infra.outputs.voicevoxContainerAppName
output voicevoxBaseUrl string = infra.outputs.voicevoxBaseUrl
output speechEnabled bool = infra.outputs.speechEnabled
output speechAccountName string = infra.outputs.speechAccountName
output speechRegion string = infra.outputs.speechRegion
output openAiEnabled bool = infra.outputs.openAiEnabled
output openAiEndpoint string = infra.outputs.openAiEndpoint
output openAiAccountName string = infra.outputs.openAiAccountName
output openAiDeploymentName string = infra.outputs.openAiDeploymentName
output aiAgentEnabled bool = infra.outputs.aiAgentEnabled
output aiAgentContainerAppName string = infra.outputs.aiAgentContainerAppName
output containerAppsEnvironmentName string = infra.outputs.containerAppsEnvironmentName
output aiAgentContainerAppsEnvironmentName string = infra.outputs.aiAgentContainerAppsEnvironmentName
output voicevoxWrapperEnabled bool = infra.outputs.voicevoxWrapperEnabled
output voicevoxWrapperContainerAppName string = infra.outputs.voicevoxWrapperContainerAppName
output voicevoxWrapperContainerAppsEnvironmentName string = infra.outputs.voicevoxWrapperContainerAppsEnvironmentName
output aiAgentAppBaseUrl string = infra.outputs.aiAgentAppBaseUrl
output voicevoxWrapperAppBaseUrl string = infra.outputs.voicevoxWrapperAppBaseUrl
output cosmosEnabled bool = infra.outputs.cosmosEnabled
output cosmosAccountName string = infra.outputs.cosmosAccountName
output cosmosEndpoint string = infra.outputs.cosmosEndpoint
output cosmosDatabaseName string = infra.outputs.cosmosDatabaseName
output cosmosLocation string = infra.outputs.cosmosLocation
output cosmosFreeTierEnabled bool = infra.outputs.cosmosFreeTierEnabled
output cosmosServerless bool = infra.outputs.cosmosServerless
