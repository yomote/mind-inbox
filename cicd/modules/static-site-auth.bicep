targetScope = 'resourceGroup'

@description('Static Web Apps name')
param staticSiteName string

@description('Function App name linked behind SWA')
param functionAppName string

@description('Enable Entra ID built-in authentication for SWA')
param enableStaticSiteEntraAuth bool = false

@description('Auto-create/update Entra app registration via deployment script')
param autoCreateStaticSiteEntraAppRegistration bool = false

@description('Display name for auto-created Entra app registration')
param staticSiteEntraAppDisplayName string

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

@description('Existing Entra app (client) ID')
param staticSiteEntraClientId string = ''

@secure()
@description('Existing Entra app client secret')
param staticSiteEntraClientSecret string = ''

@description('Apply Function App EasyAuth lockdown for SWA proxy calls')
param applyFunctionAuthLockdown bool = true

resource staticSite 'Microsoft.Web/staticSites@2023-12-01' existing = {
  name: staticSiteName
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' existing = {
  name: functionAppName
}

var staticSiteAutoCreateEntraApp = enableStaticSiteEntraAuth && autoCreateStaticSiteEntraAppRegistration
var useManagedIdentityForEntraBootstrap = !empty(staticSiteEntraBootstrapUserAssignedIdentityResourceId) && !empty(staticSiteEntraBootstrapUserAssignedIdentityClientId)

resource staticSiteEntraAppBootstrap 'Microsoft.Resources/deploymentScripts@2023-08-01' = if (staticSiteAutoCreateEntraApp) {
  name: 'ds-entra-swa-${uniqueString(resourceGroup().id, staticSite.name)}'
  location: resourceGroup().location
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
    azCliVersion: '2.52.0'
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

resource staticSiteAppSettings 'Microsoft.Web/staticSites/config@2023-12-01' = if (staticSiteEntraAuthEnabled) {
  parent: staticSite
  name: 'appsettings'
  properties: {
    AZURE_CLIENT_ID: effectiveStaticSiteEntraClientId
    AZURE_CLIENT_SECRET: effectiveStaticSiteEntraClientSecret
    AZURE_TENANT_ID: effectiveStaticSiteEntraTenantId
  }
}

resource functionAuthSettingsV2 'Microsoft.Web/sites/config@2023-12-01' = if (applyFunctionAuthLockdown) {
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
      azureStaticWebApps: {
        enabled: true
        registration: {
          clientId: staticSite.properties.defaultHostname
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
  }
}

output staticSiteHostname string = staticSite.properties.defaultHostname
output effectiveClientId string = effectiveStaticSiteEntraClientId
output staticSiteEntraAuthApplied bool = staticSiteEntraAuthEnabled
output staticSiteEntraAppAutoCreated bool = staticSiteAutoCreateEntraApp
output staticSiteEntraAppObjectId string = autoCreatedStaticSiteEntraAppObjectId
