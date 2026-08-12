// SPA アプリ登録 (Entra) の宣言。ADR 0046 D3/D4 / Issue #303。
//
// これまで docs/runbooks/entra-spa-auth-and-budget.md の「一度きり」の手順を
// 人が az ad app で叩いていたものを、Microsoft Graph Bicep 拡張で宣言に移す。
//
// ■ なぜ bootstrap と分けるのか (ADR 0046 D4)
//   Graph の拡張リソースは what-if が使えない
//   (https://learn.microsoft.com/en-us/graph/templates/bicep/limitations)。
//   main-bootstrap.bicep に混ぜると bootstrap 全体が what-if 不可になり、
//   #308 が PR CI に入れようとしている `az deployment group what-if` の網が
//   丸ごと無効化される。だから Graph は必ずこの別エントリポイントに隔離する。
//
// ■ なぜ壊して作り直してもログインが壊れないのか (ADR 0046 D3)
//   uniqueName は applications の「必須かつ Immutable な代替キー」なので、
//   何度適用しても同じアプリ登録が更新される = appId (クライアント ID) が不変。
//   SWA を作り直してホスト名が変わっても、追従するのは spa.redirectUris だけ。
//
// ■ ⚠️ 未適用 (2026-08-12 時点)
//   このファイルはまだ実環境に適用していない。CI (iac-validate) の
//   コンパイル検証のみ通している。実適用と、下記「移行前に確かめること」の
//   検証は #306 の第 1 段階 (手動 dispatch) で行う。
//
// ■ 移行前に確かめること (#306 第 1 段階の成果物)
//   1. 既存アプリ (appId a0f2c66e-…) は az ad app create で作られており
//      uniqueName を持たない。uniqueName は Immutable なので「後から付けられるか」
//      が未確認。付けられない場合、この宣言は既存を採用できず新しい登録を作る
//      = appId が一度だけ変わる (影響: main-bootstrap.parameters.json の
//      functionAuthEntraClientId / フロントのビルド時変数 / 事前 consent のやり直し)
//   2. identifierUris は api://{appId} の形が要る (EasyAuth の allowedAudiences と対)
//      が、appId は生成物なので同一リソース内で自己参照できない。
//      → existingApplicationClientId パラメータで外から渡す 2 パス方式にしてある。
//        空なら identifierUris を宣言しない (初回作成時)
//   3. 既存の access_as_user スコープの GUID は手作業時に uuid4 で採られている。
//      採用できる場合はその GUID を existingScopeId で渡す必要がある
//      (異なる GUID を宣言するとスコープが作り直しになり consent がやり直しになる)

extension microsoftGraphV1

targetScope = 'resourceGroup'

@description('アプリ登録の表示名。Runbook の APP_NAME と揃える')
param applicationDisplayName string = 'mind-inbox-dev-spa'

@description('''
uniqueName — applications の必須かつ Immutable な代替キー。
これが固定である限り、何度適用しても同じアプリ登録が更新される (appId が変わらない)。
一度決めたら変更しないこと。変更すると別のアプリ登録になる。
''')
param applicationUniqueName string = 'mind-inbox-dev-spa'

@description('SWA の既定ホスト名 (main-bootstrap の staticSiteDefaultHostname output を渡す)。ホスト名だけでスキームは含めない')
param staticSiteHostname string

@description('ローカル開発用の redirect URI。Vite の既定ポート')
param localDevRedirectUri string = 'http://localhost:5173'

@description('''
既存アプリの appId。identifierUris (api://{appId}) を宣言するために使う。
appId は生成物で同一リソース内から自己参照できないため、2 パス目で外から渡す。
初回作成時は空文字にする (identifierUris を宣言しない)。
''')
param existingApplicationClientId string = ''

@description('''
既存の access_as_user スコープの GUID。既存アプリを採用する場合は、
手作業時に採られた GUID をそのまま渡すこと (異なる GUID にすると consent がやり直しになる)。
空なら uniqueName から決定的に導出する。
''')
param existingScopeId string = ''

@description('公開する delegated permission の名前。EasyAuth 側の scope と対になる')
param apiScopeValue string = 'access_as_user'

// スコープ ID は「渡されたらそれ / 無ければ uniqueName から決定的に導出」。
// guid() は入力が同じなら同じ値を返すので、再適用でスコープが作り直しにならない。
var apiScopeId = empty(existingScopeId) ? guid(applicationUniqueName, apiScopeValue) : existingScopeId

// redirect URI は SWA のホスト名に追従する。ここが ADR 0046 D3 の中心 —
// SWA を作り直してホスト名が変わっても、宣言を再適用すれば URI だけが更新される。
var spaRedirectUris = [
  'https://${staticSiteHostname}'
  localDevRedirectUri
]

resource spaApplication 'Microsoft.Graph/applications@v1.0' = {
  displayName: applicationDisplayName
  uniqueName: applicationUniqueName

  // 単一テナント限定。他テナントのアカウントは issuer 検証で落ちる
  // (design-gate で選択した「A. 単一テナント限定のみ」/ Runbook 参照)
  signInAudience: 'AzureADMyOrg'

  // SPA 種別。web にすると PKCE にならず client secret が要る
  // (Graph Bicep は passwordCredentials 非対応なので、そもそも web では宣言化できない)
  spa: {
    redirectUris: spaRedirectUris
  }

  // identifierUris は api://{appId} の形。appId が生成物で自己参照できないため
  // 2 パス目で外から渡す。初回は空配列 = 宣言しない
  identifierUris: empty(existingApplicationClientId) ? [] : ['api://${existingApplicationClientId}']

  api: {
    // ★ v2 にしないと issuer 不一致で「ログインは通るのに API が 401」になる。
    //   既定は 1 (v1.0 エンドポイント相当)
    requestedAccessTokenVersion: 2

    // ★ EasyAuth の allowedAudiences と対になる delegated permission
    oauth2PermissionScopes: [
      {
        id: apiScopeId
        value: apiScopeValue
        type: 'User'
        isEnabled: true
        adminConsentDisplayName: 'Access Mind Inbox BFF as user'
        adminConsentDescription: 'Allows the SPA to call the Mind Inbox BFF (Functions EasyAuth) as the signed-in user.'
        userConsentDisplayName: 'Access Mind Inbox BFF'
        userConsentDescription: 'Allows this app to call the Mind Inbox backend on your behalf.'
      }
    ]
  }

  // ★ 自己参照の delegated permission。無いと .default が解決先を持たない
  requiredResourceAccess: empty(existingApplicationClientId) ? [] : [
    {
      resourceAppId: existingApplicationClientId
      resourceAccess: [
        {
          id: apiScopeId
          type: 'Scope'
        }
      ]
    }
  ]
}

// ★ SP を別途作る。applications を作るだけでは SP は生えず、
//   無いとサインインが無限ループする (Runbook の罠その 1)
resource spaServicePrincipal 'Microsoft.Graph/servicePrincipals@v1.0' = {
  appId: spaApplication.appId
}

// ★ 事前 consent。手作業では `az ad app permission grant --consent-type AllPrincipals`
//   に相当する。#303 が「最後の関門」としていた部分で、
//   Microsoft.Graph/oauth2PermissionGrants が v1.0 のサポート対象なので宣言できる
//   (https://learn.microsoft.com/en-us/graph/templates/reference/overview)
resource spaSelfConsent 'Microsoft.Graph/oauth2PermissionGrants@v1.0' = {
  clientId: spaServicePrincipal.id
  resourceId: spaServicePrincipal.id
  consentType: 'AllPrincipals'
  scope: apiScopeValue
}

@description('クライアント ID。main-bootstrap の functionAuthEntraClientId に渡す値')
output applicationClientId string = spaApplication.appId

@description('アプリ登録のオブジェクト ID')
output applicationObjectId string = spaApplication.id

@description('SP のオブジェクト ID')
output servicePrincipalObjectId string = spaServicePrincipal.id

@description('公開した delegated permission の GUID。2 パス目に existingScopeId として渡す')
output apiScopeId string = apiScopeId

@description('実際に宣言した SPA redirect URI (SWA ホスト名に追従していることの確認用)')
output spaRedirectUris array = spaRedirectUris
