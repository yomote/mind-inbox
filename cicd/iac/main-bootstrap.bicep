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

@description('Azure region for VOICEVOX Container Apps resources.')
param voicevoxLocation string = functionLocation

@allowed([
  'B1'
  'S1'
  'Y1'
  'EP1'
])
@description('Functions plan SKU')
param functionPlanSkuName string = 'Y1'

// -------------------- Azure OpenAI --------------------
@description('Enable Azure OpenAI account and model deployment.')
param enableOpenAi bool = false

@description('Azure region for Azure OpenAI (e.g. japaneast, eastus, swedencentral).')
param openAiLocation string = functionLocation

// -------------------- ACR + AI Agent Container App --------------------
@description('Enable Azure Container Registry.')
param enableAcr bool = false

@description('Enable AI Agent on Azure Container Apps.')
param enableAiAgentAca bool = false

@description('Azure region for AI Agent Container Apps resources.')
param aiAgentLocation string = functionLocation

// -------------------- VOICEVOX Wrapper Container App --------------------
@description('Enable VOICEVOX Wrapper on Azure Container Apps.')
param enableVoicevoxWrapperAca bool = false

@description('Azure region for VOICEVOX Wrapper Container Apps resources.')
param voicevoxWrapperLocation string = functionLocation

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
    enableVoicevoxAca: enableVoicevoxAca
    voicevoxLocation: voicevoxLocation
    enableStaticSiteEntraAuth: false
    autoCreateStaticSiteEntraAppRegistration: false
    enableOpenAi: enableOpenAi
    openAiLocation: openAiLocation
    enableAcr: enableAcr
    enableAiAgentAca: enableAiAgentAca
    aiAgentLocation: aiAgentLocation
    enableVoicevoxWrapperAca: enableVoicevoxWrapperAca
    voicevoxWrapperLocation: voicevoxWrapperLocation
    recoverSqlAdminKeyVault: recoverSqlAdminKeyVault
    sqlAdminKeyVaultName: sqlAdminKeyVaultName
    restoreOpenAiAccount: restoreOpenAiAccount
  }
}

output staticSiteName string = infra.outputs.staticSiteName
output functionAppDefaultHostname string = infra.outputs.functionAppDefaultHostname
output sqlServerFqdn string = infra.outputs.sqlServerFqdn
output sqlDatabase string = infra.outputs.sqlDatabase
output staticSiteEntraClientId string = infra.outputs.staticSiteEntraClientId
output staticSiteEntraAppAutoCreated bool = infra.outputs.staticSiteEntraAppAutoCreated
output staticSiteEntraAppObjectId string = infra.outputs.staticSiteEntraAppObjectId
output voicevoxBaseUrl string = infra.outputs.voicevoxBaseUrl
output openAiEnabled bool = infra.outputs.openAiEnabled
output openAiEndpoint string = infra.outputs.openAiEndpoint
output openAiDeploymentName string = infra.outputs.openAiDeploymentName
output acrLoginServer string = infra.outputs.acrLoginServer
output acrName string = infra.outputs.acrName
output aiAgentEnabled bool = infra.outputs.aiAgentEnabled
output aiAgentContainerAppName string = infra.outputs.aiAgentContainerAppName
output aiAgentContainerAppsEnvironmentName string = infra.outputs.aiAgentContainerAppsEnvironmentName
output voicevoxWrapperEnabled bool = infra.outputs.voicevoxWrapperEnabled
output voicevoxWrapperContainerAppName string = infra.outputs.voicevoxWrapperContainerAppName
output voicevoxWrapperContainerAppsEnvironmentName string = infra.outputs.voicevoxWrapperContainerAppsEnvironmentName
