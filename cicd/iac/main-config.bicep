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

@description('Static Web Apps name (must already exist).')
param staticSiteName string = toLower('swa-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Azure Functions app name (must already exist).')
param functionAppName string = toLower('func-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}')

@description('Enable Entra ID built-in authentication for SWA')
param enableStaticSiteEntraAuth bool = true

@description('Auto-create/update Entra app registration via deployment script')
param autoCreateStaticSiteEntraAppRegistration bool = true

@description('Display name for auto-created Entra app registration')
param staticSiteEntraAppDisplayName string = 'app-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-swa'

@description('User Assigned Managed Identity resource ID used by deployment script for Entra app automation')
param staticSiteEntraBootstrapUserAssignedIdentityResourceId string = ''

@description('User Assigned Managed Identity client ID used by deployment script for az login --identity')
param staticSiteEntraBootstrapUserAssignedIdentityClientId string = ''

@description('Secret validity period (years) for auto-created Entra app')
@minValue(1)
@maxValue(5)
param staticSiteEntraAppSecretYears int = 2

@description('Tenant ID for SWA Entra auth')
param staticSiteEntraTenantId string = subscription().tenantId

@description('Existing Entra app (client) ID when auto-create is false')
param staticSiteEntraClientId string = ''

@secure()
@description('Existing Entra app client secret when auto-create is false')
param staticSiteEntraClientSecret string = ''

@description('Apply Function App EasyAuth lockdown for SWA proxy calls')
param applyFunctionAuthLockdown bool = true

module staticSiteAuth '../modules/static-site-auth.bicep' = {
  name: 'mod-static-site-auth-${uniqueString(resourceGroup().id, staticSiteName, functionAppName)}'
  params: {
    staticSiteName: staticSiteName
    functionAppName: functionAppName
    enableStaticSiteEntraAuth: enableStaticSiteEntraAuth
    autoCreateStaticSiteEntraAppRegistration: autoCreateStaticSiteEntraAppRegistration
    staticSiteEntraAppDisplayName: staticSiteEntraAppDisplayName
    staticSiteEntraBootstrapUserAssignedIdentityResourceId: staticSiteEntraBootstrapUserAssignedIdentityResourceId
    staticSiteEntraBootstrapUserAssignedIdentityClientId: staticSiteEntraBootstrapUserAssignedIdentityClientId
    staticSiteEntraAppSecretYears: staticSiteEntraAppSecretYears
    staticSiteEntraTenantId: staticSiteEntraTenantId
    staticSiteEntraClientId: staticSiteEntraClientId
    staticSiteEntraClientSecret: staticSiteEntraClientSecret
    applyFunctionAuthLockdown: applyFunctionAuthLockdown
  }
}

output staticSiteHostname string = staticSiteAuth.outputs.staticSiteHostname
output effectiveClientId string = staticSiteAuth.outputs.effectiveClientId
output staticSiteEntraAuthApplied bool = staticSiteAuth.outputs.staticSiteEntraAuthApplied
output staticSiteEntraAppAutoCreated bool = staticSiteAuth.outputs.staticSiteEntraAppAutoCreated
output staticSiteEntraAppObjectId string = staticSiteAuth.outputs.staticSiteEntraAppObjectId
