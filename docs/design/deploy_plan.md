# Mind Inbox — Azure フルスタック一発デプロイ計画

作成: 2026-05-02 / 対象: PoC 実装を Azure 上で end-to-end 動作させる

---

## 0. ゴールと前提

### 0.1 ゴール

ローカルで stub-only モードまで通っている PoC 実装を、Azure リソースを実体化し、
本物の AI Agent + VOICEVOX + Azure OpenAI と通信した状態で SWA から E2E で動作させる。

### 0.2 サブスクリプション・前提

- `az login` 済み、課金可能なサブスクリプションが選択されている
- リージョン: `eastasia`（SWA / Functions） + `japaneast`（OpenAI / Container Apps / GPU）
- リソースグループ名: `rg-dev-mind-inbox`（既定値）
- App 名: `mind-box`、env: `dev`
- VOICEVOX Engine（GPU T4）は japaneast の Serverless GPU 枠が必要

### 0.3 大原則

- **デプロイスクリプトの修正を最初にまとめてコミット**してから `az deployment` を回す。
  途中で IaC を直しながら進めるとロールバック困難。
- **1 段階ずつ smoke を確認**しながら次に進める（`/health` レベルの確認は各段で必須）。
- **Function App / Container App の app settings は後段で wire する**。
  bootstrap.bicep は「リソースを作るだけ」、サービス間 URL の注入はデプロイスクリプト側の責務。

---

## 1. 現状把握 — デプロイ可能性ギャップ

ローカル PoC 完了時点での「Azure に上げる前に直さないとデプロイ失敗 or 動作しない」項目。

| #   | ギャップ                                                                                                                                                                               | 影響                                                                 | 修正先                                                                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| G1  | `deploy-backend.sh` が **旧 Python BFF 用**（`backend/` パス、`pip install`、`.python_packages` zip）                                                                                  | Node.js BFF をデプロイ不可                                           | `cicd/scripts/deploy/deploy-backend.sh` 全面書き換え                       |
| G2  | `deploy-frontend.sh` が `frontend/` パス参照（実体は `apps/frontend/`）                                                                                                                | Frontend ビルド失敗                                                  | `cicd/scripts/deploy/deploy-frontend.sh` パス修正                          |
| G3  | `main-bootstrap.bicep` が `enableVoicevoxWrapperAca` / `voicevoxWrapperLocation` パラメータをモジュール呼び出しに渡していない（`parameters.json` には定義あり）                        | Wrapper の Managed Environment が作られず Container App デプロイ失敗 | `cicd/iac/main-bootstrap.bicep` 引数追加                                   |
| G4  | `parameters.json` の各 `enable*` フラグが軒並み `false`                                                                                                                                | リソースが何も作られない                                             | パラメータを `true` に切り替え                                             |
| G5  | BFF Function App の `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` を post-deploy で wire するスクリプトがない                                                                              | BFF が stub fallback のままで Live にならない                        | `deploy-backend.sh` に wiring 処理を追加（or 新規 `wire-bff-env.sh`）      |
| G6  | `apps/bff/host.json` の `routePrefix=api` は OK だが、Functions の `package.json` の `main` を `dist/src/functions/*.js` グロブに変えた結果、zip 内に `dist/` が含まれている前提に依存 | zip 構成の組み立てミスでデプロイされても 404                         | `deploy-backend.sh` で `dist/` と `node_modules/` を確実に zip に入れる    |
| G7  | Frontend の prod ビルドで `VITE_USE_MOCK` が誤って `true` になるリスク                                                                                                                 | 本番でも mockApi が呼ばれる                                          | `.env.production` を新設 or ビルド時に明示 unset を確認                    |
| G8  | smoke-test.sh が `/api/health` を確認するが、新ルーター構成では `/api/trpc/health.ping` が正しい                                                                                       | smoke が常に WARN になる                                             | `cicd/scripts/smoke-test/smoke-test.sh` に tRPC 経由のヘルスチェックを追加 |

これらを **Phase 0** で一括修正したうえで Phase 1 以降の `az deployment` に進む。

---

## 2. ターゲット構成（Azure 側）

```text
SWA (Standard, eastasia)
 └─ linked backend: Function App (Linux, Node|20, eastasia)
      ├─ /api/trpc/{*}  → consultation.* / history.* / health.*
      └─ /api/tts       → audio/wav バイナリ

Function App env vars (post-deploy で wire)
 ├─ AI_AGENT_BASE_URL=https://<ai-agent-fqdn>
 └─ VOICEVOX_BASE_URL=https://<vv-wrapper-fqdn>

Container App: AI Agent (japaneast)
 └─ env: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT, USE_MANAGED_IDENTITY=true
 └─ Managed Identity → Cognitive Services OpenAI User on Azure OpenAI

Container App: VOICEVOX Wrapper (japaneast)
 └─ env: VOICEVOX_ENGINE_BASE_URL=https://<vv-engine-fqdn>

Container App: VOICEVOX Engine (japaneast, GPU T4)
 └─ image: voicevox/voicevox_engine:nvidia-latest

Azure OpenAI (japaneast)
 └─ deployment: gpt-4o
```

---

## 3. デプロイ手順 全体像

```text
Phase 0: 前準備（コード修正のみ、Azure リソース変更なし）
 └─ G1〜G8 を修正してコミット

Phase 1: bootstrap deploy（IaC 一発）
 ├─ enableAcr / enableOpenAi / enableAiAgentAca / enableVoicevoxAca / enableVoicevoxWrapperAca = true
 └─ az deployment group create で全リソース作成

Phase 2: コンテナサービスのデプロイ（依存順）
 ├─ 2-1. VOICEVOX Engine — Bicep が Container App として作成済み（image 直指定）
 ├─ 2-2. VOICEVOX Wrapper — deploy-voicevox-wrapper.sh で ACR build + CA create
 └─ 2-3. AI Agent — deploy-ai-agent.sh で ACR build + CA create + OpenAI ロール付与

Phase 3: Function App（BFF）のデプロイ
 ├─ 3-1. deploy-backend.sh で Node 用 zip 作成 + config-zip
 └─ 3-2. AI_AGENT_BASE_URL / VOICEVOX_BASE_URL を Container App FQDN から取得して BFF に注入

Phase 4: SWA Entra 認証 wiring（main-config.bicep）
 ├─ Entra アプリ登録の作成 or 既存利用
 └─ Key Vault に CLIENT_ID / CLIENT_SECRET を保管

Phase 5: Frontend デプロイ
 ├─ 5-1. apps/frontend で pnpm build（VITE_USE_MOCK 未設定 → 自動的に false）
 ├─ 5-2. staticwebapp.config.json の <TENANT_ID> を置換
 └─ 5-3. swa deploy

Phase 6: E2E smoke test
 ├─ 6-1. /api/trpc/health.ping → 200 OK
 ├─ 6-2. /api/trpc/consultation.start → 200 + 実 LLM の開幕メッセージ
 ├─ 6-3. /api/tts → audio/wav 200
 └─ 6-4. ブラウザでフロー一気通貫
```

---

## 4. Phase 0: 事前修正（コミット単位）

### 4.1 G1 — `deploy-backend.sh` を Node.js BFF 用に書き換え

**新仕様**:

```bash
# apps/bff で:
pnpm install --frozen-lockfile  # or npm ci
pnpm run build                  # tsc → dist/

# zip 構成（root に host.json / package.json / dist / node_modules）
zip -qr functionapp.zip \
  host.json \
  package.json \
  dist/ \
  node_modules/ \
  -x "node_modules/.cache/*" \
  -x "node_modules/.vite/*"
```

**重要**: Functions v4 (Node) は `package.json` の `main` で関数登録ファイルを解決する。
B1 で `main: "dist/src/functions/*.js"` に変更済みなので、`dist/` を zip に含めれば動く。
Python 用の `.python_packages` 関連処理はすべて削除。

**post-deploy wiring**:

```bash
# Container App FQDN を取得
AI_AGENT_FQDN="$(az containerapp show -g $RG -n $CA_NAME --query 'properties.configuration.ingress.fqdn' -o tsv)"
VV_WRAPPER_FQDN="$(az containerapp show -g $RG -n $VV_CA_NAME --query 'properties.configuration.ingress.fqdn' -o tsv)"

# BFF に注入
az functionapp config appsettings set -g $RG -n $FUNC_APP_NAME --settings \
  "AI_AGENT_BASE_URL=https://${AI_AGENT_FQDN}" \
  "VOICEVOX_BASE_URL=https://${VV_WRAPPER_FQDN}"
```

`deploy-backend.sh` の最後にこの wiring セクションを足す。
（Container Apps が未デプロイなら警告だけ出してスキップ）

### 4.2 G2 — `deploy-frontend.sh` のパス修正

```diff
- FRONTEND_DIR="$ROOT_DIR/frontend"
+ FRONTEND_DIR="$ROOT_DIR/apps/frontend"
```

`pnpm install --frozen-lockfile` / `pnpm build` の挙動は既存のままで OK
（`apps/frontend/pnpm-lock.yaml` 存在）。

### 4.3 G3 — `main-bootstrap.bicep` に Wrapper パラメータを追加

```diff
+ @description('Enable VOICEVOX Wrapper on Azure Container Apps.')
+ param enableVoicevoxWrapperAca bool = false
+
+ @description('Azure region for VOICEVOX Wrapper Container Apps resources.')
+ param voicevoxWrapperLocation string = functionLocation

  module infra '../modules/bootstrap-core.bicep' = {
    params: {
      ...
+     enableVoicevoxWrapperAca: enableVoicevoxWrapperAca
+     voicevoxWrapperLocation: voicevoxWrapperLocation
    }
  }

+ output voicevoxWrapperEnabled bool = infra.outputs.voicevoxWrapperEnabled
+ output voicevoxWrapperContainerAppName string = infra.outputs.voicevoxWrapperContainerAppName
+ output voicevoxWrapperContainerAppsEnvironmentName string = infra.outputs.voicevoxWrapperContainerAppsEnvironmentName
```

### 4.4 G4 — `parameters.json` のフラグを true に

```json
{
  "enableVoicevoxAca": { "value": true },
  "enableOpenAi": { "value": true },
  "enableAcr": { "value": true },
  "enableAiAgentAca": { "value": true },
  "enableVoicevoxWrapperAca": { "value": true }
}
```

### 4.5 G5 — wiring 処理は G1 と統合済み

`deploy-backend.sh` の末尾で実施（4.1 参照）。

### 4.6 G6 — zip 構成は G1 で対応済み

特に `node_modules/` を含めることが必須。`pnpm` の場合 symlink 構造になっているので、
**zip 化前に `pnpm install --shamefully-hoist`** を併用するか、`npm ci` に切り替えて
従来型の `node_modules/` を作るのが安全。

PoC 推奨: `apps/bff` は **npm 管理**のままにする（既に `package-lock.json` がない可能性が
あるので確認 → なければ `npm install` で生成してコミット）。

> **検証メモ**: `apps/bff` のロックファイル状態を確認: `ls apps/bff/{package-lock.json,pnpm-lock.yaml}`
> → 既に `package-lock.json` のみ存在することを Phase 0 開始時に確認する。

### 4.7 G7 — Frontend prod ビルドで mockApi に行かないこと

`apps/frontend/.env.production` を新設:

```bash
# prod では BFF を使う
VITE_USE_MOCK=false
# VITE_VOICEVOX_SPEAKER は任意。省略時はコード内デフォルト 3
```

`VITE_BFF_BASE_URL` は **設定しない**（同一オリジン同居になるため空文字列でよい）。

### 4.8 G8 — smoke-test.sh の health チェック修正

```diff
- swa_api_code=$(curl -sS -o /dev/null -w "%{http_code}" "https://$SWA_HOST/api/health")
+ swa_api_code=$(curl -sS -o /dev/null -w "%{http_code}" "https://$SWA_HOST/api/trpc/health.ping")
```

Function App 直接アクセスのチェックも同様に変更。

### 4.9 Phase 0 完了条件

- 上記 G1〜G8 を実装したコミットを作成
- `cd apps/bff && npm run build`、`cd apps/frontend && pnpm build` がローカルで通る
- IaC: `az bicep build cicd/iac/main-bootstrap.bicep` がエラーなく通る

---

## 5. Phase 1: bootstrap deploy

### 5.1 リソースグループ確認

```bash
RG="rg-dev-mind-inbox"
LOCATION="eastasia"

az group show -n $RG -o tsv --query name 2>/dev/null \
  || az group create -n $RG -l $LOCATION
```

### 5.2 bootstrap deploy

```bash
az deployment group create \
  --resource-group $RG \
  --name main-bootstrap \
  --template-file cicd/iac/main-bootstrap.bicep \
  --parameters @cicd/iac/main-bootstrap.parameters.json
```

**所要時間目安**: 15〜25 分（OpenAI account の provisioning + ACR + Function App + SQL Private Endpoint + Container Apps Environments × 3）

**確認**:

```bash
az deployment group show -g $RG -n main-bootstrap --query 'properties.outputs' -o json
```

期待される主要 output:

- `staticSiteName`
- `functionAppDefaultHostname`
- `acrName`
- `aiAgentContainerAppName` / `aiAgentContainerAppsEnvironmentName`
- `voicevoxWrapperContainerAppName` / `voicevoxWrapperContainerAppsEnvironmentName`
- `voicevoxBaseUrl`（Engine の FQDN）
- `openAiEndpoint` / `openAiDeploymentName`

### 5.3 想定される失敗とリカバリ

| 症状                                       | 原因                                 | 対処                                                                                    |
| ------------------------------------------ | ------------------------------------ | --------------------------------------------------------------------------------------- |
| `KeyVaultSoftDeleteSubscriptionDisabled`   | 同名の Key Vault が soft-delete 状態 | `recoverSqlAdminKeyVault: true` を parameters に設定                                    |
| `OpenAIAccountSoftDeletedExists`           | OpenAI account が soft-delete 中     | `restoreOpenAiAccount: true` を設定                                                     |
| `GpuQuotaExceeded`                         | T4 GPU の quota 不足                 | サブスク単位で quota 申請、または Engine の `enableVoicevoxAca: false` で当面 stub 運用 |
| `RegionNotSupported`（Container Apps GPU） | japaneast で T4 在庫なし             | `voicevoxLocation: 'eastus'` などに変更                                                 |

---

## 6. Phase 2: コンテナサービスのデプロイ

### 6.1 VOICEVOX Engine

bootstrap の Bicep が image 直指定で作成済み。**特別なデプロイ不要**。
`voicevoxBaseUrl` output から FQDN を取得して動作確認:

```bash
ENGINE_URL=$(az deployment group show -g $RG -n main-bootstrap --query 'properties.outputs.voicevoxBaseUrl.value' -o tsv)
curl -fsS "${ENGINE_URL}/version"
# 期待: バージョン文字列が返る（コールドスタート 30〜60 秒）
```

### 6.2 VOICEVOX Wrapper

```bash
RG=$RG bash cicd/scripts/deploy/deploy-voicevox-wrapper.sh
```

スクリプトが ACR build → CA create → AcrPull ロール付与まで実施。
完了後の出力 FQDN を記録。

```bash
VV_WRAPPER_FQDN=$(az containerapp show -g $RG -n $(az deployment group show -g $RG -n main-bootstrap --query 'properties.outputs.voicevoxWrapperContainerAppName.value' -o tsv) --query 'properties.configuration.ingress.fqdn' -o tsv)
curl -fsS "https://${VV_WRAPPER_FQDN}/health"
# 期待: {"status":"ok","engine_reachable":true}
```

### 6.3 AI Agent

```bash
RG=$RG bash cicd/scripts/deploy/deploy-ai-agent.sh
```

スクリプトが ACR build → CA create → AcrPull + OpenAI User ロール付与まで実施。

```bash
AI_AGENT_FQDN=$(az containerapp show -g $RG -n $(az deployment group show -g $RG -n main-bootstrap --query 'properties.outputs.aiAgentContainerAppName.value' -o tsv) --query 'properties.configuration.ingress.fqdn' -o tsv)
curl -fsS "https://${AI_AGENT_FQDN}/health"
# 期待: {"status":"ok"}
```

実 LLM 確認:

```bash
curl -X POST "https://${AI_AGENT_FQDN}/chat" \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test-1","message":"テスト"}'
# 期待: AI 応答が返る（最初の呼び出しは Managed Identity の token 取得で 5〜10 秒）
```

### 6.4 Phase 2 完了条件

3 サービスすべて `/health` が 200 を返し、AI Agent は実 OpenAI 応答を返している。

---

## 7. Phase 3: BFF (Function App) のデプロイ

### 7.1 zip deploy

```bash
RG=$RG bash cicd/scripts/deploy/deploy-backend.sh
```

新スクリプトが:

1. `apps/bff` で `npm ci` + `npm run build`
2. `dist/` `node_modules/` `package.json` `host.json` を zip
3. `az functionapp deployment source config-zip`
4. **Container App FQDN を取得して `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` を BFF に注入**
5. `az functionapp restart`（環境変数反映のため）

### 7.2 確認

```bash
FUNC_HOST=$(az deployment group show -g $RG -n main-bootstrap --query 'properties.outputs.functionAppDefaultHostname.value' -o tsv)
curl -fsS "https://${FUNC_HOST}/api/trpc/health.ping"
# 期待: {"result":{"data":{"ok":true}}}

curl -X POST "https://${FUNC_HOST}/api/trpc/consultation.start" \
  -H 'Content-Type: application/json' \
  -d '{"concern":"本番疎通テスト"}'
# 期待: 実 AI Agent 経由の開幕メッセージ（stub ではなく LLM 応答）

curl -X POST "https://${FUNC_HOST}/api/tts" \
  -H 'Content-Type: application/json' \
  -d '{"text":"テスト","speaker":3}' \
  -o /tmp/test.wav
# 期待: 200 + audio/wav バイナリ
file /tmp/test.wav  # → "RIFF (little-endian) data, WAVE audio"
```

### 7.3 想定される失敗

| 症状                                             | 原因                                                       | 対処                                                                                                      |
| ------------------------------------------------ | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `404` on `/api/trpc/*`                           | zip に `dist/` が含まれていない                            | deploy-backend.sh の zip コマンドを確認                                                                   |
| `500` on `consultation.sendMessage`              | BFF → AI Agent への通信失敗                                | Function App の env vars を `az functionapp config appsettings list` で確認、AI_AGENT_BASE_URL が正しいか |
| `EasyAuth` で 401                                | bootstrap で `applyFunctionAuthLockdown=true` になっている | Phase 4 完了後に確認、または `false` のまま進める                                                         |
| Container App コールドスタート遅延で初回 timeout | min-replicas=0 のため                                      | min-replicas=1 に一時的に変更、または Functions の timeout を延長                                         |

---

## 8. Phase 4: SWA Entra 認証 wiring

### 8.1 main-config.bicep の実行

`docs/cicd/iac/infra_arch_resource_roles.md` 参照。
Entra アプリ登録 → Key Vault に CLIENT_ID/SECRET 格納 → SWA に app settings 設定。

```bash
# 既存の main-config.bicep がある前提
az deployment group create \
  --resource-group $RG \
  --name main-config \
  --template-file cicd/iac/main-config.bicep \
  --parameters environmentName=dev appName=mind-box
```

### 8.2 確認

```bash
SWA_NAME=$(az deployment group show -g $RG -n main-bootstrap --query 'properties.outputs.staticSiteName.value' -o tsv)
az staticwebapp appsettings list -g $RG -n $SWA_NAME --query 'properties.AZURE_CLIENT_ID' -o tsv
# 期待: 非空の GUID
```

---

## 9. Phase 5: Frontend デプロイ

### 9.1 swa deploy

```bash
RG=$RG bash cicd/scripts/deploy/deploy-frontend.sh
```

スクリプトが:

1. `apps/frontend` で `pnpm build`（`.env.production` の `VITE_USE_MOCK=false` を反映）
2. `staticwebapp.config.json` の `<TENANT_ID>` 置換
3. `swa deploy dist/`

### 9.2 確認

```bash
SWA_HOST=$(az staticwebapp show -g $RG -n $SWA_NAME --query defaultHostname -o tsv)
curl -fsS "https://${SWA_HOST}"
# 期待: index.html が返る

# 認証込みの API 経路は curl では確認しづらい。ブラウザでログイン後、
# DevTools の Network タブで /api/trpc/* と /api/tts が通っているか確認
```

---

## 10. Phase 6: E2E smoke test

### 10.1 自動化された smoke

```bash
RG=$RG DEPLOYMENT=main-bootstrap bash cicd/scripts/smoke-test/smoke-test.sh
```

期待:

- SWA root reachable: OK
- SWA /api/trpc/health.ping reachable: OK
- Function App /api/trpc/health.ping reachable: OK
- SQL public access blocked: OK
- Log Analytics query: OK

### 10.2 ブラウザ E2E

1. `https://${SWA_HOST}` にアクセス → Entra ID ログイン
2. ホーム → 新しい相談 → concern 入力
3. 「対話を開始」→ AI Agent から実 LLM 応答が表示される
4. メッセージを送信して複数ターン会話
5. VOICEVOX の音声が再生される（フォールバックではない実音声）
6. 「整理する」→ 実 LLM 出力の OrganizedResult が表示
7. 「行動プランを作る」→ 実 ActionPlan
8. 「保存する」→ 履歴一覧に追加
9. ページリロードして履歴が残っている（in-memory なので消えるが、PoC 制約として許容）

### 10.3 完了条件

- 上記 E2E が一通り動作
- コンソールエラーゼロ
- DevTools Network で `/api/trpc/*` と `/api/tts` がすべて 200/204
- Application Insights で AI Agent / Wrapper への成功ログが見える

---

## 11. ロールバック方針

### 11.1 段階別ロールバック

| 段階         | ロールバック手段                                                                                      |
| ------------ | ----------------------------------------------------------------------------------------------------- |
| Phase 1 失敗 | `az deployment group cancel` → `az group delete -n $RG` で全消去                                      |
| Phase 2 失敗 | 当該 Container App だけ `az containerapp delete`                                                      |
| Phase 3 失敗 | Function App の Deployment Center から前回スロットへ revert（Functions は zip deploy 履歴を保持）     |
| Phase 5 失敗 | SWA は production 環境に直接 deploy しているため revert 困難 → preview env を経由する運用に変える検討 |

### 11.2 完全クリーンアップ

```bash
# サブスク内のすべてのコストを止める
az group delete -n $RG --yes --no-wait
```

OpenAI / Key Vault / SWA は soft-delete で残るため、再デプロイ時に
`recoverSqlAdminKeyVault: true` / `restoreOpenAiAccount: true` を設定する。

---

## 12. 推定コスト（dev 環境、月額）

| リソース               | SKU                | 概算                                 |
| ---------------------- | ------------------ | ------------------------------------ |
| Static Web Apps        | Standard           | $9                                   |
| Function App           | Y1 (Consumption)   | <$1                                  |
| Storage Account        | LRS                | <$1                                  |
| SQL Database           | Basic 2GB          | $5                                   |
| Container Apps Env × 3 | Consumption        | $0（min=0）                          |
| AI Agent Container     | scale-to-zero      | $0〜$5                               |
| VOICEVOX Wrapper       | scale-to-zero      | $0〜$5                               |
| VOICEVOX Engine        | GPU T4 Consumption | $0〜$50（T4 単価高い、使った分だけ） |
| Azure OpenAI           | gpt-4o, 10K TPM    | ~$0.005/1K tokens                    |
| Key Vault              | Standard           | <$1                                  |
| Log Analytics          | PAYG               | <$5                                  |
| ACR                    | Basic              | $5                                   |

**月額合計目安**: $30〜$80（GPU 使用時間に大きく依存）

長期間放置する場合は Phase 6 完了後に GPU を **min=0** で確実に停止状態にすること。

---

## 13. チェックリスト

```text
Phase 0: 事前修正
[ ] G1  cicd/scripts/deploy/deploy-backend.sh を Node.js 用に全面書き換え + wiring 処理追加
[ ] G2  cicd/scripts/deploy/deploy-frontend.sh のパスを apps/frontend に
[ ] G3  cicd/iac/main-bootstrap.bicep に enableVoicevoxWrapperAca / voicevoxWrapperLocation を追加
[ ] G4  cicd/iac/main-bootstrap.parameters.json の enable* フラグを true に
[ ] G7  apps/frontend/.env.production を新規作成（VITE_USE_MOCK=false）
[ ] G8  cicd/scripts/smoke-test/smoke-test.sh の health 経路を /api/trpc/health.ping に
[ ] apps/bff にロックファイル（package-lock.json）が存在するか確認、なければ npm install で生成
[ ] az bicep build / npm run build / pnpm build がローカルで通る
[ ] git commit

Phase 1: bootstrap
[ ] az group create
[ ] az deployment group create main-bootstrap
[ ] outputs に必要キーが揃っていることを確認

Phase 2: コンテナサービス
[ ] VOICEVOX Engine /version 200
[ ] deploy-voicevox-wrapper.sh 完走 + /health 200
[ ] deploy-ai-agent.sh 完走 + /health 200 + 実 LLM 応答

Phase 3: BFF
[ ] deploy-backend.sh 完走
[ ] /api/trpc/health.ping 200
[ ] /api/trpc/consultation.start で実 LLM 応答
[ ] /api/tts で audio/wav 取得

Phase 4: Entra 認証
[ ] main-config.bicep 完了
[ ] SWA app settings に AZURE_CLIENT_ID 設定済み

Phase 5: Frontend
[ ] deploy-frontend.sh 完走
[ ] SWA root 200

Phase 6: E2E smoke
[ ] smoke-test.sh PASS
[ ] ブラウザで一気通貫動作
[ ] 不要時は Container Apps min=0 を確認 / または az group delete
```

---

## 14. 進め方の提案

Phase 0 のコード修正だけで結構ボリュームがあるので、**Phase 0 を 1 つの PR としてまとめて
ローカルで全ビルドが通ることを確認してから Phase 1 以降に進む**のが安全です。

各 Phase の終わりで一度立ち止まって、想定外の Azure コストが立っていないかを `az consumption usage list` で確認することを推奨。
