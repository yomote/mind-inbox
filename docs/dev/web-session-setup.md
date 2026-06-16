# Claude Code on the web — 開発環境セットアップ

スマホ／ブラウザの [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web)
から Mind Inbox を開発するための環境構成メモ。各セッションは**使い捨てコンテナ**で
動き、リポジトリは毎回クリーンに clone される。依存は `SessionStart` フックで自動
インストールされる。

## 自動セットアップ（リポジトリ側）

- フック: `.claude/hooks/session-start.sh`（`.claude/settings.json` で登録）
- セッション開始時に同期実行され、以下を導入する:
  - ルート tooling（`npm install`）
  - BFF（`npm --prefix apps/bff install`）
  - Frontend（`pnpm --dir apps/frontend install`）
  - AI Agent（`uv sync --dev`）
  - VOICEVOX wrapper（Python 3.12 venv + `pip install -r requirements.txt`）
  - Azure Functions Core Tools（`func` — BFF を実起動する場合に必要）
- 非機密のサービス間ワイヤリング（`AI_AGENT_BASE_URL`, `VOICEVOX_BASE_URL`）を
  `$CLAUDE_ENV_FILE` に書き込む。

このフックを **デフォルトブランチ (main) にマージすると、以降の全 web セッションで
自動適用**される。

## 手動セットアップ（リポジトリ外＝web 環境側で設定が必要）

フックでは解決できない。Claude Code on the web の環境作成／設定で行う。

### 1. シークレット（実 LLM を使う場合は必須）

AI Agent が実際に Azure OpenAI / OpenAI を呼ぶには、API キーを **web 環境のシークレット**
として登録する。`pydantic-settings` が OS 環境変数を直接読むため、`.env` に書かなくても
反映される。

- Azure OpenAI を使う場合:
  - `AZURE_OPENAI_ENDPOINT`（例 `https://<name>.openai.azure.com/`）
  - `AZURE_OPENAI_API_KEY`
  - `AZURE_OPENAI_DEPLOYMENT`（既定 `gpt-4o`）
- OpenAI を使う場合（`AZURE_OPENAI_ENDPOINT` 未設定時のフォールバック）:
  - `OPENAI_API_KEY`

### 2. ネットワークポリシー

環境作成時に選ぶアウトバウンド許可。最低限、依存インストール用に **npm registry / PyPI**、
実 LLM 呼び出し用に **`api.openai.com`** または **`*.openai.azure.com`** への送信を許可する
ポリシーが必要。詳細は
[ネットワークポリシーのドキュメント](https://code.claude.com/docs/en/claude-code-on-the-web)。

## 環境変数一覧

| 変数                       | アプリ   | 必須/任意 | 既定・フォールバック         | 用途                          |
| -------------------------- | -------- | --------- | ---------------------------- | ----------------------------- |
| `VITE_USE_MOCK`            | frontend | 任意      | `false`                      | true=mockApi, false=BFF tRPC  |
| `VITE_BFF_BASE_URL`        | frontend | 任意      | 空（Vite proxy→:7071）       | dev で BFF を直接指定         |
| `VITE_VOICEVOX_SPEAKER`    | frontend | 任意      | `3`                          | TTS 話者 ID                   |
| `AI_AGENT_BASE_URL`        | bff      | 任意      | 未設定=stub 応答             | AI Agent サービス URL         |
| `VOICEVOX_BASE_URL`        | bff      | 任意      | 未設定=204→Web Speech        | VOICEVOX wrapper URL          |
| `FUNCTIONS_WORKER_RUNTIME` | bff      | 必須      | `node`                       | Functions v4 ランタイム       |
| `AzureWebJobsStorage`      | bff      | 任意(dev) | `UseDevelopmentStorage=true` | ストレージ接続                |
| `AZURE_OPENAI_ENDPOINT`    | ai-agent | 任意\*    | 空→OpenAI へフォールバック   | Azure OpenAI エンドポイント   |
| `AZURE_OPENAI_API_KEY`     | ai-agent | 任意\*    | 空                           | Azure OpenAI キー             |
| `AZURE_OPENAI_DEPLOYMENT`  | ai-agent | 任意      | `gpt-4o`                     | デプロイ名                    |
| `AZURE_OPENAI_API_VERSION` | ai-agent | 任意      | `2024-02-01`                 | API バージョン                |
| `USE_MANAGED_IDENTITY`     | ai-agent | 任意      | `false`                      | Managed Identity 使用         |
| `OPENAI_API_KEY`           | ai-agent | 任意\*    | 空                           | OpenAI キー（フォールバック） |
| `OPENAI_MODEL`             | ai-agent | 任意      | `gpt-4o`                     | OpenAI モデル名               |
| `LOG_LEVEL`                | ai-agent | 任意      | `INFO`                       | ログレベル                    |
| `VOICEVOX_ENGINE_BASE_URL` | voicevox | 任意      | `http://localhost:50021`     | VOICEVOX エンジン URL         |
| `PORT`                     | voicevox | 任意      | `8080`（dev は 8001 で起動） | wrapper ポート                |

\* 実 LLM 呼び出しには Azure 系 **または** `OPENAI_API_KEY` のいずれかが必須。
未設定でも BFF の stub 経由で UI 開発は可能。

テンプレート: `apps/frontend/.env.local.example` /
`apps/bff/local.settings.json.example` / `apps/services/ai-agent/.env.example` /
`apps/services/voicevox/.env.example`。

## フルスタックを手元で起動する（参考）

```bash
# AI Agent（要 LLM キー）
cd apps/services/ai-agent && uvicorn app.main:app --reload --port 8000

# VOICEVOX wrapper（任意。エンジン本体は Docker 必要 = web コンテナでは原則範囲外）
cd apps/services/voicevox && uvicorn app.main:app --reload --port 8001

# BFF（要 azure-functions-core-tools）
cd apps/bff && npm run dev          # func start (:7071)

# Frontend
cd apps/frontend && npm run dev     # vite (:5173) → /api を :7071 にプロキシ
```

VOICEVOX エンジン本体（`:50021`）は Docker 必須のため web コンテナでは通常起動しない。
その場合 `VOICEVOX_BASE_URL` 未設定でフロントは Web Speech にフォールバックする。
