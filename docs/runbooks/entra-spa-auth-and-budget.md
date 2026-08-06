# Runbook: 常設 dev の認可 (Entra SPA + Functions EasyAuth) と予算アラート

関連: [ADR 0013](../adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / epic #70 / issue #69

常設・公開 URL で本物の AI を出すため、**課金の芯 (Azure OpenAI) を持つ Functions 側に認可の門**を置く。SWA は Free (¥0) で静的ファイルを配るだけになり、フロントは Functions を別オリジンから直叩きする。

> **CORS は認可ではない。** CORS を判定するのはブラウザなので、`curl` や Postman はヘッダを無視して到達する。第三者を実際に止めるのは **Functions EasyAuth の 401**。CORS は「他人のサイトの JS がログイン中のあなたのブラウザを踏み台にする」のを防ぐためのもの。

## 全体像

```
ブラウザ ──(MSAL でログイン)──> Entra ID
   │  アクセストークン (aud = SPA の client ID)
   ├──> SWA Free            静的ファイルのみ・匿名で取得可
   └──> Functions (BFF)     EasyAuth: 未認証は 401 ←★ 守りはここ
             └──> Azure OpenAI   ←★ 課金の芯 + 予算アラート
```

## 前提

- `az login` 済み（[device code の手順](./claude-web-azure-access.md)）
- Entra でアプリ登録を作れる権限
- bootstrap 済み（`main-bootstrap.bicep`）

---

## 1. Entra アプリ登録 (SPA) を作る — 一度きり

```bash
APP_NAME="mind-inbox-dev-spa"
SWA_HOST="$(az deployment group show -g rg-dev-mind-inbox -n main-bootstrap \
  --query 'properties.outputs.staticSiteDefaultHostname.value' -o tsv)"

# SPA プラットフォームとしてリダイレクト URI を登録する（Web ではなく SPA。
# SPA にすると PKCE が使われ、client secret を持たずに済む = 静的シークレット0 を維持）
CLIENT_ID="$(az ad app create \
  --display-name "$APP_NAME" \
  --sign-in-audience AzureADMyOrg \
  --spa-redirect-uris "https://$SWA_HOST" "http://localhost:5173" \
  --query appId -o tsv)"
echo "CLIENT_ID=$CLIENT_ID"

# API スコープを露出させる（EasyAuth の allowedAudiences と対にする）
az ad app update --id "$CLIENT_ID" --identifier-uris "api://$CLIENT_ID"
```

`--sign-in-audience AzureADMyOrg` が **単一テナント限定**（design-gate で選択した「A. 単一テナント限定のみ」）。他テナントのアカウントは issuer 検証で落ちる。

> テナントに自分以外が増えたら、Enterprise アプリケーション → プロパティ → **「割り当てが必要」= はい** にして自分だけを割り当てると、テナント内でも閉じられる。

## 2. IaC に client ID を渡して再デプロイ

```bash
az deployment group create \
  -g rg-dev-mind-inbox -n main-bootstrap \
  -f cicd/iac/main-bootstrap.bicep \
  -p @cicd/iac/main-bootstrap.parameters.json \
  -p applyFunctionAuthLockdown=true \
     functionAuthEntraClientId="$CLIENT_ID" \
     budgetContactEmails='["<your-email@example.com>"]'
```

これで:

- Function App の EasyAuth が Entra 単一テナントで有効になり、**未認証は 401**
- Function App の CORS に SWA の既定ホスト名が入る
- 月次予算アラート（既定 ¥3,000 / actual 50% ・ forecast 80% ・ actual 100%）が作られる

## 3. フロントを再デプロイ

```bash
RG=rg-dev-mind-inbox ./cicd/scripts/deploy/deploy-frontend.sh
```

`deploy-frontend.sh` が deployment outputs から `VITE_BFF_BASE_URL` / `VITE_ENTRA_CLIENT_ID` / `VITE_ENTRA_TENANT_ID` を解決し、**ビルド時に焼き込む**。Entra の値が空のときは「認証無効のビルドを出す」旨を警告するので、公開前にログを見ること。

---

## 4. 検証 — ここが本体

**未認証で 401 が返ることを実測する。** これが通らないと、認可があるつもりで公開されている。

```bash
FUNC_HOST="$(az deployment group show -g rg-dev-mind-inbox -n main-bootstrap \
  --query 'properties.outputs.functionAppDefaultHostname.value' -o tsv)"

# (a) 未認証で叩く → 401 が返れば門が効いている
curl -s -o /dev/null -w '%{http_code}\n' "https://$FUNC_HOST/api/trpc/health.ping"
# 期待: 401   NG: 200 (門が効いていない = 誰でも OpenAI を焼ける)

# (b) CORS preflight が通るか（EasyAuth が OPTIONS まで 401 にすると本物のブラウザが動かない）
curl -s -o /dev/null -w '%{http_code}\n' -X OPTIONS \
  -H "Origin: https://$SWA_HOST" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,content-type" \
  "https://$FUNC_HOST/api/trpc/health.ping"
# 期待: 200 or 204
```

**(b) が 401 になる場合**（EasyAuth と preflight の既知の噛み合わせ問題）: `authsettingsV2` の `globalValidation.excludedPaths` に該当パスを足すか、CORS を Functions アプリ側で処理する。ここは実環境でしか確認できないため、詰まったらこの節に結果を追記すること。

最後にブラウザで SWA の URL を開き、Entra ログイン → 実際の AI 応答まで到達することを確認する。

---

## トラブルシュート

| 症状 | 原因 / 対処 |
| --- | --- |
| ログイン後も API が 401 | トークンの `aud` が EasyAuth の `allowedAudiences` と不一致。`api://<clientId>` を identifier-uri に設定したか確認。フロントの `VITE_ENTRA_API_SCOPE` も合わせる |
| ブラウザで CORS エラー | Function App の CORS 許可オリジンに SWA のホスト名が入っているか。`functionExtraCorsOrigins` で追加も可 |
| ログイン画面に飛ばない | フロントのビルドに `VITE_ENTRA_*` が入っていない（＝認証無効ビルド）。`deploy-frontend.sh` のログの警告を確認 |
| リダイレクトエラー (AADSTS50011) | アプリ登録の **SPA** リダイレクト URI に SWA の URL が無い。Web ではなく SPA 種別で登録すること |
| 予算アラートが来ない | `budgetContactEmails` が空だと予算リソース自体を作らない（通知の無い予算は無意味なため）。値を入れて再デプロイ |

## 一時的に認可を外したいとき

`applyFunctionAuthLockdown=false` で再デプロイすると EasyAuth が外れる。**公開 URL のまま外すと誰でも OpenAI を叩ける**ので、外すのは切り分けの一時措置に限り、戻すまで放置しない。予算アラートは外さないこと。
