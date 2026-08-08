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

// -------------------- AI Agent Container App --------------------
// NOTE: ACR は廃止（#67 / ADR 0013）。image は ghcr に事前ビルド（build-images.yml）。
@description('Enable AI Agent on Azure Container Apps.')
param enableAiAgentAca bool = false

// -------------------- VOICEVOX Wrapper Container App --------------------
@description('Enable VOICEVOX Wrapper on Azure Container Apps.')
param enableVoicevoxWrapperAca bool = false

@description('Provision the Azure SQL stack. Default false: v1 は in-memory のみで SQL 未使用 (ADR 0013)。永続化 (Phase 2: Redis + Cosmos) が要るとき true。')
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
    enableAiAgentAca: enableAiAgentAca
    enableVoicevoxWrapperAca: enableVoicevoxWrapperAca
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
