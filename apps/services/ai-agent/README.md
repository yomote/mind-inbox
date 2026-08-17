# AI Agent Service

Azure OpenAI を使ったチャットエージェント。FastAPI + Microsoft Agent Framework (MAF / ADR 0016) で実装。

## エンドポイント

| Method | Path           | 説明                                                                                            |
| ------ | -------------- | ----------------------------------------------------------------------------------------------- |
| GET    | `/health`      | ヘルスチェック                                                                                  |
| POST   | `/chat`        | メッセージ送信                                                                                  |
| POST   | `/chat/stream` | `/chat` の SSE ストリーミング版 (#120 / ADR 0024)。失敗時は error イベントで伝える              |
| POST   | `/extract`     | 会話ログから問題 (problem) を構造化抽出 (#183)。パース失敗は 502 で「0 件」と区別する           |
| POST   | `/plan`        | 問題からプラン (次の一歩) を生成 (#227)。パース失敗は 502 で固定文言の偽プランと区別する (#485) |
| POST   | `/approve`     | ツール呼び出し承認/拒否。処理済みへの再送は 409 (二重送信と喪失を混ぜない)                      |

この表はテスト (`tests/test_readme_machine_check.py`) が FastAPI の `app.routes` と機械照合している — 実装とズレると CI が赤になる。

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

この表はテスト (`tests/test_readme_machine_check.py`) が `app/config.py` の `Settings` と機械照合している — フィールドを足したら / 消したらこの表も直さないと CI が赤になる。1 行 1 変数（機械照合のため、複数変数を 1 行にまとめない）。

| 変数名                               | 必須 | 説明                                                                                                                                                                                                       |
| ------------------------------------ | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AZURE_OPENAI_ENDPOINT`              | △    | Azure OpenAI エンドポイント（ACA では managed identity で取得）                                                                                                                                            |
| `AZURE_OPENAI_API_KEY`               | -    | Azure OpenAI API キー（デフォルト: 空）。managed identity を使わずにキーで叩くローカル用。ACA では使わない（キー認証は disableLocalAuth で無効）                                                           |
| `AZURE_OPENAI_DEPLOYMENT`            | -    | モデルデプロイ名（デフォルト: `gpt-4o`）                                                                                                                                                                   |
| `AZURE_OPENAI_RESPONSES_API_VERSION` | -    | Responses API の api-version（デフォルト: `preview` = MAF の既定に合わせる）。chat completions 用の api-version とは**別物** — 共用すると `/extract` が 400 → 常時 500 になった実績あり（2026-08-10）      |
| `OPENAI_API_KEY`                     | △    | OpenAI API キー（Azure を使わない場合）                                                                                                                                                                    |
| `OPENAI_MODEL`                       | -    | OpenAI フォールバック時のモデル名（デフォルト: `gpt-4o`）                                                                                                                                                  |
| `USE_MANAGED_IDENTITY`               | -    | ACA での managed identity 使用フラグ（デフォルト: `false`）                                                                                                                                                |
| `LOG_LEVEL`                          | -    | ログレベル（デフォルト: `INFO`）                                                                                                                                                                           |
| `APP_NAME`                           | -    | アプリ名（デフォルト: `mind-inbox-ai-agent`）。FastAPI title と起動/終了ログに使う                                                                                                                         |
| `LLM_EXPOSED_TOOLS`                  | -    | **LLM に見せるツール（デフォルト: 空 = 1 本も見せない / #320・#321）**。`*` で `app/tools.py` の registry 全部、`a,b` で名前指定。registry に無い名前はエラー（綴り間違いを黙って無視しない）              |
| `LLM_MAX_FUNCTION_CALLS`             | -    | 1 リクエストで許すツール実行の総回数（デフォルト: `4`）。MAF の既定は**無制限**なので必ず明示する                                                                                                          |
| `LLM_MAX_TOOL_ITERATIONS`            | -    | 1 リクエストで許すモデル往復の上限（デフォルト: `4`）                                                                                                                                                      |
| `LLM_REQUEST_TIMEOUT_SECONDS`        | -    | LLM への 1 回の HTTP 試行の上限秒（デフォルト: `60` / #313）。「遅いが正常」を切らずにぶら下がりだけを切る線                                                                                               |
| `LLM_TOTAL_TIMEOUT_SECONDS`          | -    | リトライ込みの 1 回の LLM 呼び出しの実時間上限秒（デフォルト: `120` / #313）。**上流 Functions の 230s より必ず先に切れる**ことが条件                                                                      |
| `LLM_STREAM_IDLE_TIMEOUT_SECONDS`    | -    | ストリーミングのチャンク間無音の上限秒（デフォルト: `45` / #313）。総時間ではなく無音で測る（長い応答を正常に流し切るため）。最初のトークンまでの待ちもこの上限                                            |
| `COSMOS_REQUEST_TIMEOUT_SECONDS`     | -    | Cosmos への永続化 I/O のタイムアウト秒（デフォルト: `20` / #313）。ここが詰まると `/chat` 全体が詰まるので LLM より短く切る                                                                                |
| `COSMOS_ENDPOINT`                    | -    | Cosmos DB エンドポイント（#188 / ADR 0030）。**未設定なら in-memory で動く**（ローカル既定）。設定時はセッション・承認レコード・MAF checkpoint を TTL 付きコンテナへ永続化（認証は Managed Identity のみ） |
| `COSMOS_DATABASE`                    | -    | Cosmos DB データベース名（デフォルト: `mindinbox`）                                                                                                                                                        |
| `COSMOS_SESSIONS_CONTAINER`          | -    | セッションコンテナ名（デフォルト: `sessions`。bicep の宣言と揃える）                                                                                                                                       |
| `COSMOS_APPROVALS_CONTAINER`         | -    | 承認レコードコンテナ名（デフォルト: `approvals`。bicep の宣言と揃える）                                                                                                                                    |
| `COSMOS_CHECKPOINTS_CONTAINER`       | -    | MAF checkpoint コンテナ名（デフォルト: `checkpoints`。bicep の宣言と揃える）                                                                                                                               |

> **注**: `Settings` にはこのほか `VOICEVOX_URL` / `VOICEVOX_ENABLED` が定義されているが、**参照箇所ゼロの未使用フィールドで #489 で削除予定**のため表には載せない（照合テスト側でも明示的に除外している。#489 が着地したらテストの allowlist ごと消すこと）。
