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
@description('Static Web Apps SKU')
param staticSiteSkuName string = 'Standard'

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

@description('Azure region for VOICEVOX Container Apps environment/app.')
param voicevoxLocation string = location

@description('Container Apps Environment name for VOICEVOX.')
param voicevoxContainerAppsEnvironmentName string = toLower('cae-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-voicevox')

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

// -------------------- Azure Container Registry params --------------------
@description('Enable Azure Container Registry for building and storing AI Agent images.')
param enableAcr bool = false

@description('ACR name (alphanumeric, 5-50 chars, globally unique).')
param acrName string = toLower('cr${take('${environmentName}${replace(replace(appName, '-', ''), '_', '')}', 46)}')

@allowed([
  'Basic'
  'Standard'
  'Premium'
])
@description('ACR SKU.')
param acrSkuName string = 'Basic'

// -------------------- AI Agent Container App params --------------------
@description('Enable AI Agent on Azure Container Apps.')
param enableAiAgentAca bool = false

@description('Azure region for AI Agent Container Apps resources.')
param aiAgentLocation string = location

@description('Container Apps Environment name for AI Agent.')
param aiAgentContainerAppsEnvironmentName string = toLower('cae-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-ai')

@description('Container App name for AI Agent.')
param aiAgentContainerAppName string = toLower('ca-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-ai-agent')

// -------------------- VOICEVOX Wrapper Container App params --------------------
@description('Enable VOICEVOX Wrapper on Azure Container Apps.')
param enableVoicevoxWrapperAca bool = false

@description('Azure region for VOICEVOX Wrapper Container Apps resources.')
param voicevoxWrapperLocation string = location

@description('Container Apps Environment name for VOICEVOX Wrapper.')
param voicevoxWrapperContainerAppsEnvironmentName string = toLower('cae-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-vv-wrap')

@description('Container App name for VOICEVOX Wrapper.')
param voicevoxWrapperContainerAppName string = toLower('ca-${environmentName}-${replace(replace(appName, '-', ''), '_', '')}-vv-wrap')

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
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
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

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' existing = {
  parent: vnet
  name: peSubnetName
}
resource appSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-01-01' existing = {
  parent: vnet
  name: appSubnetName
}

// -------------------- Private DNS Zone (SQL) --------------------
var sqlServerHostnameSuffix = startsWith(environment().suffixes.sqlServerHostname, '.')
  ? substring(environment().suffixes.sqlServerHostname, 1)
  : environment().suffixes.sqlServerHostname

var sqlPrivateDnsZoneName = 'privatelink.${sqlServerHostnameSuffix}'

resource sqlPrivateDnsZone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: sqlPrivateDnsZoneName
  location: 'global'
}

resource sqlPrivateDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: sqlPrivateDnsZone
  name: '${vnet.name}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

// -------------------- Key Vault for SQL admin password --------------------
@description('Set to true if a soft-deleted Key Vault with the same name already exists and needs to be recovered.')
param recoverSqlAdminKeyVault bool = false

resource sqlAdminKeyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
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

resource sqlAdminPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: sqlAdminKeyVault
  name: sqlAdminPasswordSecretName
  properties: {
    value: sqlAdminPassword
  }
}

// -------------------- Azure SQL Server & DB --------------------
resource sqlServer 'Microsoft.Sql/servers@2024-05-01-preview' = {
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

resource sqlDb 'Microsoft.Sql/servers/databases@2024-05-01-preview' = {
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
resource sqlPe 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: 'pe-${sqlServer.name}'
  location: location
  properties: {
    subnet: {
      id: peSubnet.id
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
}

// DNS Zone Group to auto-create A records in privatelink zone
resource sqlPeDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
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

resource diagSqlServer 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-${sqlServer.name}'
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
          linuxFxVersion: 'Node|20'
          minTlsVersion: '1.2'
          ftpsState: 'Disabled'
          appSettings: [
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
              value: '~20'
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
          ]
        },
        enableFunctionVnetIntegration ? { vnetRouteAllEnabled: true } : {}
      )
    },
    enableFunctionVnetIntegration ? { virtualNetworkSubnetId: appSubnet.id } : {}
  )
}

resource voicevoxManagedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = if (enableVoicevoxAca) {
  name: voicevoxContainerAppsEnvironmentName
  location: voicevoxLocation
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
      {
        name: voicevoxWorkloadProfileName
        workloadProfileType: voicevoxWorkloadProfileType
      }
    ]
  }
}

resource voicevoxContainerApp 'Microsoft.App/containerApps@2024-03-01' = if (enableVoicevoxAca) {
  name: voicevoxContainerAppName
  location: voicevoxLocation
  properties: {
    managedEnvironmentId: voicevoxManagedEnvironment.id
    workloadProfileName: voicevoxWorkloadProfileName
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 50021
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'voicevox-engine'
          image: voicevoxImage
          env: [
            {
              name: 'MALLOC_ARENA_MAX'
              value: '2'
            }
          ]
          resources: {
            cpu: voicevoxCpu
            memory: voicevoxMemory
          }
        }
      ]
      scale: {
        minReplicas: voicevoxMinReplicas
        maxReplicas: voicevoxMaxReplicas
      }
    }
  }
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

// -------------------- Azure Container Registry --------------------
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = if (enableAcr) {
  name: acrName
  location: location
  sku: {
    name: acrSkuName
  }
  properties: {
    adminUserEnabled: false
  }
}

// -------------------- AI Agent Container Apps Environment --------------------
// Container App itself is created/updated by deploy-ai-agent.sh (avoids ARM provisioning timeout)
// Consumption-only environment (no workloadProfiles) — provisions in seconds
resource aiAgentManagedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = if (enableAiAgentAca) {
  name: aiAgentContainerAppsEnvironmentName
  location: aiAgentLocation
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

// -------------------- VOICEVOX Wrapper Container Apps Environment --------------------
// Container App itself is created/updated by deploy-voicevox-wrapper.sh
resource voicevoxWrapperManagedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = if (enableVoicevoxWrapperAca) {
  name: voicevoxWrapperContainerAppsEnvironmentName
  location: voicevoxWrapperLocation
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

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
// Lock down the Function App so it is intended to be called via SWA proxy only.
// This uses the built-in Azure Static Web Apps identity provider.
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
          // Observed in the portal as the SWA hostname; keep it deterministic in IaC.
          clientId: staticSite.properties.defaultHostname
        }
      }
      // Explicitly disable all other providers to avoid partially-enabled configs.
      azureActiveDirectory: {
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

output logAnalyticsWorkspaceId string = law.id
output logAnalyticsCustomerId string = law.properties.customerId
output sqlServerFqdn string = '${sqlServer.name}.${sqlServerHostnameSuffix}'
output sqlDatabase string = sqlDb.name
output vnetId string = vnet.id
output peSubnetId string = peSubnet.id
output appSubnetId string = appSubnet.id
output sqlAdminKeyVaultName string = sqlAdminKeyVault.name

output staticSiteDefaultHostname string = staticSite.properties.defaultHostname
output functionAppDefaultHostname string = functionApp.properties.defaultHostName

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
output openAiEnabled bool = enableOpenAi
output openAiEndpoint string = enableOpenAi ? (openAiAccount.?properties.?endpoint ?? '') : ''
output openAiAccountName string = enableOpenAi ? openAiAccountName : ''
output openAiDeploymentName string = enableOpenAi ? openAiDeploymentName : ''
output acrEnabled bool = enableAcr
output acrName string = enableAcr ? acrName : ''
output acrLoginServer string = enableAcr ? acr!.properties.loginServer : ''
output aiAgentEnabled bool = enableAiAgentAca
output aiAgentContainerAppName string = enableAiAgentAca ? aiAgentContainerAppName : ''
output aiAgentContainerAppsEnvironmentName string = enableAiAgentAca ? aiAgentContainerAppsEnvironmentName : ''
output voicevoxWrapperEnabled bool = enableVoicevoxWrapperAca
output voicevoxWrapperContainerAppName string = enableVoicevoxWrapperAca ? voicevoxWrapperContainerAppName : ''
output voicevoxWrapperContainerAppsEnvironmentName string = enableVoicevoxWrapperAca ? voicevoxWrapperContainerAppsEnvironmentName : ''
