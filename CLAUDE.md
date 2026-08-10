# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**このファイルはエージェント向けの作業規約**。プロダクトの説明・構成・起動手順は [README.md](README.md) が入口 (ここには再掲しない)。

## Working in this repo

### まず読む戦略 doc

- **テスト戦略**: [`docs/testing/strategy.md`](docs/testing/strategy.md) — L0〜L4 のテスト階層 / 書く・書かない判断基準 / PR・Issue テンプレ運用
- **ドキュメント戦略**: [`docs/documentation/strategy.md`](docs/documentation/strategy.md) — 真実の所在 (UI = MDX / API = OpenAPI / 判断 = ADR / 手順 = Runbook) / 生成物 commit ルール
- **プロダクト設計 (v1)**: [`docs/design/`](docs/design/requirements.md) — [要件](docs/design/requirements.md) → [ユースケース](docs/design/use_cases.md) → [ドメインモデル](docs/design/domain_model.md) → [v1 実装計画 (archive)](docs/design/archive/implementation_plan_v1.md) → [v2 計画](docs/design/implementation_plan_v2.md)。**Problem 中心 2層モデル (Mention → Problem)** が v1 の核 (ADR 0007)
- **アーキテクチャ判断 (ADR)**: [`docs/adr/`](docs/adr/README.md) — 過去の構成/技術選択の不変記録。覆す前に必ず読む。主要: [0001 tRPC](docs/adr/0001-bff-as-trpc-not-rest.md) / [0002 Container Apps](docs/adr/0002-container-apps-not-aks.md) / [0003 2-phase Bicep](docs/adr/0003-two-phase-bicep.md) / [0004 mockApi 真実](docs/adr/0004-mockapi-as-frontend-truth.md) / [0005 MDX 真実](docs/adr/0005-mdx-ui-spec-as-truth.md) / [0007 Problem 中心 2層](docs/adr/0007-problem-centric-two-layer-domain-model.md) / [0008 PR レビュー Routine](docs/adr/0008-pr-review-via-cloud-routine.md) / [0011 Projects=実行ダッシュボード](docs/adr/0011-github-projects-as-execution-dashboard.md) / [0013 常設 dev 環境+自動デプロイ](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [0014 理解ゲート+デブリーフ](docs/adr/0014-design-comprehension-gate-and-debrief.md) / [0018 動作検証をループに組み込む](docs/adr/0018-runtime-verification-in-the-loop.md) / [0019 独立 judge (security/QA/release)](docs/adr/0019-independent-judge-agents-security-qa-release.md) / [0020 HITL 選択肢形式+needs-human](docs/adr/0020-hitl-choice-format-and-needs-human-queue.md) / [0021 hub-and-spoke セッション運用](docs/adr/0021-parent-session-as-pm-orchestrator.md)
- **自動化の生死**: <https://yomote.github.io/mind-inbox/status/> が「今どれが動いていて何が問題か」を見る場所 (GitHub の実データから毎回生成。手書きの台帳は作らない)。運用は [Runbook](docs/runbooks/status-page.md)、監視対象の定義は [`cicd/scripts/status-page/watchers.json`](cicd/scripts/status-page/watchers.json)。**自動化を足したら watchers.json に 1 行足す。足せないなら作らない**。新設の必須条件は「動いたら痕跡がリポジトリに残ること」(異常時だけ喋る設計にすると、沈黙と正常が区別できなくなる)
- **実行状態 (計画・進捗)**: GitHub Issues + Projects が真実 ([ADR 0011](docs/adr/0011-github-projects-as-execution-dashboard.md))。docs は「なぜ/何を」、Projects は「いつ/誰が/今どこ」。**board に設計内容は書かない** (doc へのリンクのみ)。セットアップは [Runbook](docs/runbooks/github-projects-setup.md)

### ドキュメント更新ルール

正典: [`docs/documentation/strategy.md`](docs/documentation/strategy.md) の「真実の所在マトリクス」(§2) と「エージェントが間違えやすい点」(§7.2)。実装と並行して更新する。毎回効く 3 つ:

- **アーキテクチャに関わる判断は ADR を先に書く** — `docs/adr/` に MADR 形式で。実装より前に。後から書くと意図が薄れる
- **エージェント起案の ADR は `Status: Proposed` で入れる** — `Accepted` へ遷移させるのは user のみ (design-gate / debrief の場で)。Proposed の判断を前提に実装を進めてよいが、承認キューとして残す ([ADR 0014](docs/adr/0014-design-comprehension-gate-and-debrief.md))
- **生成物と真実を取り違えない** — OpenAPI は手書きせず再生成 (真実は zod / pydantic)、UI 仕様は MDX が真実 (乖離したら実装を直す)、運用手順は Runbook に集約 (README に書かない)

### テスト方針 (要点)

正典: [`docs/testing/strategy.md`](docs/testing/strategy.md)。毎回効く要点だけ:

- **L2 結合を主戦場に、L1 単体は絞る**。テスト名に `[L0]`/`[L1]`/`[L2]`/`[L3]` プレフィックスを付ける (§1.3)
- **「無いと何が静かに通るか?」を 1 文で書けないテストは書かない** (§1.2)
- **状態・副作用を持つ新モジュールはテストファーストで切る。テストが書けない構成は設計の警報** (§1.4)
- `npm run test:fast` をローカルで緑にしてから PR を出す
- **自動テストが緑でも「動かせば見つかる」層は残る** — 実際に叩いた結果を PR に貼る。「設定したか」ではなく**振る舞い**で書く ([ADR 0018](docs/adr/0018-runtime-verification-in-the-loop.md))。UI 変更はローカル (mock + 認証なし) でブラウザ確認する

### 理解ゲートとデブリーフ (ループ運用)

user の意思決定と技術学習をループに組み込む仕組み ([ADR 0014](docs/adr/0014-design-comprehension-gate-and-debrief.md))。

- **設計 → 実装の境界では必ず `design-gate` skill を通す** — 新機能 / Phase 着手 / ADR 級判断の実装を始める前に、設計を可視化して user に提示し、理解確認の対話と**明示的な承認**を取る。承認前に実装に入らない (バグ修正・既承認設計内の作業は対象外)
- **マージ / Proposed ADR が溜まったら `debrief` skill** — ゼミ形式で「何を作ったか / なぜ / 代替案」を解説し、Proposed ADR を user が Accept/Reject する。溜まっていたらエージェントから提案してよい
- **リリース級の節目は `briefing` skill (報告会)** — 音声ナレーション付きスライドでエージェントが説明し切り、PO は聞き流しながら随時質問するだけ (Issue #116 対策)。「資料を読んでおいて」で終えない。承認事項は選択肢形式でその場で取る
- **「あれなんだっけ?」には `explain` skill** — 真実ソースを引いて図解で即答する
- **無人セッション (Routine 等) ではゲートを通せない** — 不可逆な判断 (DB スキーマ破壊的変更 / 外部サービス・課金追加 / 公開 API の形 / データ削除) は実装せず Issue に質問を積む。可逆な判断は Proposed ADR を書いて進め、次の debrief で追認を受ける
- **リリースは「リリース PR (`main → release`)」で表現し、そこで `release-gate` skill を通す** — 実装セッション自身に Go/No-Go を判定させず、独立 judge (いずれも新品コンテキストの subagent: security-reviewer / qa-reviewer / biz-owner-reviewer / release-judge) に判定させる。**判断は [ADR 0019](docs/adr/0019-independent-judge-agents-security-qa-release.md)、運用手順は [Runbook](docs/runbooks/review-agents.md)、各 judge の観点は `.github/claude/*-rubric.md` (+ 共通規約 `_common.md`) が正典**。main への機能 PR / dev の日常 auto-deploy は対象外。blocker はリリース PR のスレッド + ブランチ保護でマージ不可。🟢 でも merge / deploy は人間
- **user (PO) の指示の出し方へのフィードバックは `po-feedback` skill** — 実セッションの証拠ベースで辛口レビュー。debrief の締めに 1 コーナーとして回すのが既定
- **人間の確認は選択肢形式・宿題は needs-human キュー** ([ADR 0020](docs/adr/0020-hitl-choice-format-and-needs-human-queue.md)) — 解釈確認・設計選択・承認は散文に埋めず AskUserQuestion (クリック選択式) で出す。人間にしかできない宿題 (web UI 設定・ADR Accept 等) は発生時点で `needs-human` ラベルの Issue に積む。`/status` は冒頭に「🙋 あなたの番」(needs-human 残 + Proposed ADR 残) を必ず出す。未確認のまま進む場合はその旨を明示して記録を残す
- **セッションは hub-and-spoke で運用する** ([ADR 0021](docs/adr/0021-parent-session-as-pm-orchestrator.md)) — user の対話窓口は親 (PM) セッション 1 本。独立した並行作業は親が子セッションへ分配する (起票プロンプトに対象 Issue / 完遂条件 / **触ってはいけないファイル境界** / CLAUDE.md 参照を必ず含める)。子は user に直接報告せず PR / Issue / needs-human に残し、親が GitHub のライブ状態から集約して報告する。design-gate 対象の設計判断は子に分配せず親でゲートを通してから分配する。**親はタイトル `[PM]` 接頭辞で常に 1 本・使い捨てローテーション** (節目やコンテキスト劣化時に次代を起動し `/status` で復元、旧親は `[PM-retired]` にして archive)。親のタイトルは **`[PM] Mind Inbox ハブ (YYYY-MM-DD〜)`** (user が一覧で窓口を見つけるため。**変更はエージェントから叩けない**ので user に貼る形で渡す)。子の命名規約は無い。
- **親は自分でキーボードを持たない** ([ADR 0033](docs/adr/0033-parent-implements-via-subagent-when-child-sessions-are-gated.md)) — この実行環境では `create_session` が承認ゲートで弾かれ子セッションを起動できないため、**実装は subagent (`isolation: "worktree"`) に回し、親が指示・レビュー・PR 作成・集約を持つ**。親が直接書いてよいのはプロセス docs (ADR / journal / CLAUDE.md / Runbook) のみ。**user にクリックを肩代わりさせない**。`create_session` が通る環境では従来どおり子セッションへ分配してよい (そちらが優れる)。subagent への指示文は起票パケットの要件 (対象 Issue / 完遂条件 / **触ってはいけないファイル境界** / CLAUDE.md 参照) をそのまま満たすこと
- セッション記録は [`docs/debrief/journal.md`](docs/debrief/journal.md)

### PR を出したあとの追従

PR を作成したら放置せず、**merge / close されるまで追従する**。

- PR を作ったら `subscribe_pr_activity` で監視を有効化する
- レビュー ([ADR 0008](docs/adr/0008-pr-review-via-cloud-routine.md) の Routine 含む) や CI コメントが付いたら調査し、**小さく確実な修正は push**、曖昧 / 重大な指摘は確認を取る。**再レビューが Resolve するまで追う**
- webhook は CI 成功・新規 push・マージ遷移を配信しないので、定期チェックインで取りこぼしを補い、merge / close で監視を終える
- **定期チェックインは `send_later` ではなく `CronCreate` を使う** — MCP 側は承認ゲートに当たり毎回確認を求められる ([ADR 0031](docs/adr/0031-agent-reaches-outside-via-github-actions.md) D6)

#### マージの常設承認 (2026-08-09 PO 決定)

**main への PR は、CI が緑でレビュー指摘が解決していればエージェントがマージしてよい。** 都度の確認は不要 — 毎回聞かれる方が PO のコストになる、という判断。マージ後は関連 Issue の close と持ち越しの確認まで済ませる。

**例外 (必ず人間が押す)**:

- **リリース PR (`main → release`)** — judge が 🟢 でも merge / deploy は人間 ([ADR 0019](docs/adr/0019-independent-judge-agents-security-qa-release.md))。この常設承認は**適用されない**
- `needs-human` ラベルの付いた PR / 未解決のレビュースレッドが残っている PR
- PO が明示的に「保留」と言った PR
- **`Status: Proposed` の ADR を実装まで含む PR** — ADR 本文のマージは可 (承認キューとして残す運用) だが、その判断に依存する**不可逆な実装**を含むなら裁定を待つ

## Commands

### リポジトリ全体 (まずこれ)

```bash
npm run test:fast   # bff / frontend / ai-agent / scripts を並列
npm run lint        # eslint + ruff + markdownlint
npm test            # test:contract → test:fast → test:e2e
```

### BFF (Azure Functions + tRPC)

```bash
cd apps/bff
npm install
npm run dev       # build:watch + func start (requires Azure Functions Core Tools)
npm run build     # tsc compile only
npm run lint      # ESLint
```

### Frontend (React + Vite) — **pnpm** (npm ではない / `pnpm-lock.yaml`)

```bash
pnpm --dir apps/frontend install
VITE_USE_MOCK=true pnpm --dir apps/frontend dev   # BFF も認証も不要のモック
pnpm --dir apps/frontend test                     # vitest
pnpm --dir apps/frontend test:e2e                 # Playwright (L3 / mock)
pnpm --dir apps/frontend build                    # tsc -b && vite build
```

### AI Agent (Python FastAPI + Semantic Kernel → MAF へ移行中 / [ADR 0016](docs/adr/0016-ai-agent-orchestration-on-maf.md))

```bash
cd apps/services/ai-agent
pip install -e .              # または uv (uv.lock が正典)
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

**構成と責務の正典は [`docs/design/basic_design.md`](docs/design/basic_design.md)** (ここには再掲しない)。作業中に踏み外しやすい不変条件だけ:

- **BFF は chat の素通しではない** — アーティファクト生成を組み立てる。副作用ツールは `requiresApproval` で人間に返す
- **stub fallback を壊さない** — `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` 未設定でも BFF は動く (ローカルで外部サービス無しに触れる特性)
- **`mockApi.ts` は mock 兼テスト fixture** — テストごとに別 mock を増やさない ([ADR 0004](docs/adr/0004-mockapi-as-frontend-truth.md))
- **UI 仕様は MDX が真実** — 乖離したら実装を直す ([ADR 0005](docs/adr/0005-mdx-ui-spec-as-truth.md))
- **型は tRPC の zod / pydantic が真実** — OpenAPI は生成物なので手書きしない

### Environment Variables (BFF)

| Variable                                  | Purpose                            | 未設定時                                      |
| ----------------------------------------- | ---------------------------------- | --------------------------------------------- |
| `AI_AGENT_BASE_URL`                       | AI Agent service URL               | stub 応答                                     |
| `VOICEVOX_BASE_URL`                       | VOICEVOX Wrapper URL               | `/api/tts` が 204 → ブラウザ読み上げへ        |
| `AI_AGENT_AUDIENCE` / `VOICEVOX_AUDIENCE` | Container Apps 認証の門 (ADR 0017) | Authorization を付けない (門の無いローカル用) |
| `SPEECH_RESOURCE_ID` / `SPEECH_REGION`    | Azure Speech STT (ADR 0023)        | `speech.issueToken` が `available:false`      |

**正典は [`apps/bff/local.settings.json.example`](apps/bff/local.settings.json.example)** — 増やしたらそちらを先に更新する。

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
cicd/scripts/deploy/deploy-frontend.sh         # SWA へ静的配信 (VITE_* をビルド時に焼き込み)
cicd/scripts/deploy/deploy-backend.sh          # BFF zip deploy to Functions
cicd/scripts/deploy/deploy-ai-agent.sh         # ghcr の事前ビルド image を Container App に差し替え
cicd/scripts/deploy/deploy-voicevox-wrapper.sh # ghcr の事前ビルド image を Container App に差し替え
cicd/scripts/smoke-test/smoke-test.sh          # Post-deploy verification
```

### コストと公開面で覆さない前提

判断の理由は各 ADR。ここは「知らずに壊しやすい」ものだけ:

- **SWA は Free、フロントは Functions を直叩き** (linked backend は使わない)。認可は Functions EasyAuth の 401 が担う ([ADR 0013](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [Runbook](docs/runbooks/entra-spa-auth-and-budget.md))
- **image は ghcr に事前ビルド、デプロイは不変 sha タグの差し替え**。ACR は廃止、`:latest` は使わない ([ADR 0013](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [ADR 0025](docs/adr/0025-deploy-container-images-by-immutable-sha-tag.md) / [Runbook](docs/runbooks/ghcr-images.md))
- **Container Apps は scale-to-zero** (AKS ではない / [ADR 0002](docs/adr/0002-container-apps-not-aks.md))
- **SQL は既定で作らない** (`enableSql=false`)。有効化すると VNet + Private Endpoint 一式が付き待機課金が乗る
- **Container Apps は組み込み認証で閉じる** — IP 許可リストに戻さない ([ADR 0017](docs/adr/0017-container-apps-access-via-auth-gate.md))
