# AI Agent Service

Azure OpenAI を使ったチャットエージェント。FastAPI + Microsoft Agent Framework (MAF / ADR 0016) で実装。

## エンドポイント

| Method | Path       | 説明                    |
| ------ | ---------- | ----------------------- |
| GET    | `/health`  | ヘルスチェック          |
| POST   | `/chat`    | メッセージ送信          |
| POST   | `/approve` | ツール呼び出し承認/拒否 |

---

## ローカル実行

### 前提

- Python 3.11+
- VOICEVOX Engine は不要（スタブ実装）

### セットアップ

```bash
cd apps/services/ai-agent
pip install -e .
```

`.env` を作成:

```env
# Azure OpenAI を使う場合
AZURE_OPENAI_ENDPOINT=https://<your-openai>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4o

# OpenAI (フォールバック)
# OPENAI_API_KEY=sk-...
```

### 起動

```bash
uvicorn app.main:app --reload --port 8000
```

### 動作確認

```bash
# ヘルスチェック
curl http://localhost:8000/health

# チャット
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-01", "message": "こんにちは"}'
```

---

## Azure Container Apps へのデプロイ

### 前提

- `az login` 済み
- bootstrap で `enableAiAgentAca=true` でデプロイ済み
- image が ghcr に push 済み（`build-images.yml` が main マージ時に自動ビルド。手動は Actions の `build-images` を dispatch）。詳細: [ghcr images runbook](../../../docs/runbooks/ghcr-images.md)

```bash
# CAE を作成（未実施の場合）。ACR は廃止（#67 / ADR 0013）— image は ghcr の事前ビルド済み。
az deployment group create \
  -g rg-dev-mind-inbox \
  -n main-bootstrap \
  -f cicd/modules/bootstrap-core.bicep \
  -p cicd/iac/main-bootstrap.parameters.json \
  -p enableAiAgentAca=true enableOpenAi=true
```

### デプロイ

```bash
RG=rg-dev-mind-inbox ./cicd/scripts/deploy/deploy-ai-agent.sh
```

### 動作確認

```bash
# FQDN を取得
FQDN=$(az containerapp show -g rg-dev-mind-inbox \
  -n $(az deployment group show -g rg-dev-mind-inbox -n main-bootstrap \
       --query 'properties.outputs.aiAgentContainerAppName.value' -o tsv) \
  --query 'properties.configuration.ingress.fqdn' -o tsv)

curl https://${FQDN}/health

curl -X POST https://${FQDN}/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-01", "message": "こんにちは"}'
```

---

## 環境変数

| 変数名                                                                                      | 必須 | 説明                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AZURE_OPENAI_ENDPOINT`                                                                     | △    | Azure OpenAI エンドポイント（ACA では managed identity で取得）                                                                                                                                            |
| `AZURE_OPENAI_DEPLOYMENT`                                                                   | -    | モデルデプロイ名（デフォルト: `gpt-4o`）                                                                                                                                                                   |
| `OPENAI_API_KEY`                                                                            | △    | OpenAI API キー（Azure を使わない場合）                                                                                                                                                                    |
| `USE_MANAGED_IDENTITY`                                                                      | -    | ACA での managed identity 使用フラグ（デフォルト: `false`）                                                                                                                                                |
| `LOG_LEVEL`                                                                                 | -    | ログレベル（デフォルト: `INFO`）                                                                                                                                                                           |
| `COSMOS_ENDPOINT`                                                                           | -    | Cosmos DB エンドポイント（#188 / ADR 0030）。**未設定なら in-memory で動く**（ローカル既定）。設定時はセッション・承認レコード・MAF checkpoint を TTL 付きコンテナへ永続化（認証は Managed Identity のみ） |
| `COSMOS_DATABASE`                                                                           | -    | Cosmos DB データベース名（デフォルト: `mindinbox`）                                                                                                                                                        |
| `COSMOS_SESSIONS_CONTAINER` / `COSMOS_APPROVALS_CONTAINER` / `COSMOS_CHECKPOINTS_CONTAINER` | -    | コンテナ名（デフォルト: `sessions` / `approvals` / `checkpoints`。bicep の宣言と揃える）                                                                                                                   |
