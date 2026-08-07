# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Mind Inbox** は、AIとの対話をエフェメラルなチャット体験ではなく、累積的な自己理解アーティファクトへ変換するアプリ。コアコンセプト: "モヤモヤを話す → AIが構造化する → 自己理解の地図として育つ"

## Working in this repo

### まず読む戦略 doc

- **テスト戦略**: [`docs/testing/strategy.md`](docs/testing/strategy.md) — L0〜L4 のテスト階層 / 書く・書かない判断基準 / PR・Issue テンプレ運用
- **ドキュメント戦略**: [`docs/documentation/strategy.md`](docs/documentation/strategy.md) — 真実の所在 (UI = MDX / API = OpenAPI / 判断 = ADR / 手順 = Runbook) / 生成物 commit ルール
- **プロダクト設計 (v1)**: [`docs/design/`](docs/design/requirements.md) — [要件](docs/design/requirements.md) → [ユースケース](docs/design/use_cases.md) → [ドメインモデル](docs/design/domain_model.md) → [v1 実装計画](docs/design/implementation_plan_v1.md)。**Problem 中心 2層モデル (Mention → Problem)** が v1 の核 (ADR 0007)
- **アーキテクチャ判断 (ADR)**: [`docs/adr/`](docs/adr/README.md) — 過去の構成/技術選択の不変記録。覆す前に必ず読む。主要: [0001 tRPC](docs/adr/0001-bff-as-trpc-not-rest.md) / [0002 Container Apps](docs/adr/0002-container-apps-not-aks.md) / [0003 2-phase Bicep](docs/adr/0003-two-phase-bicep.md) / [0004 mockApi 真実](docs/adr/0004-mockapi-as-frontend-truth.md) / [0005 MDX 真実](docs/adr/0005-mdx-ui-spec-as-truth.md) / [0007 Problem 中心 2層](docs/adr/0007-problem-centric-two-layer-domain-model.md) / [0008 PR レビュー Routine](docs/adr/0008-pr-review-via-cloud-routine.md) / [0011 Projects=実行ダッシュボード](docs/adr/0011-github-projects-as-execution-dashboard.md) / [0013 常設 dev 環境+自動デプロイ](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [0014 理解ゲート+デブリーフ](docs/adr/0014-design-comprehension-gate-and-debrief.md) / [0017 動作検証をループに組み込む](docs/adr/0017-runtime-verification-in-the-loop.md)
- **実行状態 (計画・進捗)**: GitHub Issues + Projects が真実 ([ADR 0011](docs/adr/0011-github-projects-as-execution-dashboard.md))。docs は「なぜ/何を」、Projects は「いつ/誰が/今どこ」。**board に設計内容は書かない** (doc へのリンクのみ)。セットアップは [Runbook](docs/runbooks/github-projects-setup.md)

### ドキュメント更新ルール

実装と並行して以下を更新する。PR テンプレ (`.github/PULL_REQUEST_TEMPLATE.md`) でもチェックリストで明示要求される。

- **アーキテクチャに関わる判断は ADR を先に書く** — `docs/adr/` に MADR 形式で。実装より前に書く。後から書くと意図が薄れる
- **エージェント起案の ADR は `Status: Proposed` で入れる** — `Accepted` へ遷移させるのは user のみ (design-gate / debrief の場で)。Proposed の判断を前提に実装を進めてよいが、承認キューとして残す ([ADR 0014](docs/adr/0014-design-comprehension-gate-and-debrief.md))
- **OpenAPI は手書きしない** — `docs/api/{bff-trpc,ai-agent,voicevox}.yaml` は CI で再生成。実装側 (zod / pydantic) を直して再生成する
- **UI 仕様は MDX が真実** — `docs/frontend/ui_specs/*.mdx` を先に直す。実装が乖離したら実装を直す
- **運用手順は Runbook** — `docs/runbooks/` に集約。README 側に書かない

### テスト方針 (要点)

`docs/testing/strategy.md` 参照。要点だけ:

- **L2 結合を主戦場に、L1 単体は絞る** — agent は unit を機械的に通せるので、サービス結合層 (tRPC mutation / FastAPI endpoint) が回帰検知の主戦場 (§1.3)
- **「無いと何が静かに通るか?」を 1 文で書けないテストは書かない** (§1.2「書かない判断」)
- テスト名に `[L0]`/`[L1]`/`[L2]`/`[L3]` プレフィックスを付ける — CI sticky comment の集計と切り分けに使う (§1.3「失敗の局所化」)
- `npm run test:fast` をローカルで緑にしてから PR を出す
- **自動テストが緑でも「動かせば見つかる」層は残る** — 実際に叩いた結果を PR に貼る。「設定したか」ではなく**振る舞い**で書く ([ADR 0017](docs/adr/0017-runtime-verification-in-the-loop.md))。UI 変更はローカル (mock + 認証なし) でブラウザ確認する

### 理解ゲートとデブリーフ (ループ運用)

user の意思決定と技術学習をループに組み込む仕組み ([ADR 0014](docs/adr/0014-design-comprehension-gate-and-debrief.md))。

- **設計 → 実装の境界では必ず `design-gate` skill を通す** — 新機能 / Phase 着手 / ADR 級判断の実装を始める前に、設計を可視化して user に提示し、理解確認の対話と**明示的な承認**を取る。承認前に実装に入らない (バグ修正・既承認設計内の作業は対象外)
- **マージ / Proposed ADR が溜まったら `debrief` skill** — ゼミ形式で「何を作ったか / なぜ / 代替案」を解説し、Proposed ADR を user が Accept/Reject する。溜まっていたらエージェントから提案してよい
- **「あれなんだっけ?」には `explain` skill** — 真実ソースを引いて図解で即答する
- **無人セッション (Routine 等) ではゲートを通せない** — 不可逆な判断 (DB スキーマ破壊的変更 / 外部サービス・課金追加 / 公開 API の形 / データ削除) は実装せず Issue に質問を積む。可逆な判断は Proposed ADR を書いて進め、次の debrief で追認を受ける
- セッション記録は [`docs/debrief/journal.md`](docs/debrief/journal.md)

### PR を出したあとの追従

PR を作成したら放置せず、**merge / close されるまで追従する**。

- PR を作ったら `subscribe_pr_activity` で監視を有効化する
- レビュー ([ADR 0008](docs/adr/0008-pr-review-via-cloud-routine.md) の Routine 含む) や CI コメントが付いたら調査し、**小さく確実な修正は push**、曖昧 / 重大な指摘は確認を取る。**再レビューが Resolve するまで追う**
- webhook は CI 成功・新規 push・マージ遷移を配信しないので、定期チェックインで取りこぼしを補い、merge / close で監視を終える

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

1. **bootstrap** (`cicd/iac/main-bootstrap.bicep`) — Creates all resources: SWA, Function App, Key Vault, Log Analytics, Container App environments (SQL 一式は `enableSql=true` の時だけ。ACR は廃止 — ADR 0013)
2. **config** (`cicd/iac/main-config.bicep`) — Entra ID auth + secrets (run after bootstrap)

### Resource Naming Convention

`{resourcetype}-{env}-{appname}` — e.g., `func-dev-mindbox`, `swa-dev-mindbox`
Environments: `dev` / `stg` / `prod`, default app name: `mind-box`

### Deployment Scripts

```bash
cicd/scripts/deploy/deploy-all.sh              # Frontend + BFF
cicd/scripts/deploy/deploy-frontend.sh         # SWA + Entra auth sync
cicd/scripts/deploy/deploy-backend.sh          # BFF zip deploy to Functions
cicd/scripts/deploy/deploy-ai-agent.sh         # ghcr の事前ビルド image を Container App に差し替え
cicd/scripts/deploy/deploy-voicevox-wrapper.sh # ghcr の事前ビルド image を Container App に差し替え
cicd/scripts/smoke-test/smoke-test.sh          # Post-deploy verification
```

## Key Design Decisions

- **BFF is NOT a chat passthrough** — it orchestrates artifact generation; `requiresApproval` flag enables human-in-the-loop tool approval flow in the AI Agent
- **tRPC** provides end-to-end type safety between frontend and BFF without code generation
- **SWA linked backend** uses Standard SKU to proxy API calls to Azure Functions with built-in auth
- **Container Apps** (not AKS) for services — serverless containers with scale-to-zero for cost control
- **コンテナ image は ghcr に事前ビルド** — GitHub Actions (`build-images.yml`) が main マージ時に build/push し、デプロイはタグ差し替えのみ。ACR は廃止 ([ADR 0013](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [Runbook](docs/runbooks/ghcr-images.md))
- **Private endpoints** for SQL — network-isolated, accessed only from within VNet (SQL は `enableSql=true` の時のみ。既定は未プロビジョニング)
