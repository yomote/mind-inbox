# Mind Inbox — Frontend

React 19 + Vite + MUI の SPA。BFF (tRPC) だけを知り、AI Agent / VOICEVOX のトポロジーは見ない。
プロジェクト全体は [ルート README](../../README.md) を参照。

## 起動

```bash
pnpm install
VITE_USE_MOCK=true pnpm dev   # モック: BFF も認証も不要 (→ http://localhost:5173/)
pnpm dev                      # 実 BFF に接続 (localhost:7071 が要る)
```

BFF・VOICEVOX まで含めた起動は [ローカルフルスタック起動 Runbook](../../docs/runbooks/local-fullstack-dev.md)。

```bash
pnpm test          # vitest (L1)
pnpm test:e2e      # Playwright (L3 / mock)
pnpm lint
pnpm build         # tsc -b && vite build
```

## 構成

| ディレクトリ        | 役割                                                                              |
| ------------------- | --------------------------------------------------------------------------------- |
| `src/api/`          | BFF クライアント。`VITE_USE_MOCK` で mockApi と実 BFF を分岐 / SSE ストリーミング |
| `src/consultation/` | 相談セッションの状態と操作 (`useConsultation`)                                    |
| `src/voice/`        | 音声入力 (Azure Speech / Web Speech) と読み上げ (VOICEVOX ↔ ブラウザ)             |
| `src/auth/`         | Entra ID (MSAL)                                                                   |
| `src/spec/`         | MDX UI 仕様のプレビュー (`docs/frontend/ui_specs/` が真実)                        |
| `src/mockApi.ts`    | 全画面分のモックデータ。**テストの共通 fixture でもある**                         |

## 前提として効いている判断

- **UI 仕様は MDX が真実** — 実装と乖離したら実装を直す ([ADR 0005](../../docs/adr/0005-mdx-ui-spec-as-truth.md))
- **mockApi.ts がフロントの真実** — テストごとに別 mock を増やさない ([ADR 0004](../../docs/adr/0004-mockapi-as-frontend-truth.md))
- **応答は SSE で流す** — tRPC の外に側路を持つ ([ADR 0024](../../docs/adr/0024-chat-streaming-via-sse-side-channel.md))

## 環境変数

| 変数                                                                     | 用途                                   |
| ------------------------------------------------------------------------ | -------------------------------------- |
| `VITE_USE_MOCK`                                                          | `true` で BFF・認証なしの自己完結デモ  |
| `VITE_BFF_BASE_URL` / `VITE_API_BASE_URL`                                | BFF の宛先 (未設定なら同一オリジン)    |
| `VITE_VOICEVOX_SPEAKER`                                                  | 話者 ID (既定 3 = ずんだもん)          |
| `VITE_ENTRA_CLIENT_ID` / `VITE_ENTRA_TENANT_ID` / `VITE_ENTRA_API_SCOPE` | Entra ID (SPA)。モックモードでは未使用 |

ビルド時に焼き込まれる (`cicd/scripts/deploy/deploy-frontend.sh`)。
