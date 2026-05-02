q# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Mind Inbox** は、AIとの対話をエフェメラルなチャット体験ではなく、累積的な自己理解アーティファクトへ変換するアプリ。コアコンセプト: "モヤモヤを話す → AIが構造化する → 自己理解の地図として育つ"

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
