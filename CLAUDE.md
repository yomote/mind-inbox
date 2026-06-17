# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Mind Inbox** は、AIとの対話をエフェメラルなチャット体験ではなく、累積的な自己理解アーティファクトへ変換するアプリ。コアコンセプト: "モヤモヤを話す → AIが構造化する → 自己理解の地図として育つ"

## Working in this repo

### まず読む戦略 doc

- **テスト戦略**: [`docs/testing/strategy.md`](docs/testing/strategy.md) — L0〜L4 のテスト階層 / 書く・書かない判断基準 / PR・Issue テンプレ運用
- **ドキュメント戦略**: [`docs/documentation/strategy.md`](docs/documentation/strategy.md) — 真実の所在 (UI = MDX / API = OpenAPI / 判断 = ADR / 手順 = Runbook) / 生成物 commit ルール

### ドキュメント更新ルール

実装と並行して以下を更新する。PR テンプレ (`.github/PULL_REQUEST_TEMPLATE.md`) でもチェックリストで明示要求される。

- **アーキテクチャに関わる判断は ADR を先に書く** — `docs/adr/` に MADR 形式で。実装より前に書く。後から書くと意図が薄れる
- **OpenAPI は手書きしない** — `docs/api/{bff-trpc,ai-agent,voicevox}.yaml` は CI で再生成。実装側 (zod / pydantic) を直して再生成する
- **UI 仕様は MDX が真実** — `docs/frontend/ui_specs/*.mdx` を先に直す。実装が乖離したら実装を直す
- **運用手順は Runbook** — `docs/runbooks/` に集約。README 側に書かない

### テスト方針 (要点)

`docs/testing/strategy.md` 参照。要点だけ:

- **L2 結合を主戦場に、L1 単体は絞る** — agent は unit を機械的に通せるので、サービス結合層 (tRPC mutation / FastAPI endpoint) が回帰検知の主戦場 (§1.3)
- **「無いと何が静かに通るか?」を 1 文で書けないテストは書かない** (§1.2「書かない判断」)
- テスト名に `[L0]`/`[L1]`/`[L2]`/`[L3]` プレフィックスを付ける — CI sticky comment の集計と切り分けに使う (§1.3「失敗の局所化」)
- `npm run test:fast` をローカルで緑にしてから PR を出す

## Commands

### BFF (Azure Functions + tRPC)

```bash
cd apps/bff
npm install
npm run dev       # build:watch + func start (requires Azure Functions Core Tools)
npm run build     # tsc compile only
npm run lint      # ESLint
```

### Frontend (React + Vite)

```bash
cd apps/frontend
npm install
npm run dev       # vite dev server
npm run build     # tsc + vite build
npm run lint      # ESLint
```

### AI Agent (Python FastAPI + Semantic Kernel)

```bash
cd apps/services/ai-agent
pip install -e .
uvicorn app.main:app --reload --port 8000
```

### VOICEVOX Wrapper (Python FastAPI)

```bash
cd apps/services/voicevox
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

### Local VOICEVOX Engine

```bash
cicd/scripts/local-voicevox/start-voicevox.sh  # Docker-based VOICEVOX for development
```

## Architecture

### Monorepo Structure

```
apps/
  bff/          # Azure Functions v4 + tRPC — BFF layer
  frontend/     # React 19 + Vite + MUI — SPA
  services/
    ai-agent/   # FastAPI + Semantic Kernel — Azure OpenAI integration
    voicevox/   # FastAPI — VOICEVOX TTS wrapper
cicd/
  iac/          # Bicep IaC (2-layer: bootstrap → config)
  modules/      # Bicep modules
  scripts/      # Deploy, smoke-test, local dev scripts
docs/           # Concept deck, infra diagrams, UI specs (MDX)
```

### Request Flow

```
Browser → SWA (Static Web App)
       → Azure Functions BFF (/api/trpc/{path})
       → AI Agent service (Container App)
       → Azure OpenAI (GPT-4o)
       → VOICEVOX Wrapper (Container App) [optional audio]
       → VOICEVOX Engine (Container App)
```

### BFF: tRPC Router

- Single HTTP entry point: `apps/bff/src/functions/trpc.ts` → `/api/trpc/{path}`
- Router (`apps/bff/src/trpc/router.ts`) exposes `health` and `chat` subrouters
- `chat.sendMessage` mutation: `{ sessionId, message, withAudio? }` → `{ reply, requiresApproval, citations, audioUrl? }`
- AI Agent / VOICEVOX clients fall back to stubs when env vars are unset (safe for local dev without services running)

### Frontend Mock System

- `apps/frontend/src/mockApi.ts` provides full mock data for all screens
- UI specs live in `docs/frontend/ui_specs/` as MDX interactive previews
- Screens: onboarding, home, newConsultation, session, result, actionPlan, history, settings, paused, crisisSupport

### Environment Variables (BFF)

| Variable            | Purpose              | Fallback       |
| ------------------- | -------------------- | -------------- |
| `AI_AGENT_BASE_URL` | AI Agent service URL | Stub responses |
| `VOICEVOX_BASE_URL` | VOICEVOX Wrapper URL | Stub audio URL |

See `apps/bff/local.settings.json.example` for local dev template.

## Azure Infrastructure

### Two-Phase IaC (Bicep)

1. **bootstrap** (`cicd/iac/main-bootstrap.bicep`) — Creates all resources: SWA, Function App, SQL, Key Vault, Log Analytics, Container App environments, ACR
2. **config** (`cicd/iac/main-config.bicep`) — Entra ID auth + secrets (run after bootstrap)

### Resource Naming Convention

`{resourcetype}-{env}-{appname}` — e.g., `func-dev-mindbox`, `swa-dev-mindbox`
Environments: `dev` / `stg` / `prod`, default app name: `mind-box`

### Deployment Scripts

```bash
cicd/scripts/deploy/deploy-all.sh              # Frontend + BFF
cicd/scripts/deploy/deploy-frontend.sh         # SWA + Entra auth sync
cicd/scripts/deploy/deploy-backend.sh          # BFF zip deploy to Functions
cicd/scripts/deploy/deploy-ai-agent.sh         # Docker build → ACR → Container App
cicd/scripts/deploy/deploy-voicevox-wrapper.sh # Docker build → ACR → Container App
cicd/scripts/smoke-test/smoke-test.sh          # Post-deploy verification
```

## Key Design Decisions

- **BFF is NOT a chat passthrough** — it orchestrates artifact generation; `requiresApproval` flag enables human-in-the-loop tool approval flow in the AI Agent
- **tRPC** provides end-to-end type safety between frontend and BFF without code generation
- **SWA linked backend** uses Standard SKU to proxy API calls to Azure Functions with built-in auth
- **Container Apps** (not AKS) for services — serverless containers with scale-to-zero for cost control
- **Private endpoints** for SQL — network-isolated, accessed only from within VNet
