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
