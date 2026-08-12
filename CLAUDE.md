# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**このファイルはエージェント向けの作業規約**。プロダクトの説明・構成・起動手順は [README.md](README.md) が入口 (ここには再掲しない)。

> **`AGENTS.md` との関係**: Codex など `AGENTS.md` を読むエージェント向けに、実装時に効く規約だけを抜き出した [`AGENTS.md`](AGENTS.md) を置いている。**このファイルが正典**で、規約を変えたら AGENTS.md も同じ PR で直す (食い違うと、別系統のエージェントが違うルールで実装する)。

## Working in this repo

### まず読む戦略 doc

- **テスト戦略**: [`docs/testing/strategy.md`](docs/testing/strategy.md) — 4 層 (契約 / 単体 / スモーク / E2E) のテスト階層 / 書く・書かない判断基準 / PR・Issue テンプレ運用
- **ドキュメント戦略**: [`docs/documentation/strategy.md`](docs/documentation/strategy.md) — 真実の所在 (UI = MDX / API = OpenAPI / 判断 = ADR / 手順 = Runbook) / 生成物 commit ルール
- **プロダクト設計 (v1)**: [`docs/design/`](docs/design/requirements.md) — [要件](docs/design/requirements.md) → [ユースケース](docs/design/use_cases.md) → [ドメインモデル](docs/design/domain_model.md) → [v1 実装計画 (archive)](docs/design/archive/implementation_plan_v1.md) → [v2 計画](docs/design/implementation_plan_v2.md)。**Problem 中心 2層モデル (Mention → Problem)** が v1 の核 (ADR 0007)
- **アーキテクチャ判断 (ADR)**: [`docs/adr/`](docs/adr/README.md) — 過去の構成/技術選択の不変記録。覆す前に必ず読む。主要: [0001 tRPC](docs/adr/0001-bff-as-trpc-not-rest.md) / [0002 Container Apps](docs/adr/0002-container-apps-not-aks.md) / [0003 2-phase Bicep](docs/adr/0003-two-phase-bicep.md) / [0004 mockApi 真実](docs/adr/0004-mockapi-as-frontend-truth.md) / [0005 MDX 真実](docs/adr/0005-mdx-ui-spec-as-truth.md) / [0007 Problem 中心 2層](docs/adr/0007-problem-centric-two-layer-domain-model.md) / [0008 PR レビュー Routine](docs/adr/0008-pr-review-via-cloud-routine.md) / [0011 Projects=実行ダッシュボード](docs/adr/0011-github-projects-as-execution-dashboard.md) / [0013 常設 dev 環境+自動デプロイ](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md) / [0014 理解ゲート+デブリーフ](docs/adr/0014-design-comprehension-gate-and-debrief.md) / [0018 動作検証をループに組み込む](docs/adr/0018-runtime-verification-in-the-loop.md) / [0019 独立 judge (security/QA/release)](docs/adr/0019-independent-judge-agents-security-qa-release.md) / [0020 HITL 選択肢形式+needs-human](docs/adr/0020-hitl-choice-format-and-needs-human-queue.md) / [0021 hub-and-spoke セッション運用](docs/adr/0021-parent-session-as-pm-orchestrator.md)
- **自動化の生死**: <https://yomote.github.io/mind-inbox/status/> が「今どれが動いていて何が問題か」を見る場所 (GitHub の実データから毎回生成。手書きの台帳は作らない)。運用は [Runbook](docs/runbooks/status-page.md)、監視対象の定義は [`cicd/scripts/status-page/watchers.json`](cicd/scripts/status-page/watchers.json)。**自動化を足したら watchers.json に 1 行足す。足せないなら作らない**。新設の必須条件は「動いたら痕跡がリポジトリに残ること」(異常時だけ喋る設計にすると、沈黙と正常が区別できなくなる)
- **実行状態 (計画・進捗)**: GitHub Issues が真実 ([ADR 0011](docs/adr/0011-github-projects-as-execution-dashboard.md))。docs は「なぜ/何を」、Issues は「いつ/誰が/今どこ」。全体の地図は **固定 5 レーン (`stream:*` ラベル)** = 戦況図 ([ADR 0044](docs/adr/0044-stream-lanes-as-the-project-map.md))。**open Issue にはちょうど 1 個の `stream:*` を付ける** (`product` / `improve-loop` / `concept` / `factory` / `infra`。判定は「閉じたとき変わるのはプロダクトの挙動か、改善を回す機械か」、迷ったら `product`)。レーン定義は [`streams.json`](cicd/scripts/status-page/streams.json)。**戦況図を今すぐ見る手段は `/status` skill** — [status ページ](https://yomote.github.io/mind-inbox/status/)への描画は **#289 で実装予定** (それまでページに 5 レーンは出ず、自動化の生死のみ)。**Projects board は退役** (再建しない / [Runbook](docs/runbooks/github-projects-setup.md) は歴史的経緯)
- **PO 裁定は 1 回 3 件まで** ([ADR 0044](docs/adr/0044-stream-lanes-as-the-project-map.md) D3) — 「🙋 あなたの番」は最重要 3 件を**レーン文脈つき**で出し (どのレーンが止まる / 推奨 / 先送りの代償)、残りは「寝かせ中 n 件」と件数だけ。全部並べない

### ドキュメント更新ルール

正典: [`docs/documentation/strategy.md`](docs/documentation/strategy.md) の「真実の所在マトリクス」(§2) と「エージェントが間違えやすい点」(§7.2)。実装と並行して更新する。毎回効く 3 つ:

- **アーキテクチャに関わる判断は ADR を先に書く** — `docs/adr/` に MADR 形式で。実装より前に。後から書くと意図が薄れる
- **エージェント起案の ADR は `Status: Proposed` で入れる** — `Accepted` へ遷移させるのは user のみ (design-gate / debrief の場で)。Proposed の判断を前提に実装を進めてよいが、承認キューとして残す ([ADR 0014](docs/adr/0014-design-comprehension-gate-and-debrief.md))
- **生成物と真実を取り違えない** — OpenAPI は手書きせず再生成 (真実は zod / pydantic)、UI 仕様は MDX が真実 (乖離したら実装を直す)、運用手順は Runbook に集約 (README に書かない)

### テスト方針 (要点)

正典: [`docs/testing/strategy.md`](docs/testing/strategy.md)。毎回効く要点だけ:

- **4 層 (契約 / 単体 / スモーク / E2E) で守る** (§2)。単体は入場条件「壊れても例外が出ず、データが静かに間違う」を満たすところだけに書き、**例ではなく性質 (プロパティ) で書くのが既定** (§2.2 / §3)。新規テスト名に `[契約]`/`[単体]`/`[スモーク]`/`[E2E]` プレフィックス (§1.3 — 既存の `[L0]`〜`[L3]` の読み替えと移行は §6)
- **「無いと何が静かに通るか?」を 1 文で書けないテストは書かない** (§1.2)。**仕様を指せないテストも書かない — 「仕様がない」と言う** (§3.4)
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
- **セッションは hub-and-spoke で運用する** ([ADR 0021](docs/adr/0021-parent-session-as-pm-orchestrator.md)) — user の対話窓口は親 (PM) セッション 1 本。独立した並行作業は親が子セッションへ分配する (起票プロンプトに対象 Issue / 完遂条件 / **触ってはいけないファイル境界** / CLAUDE.md 参照を必ず含める)。子は user に直接報告せず PR / Issue / needs-human に残し、親が GitHub のライブ状態から集約して報告する。design-gate 対象の設計判断は子に分配せず親でゲートを通してから分配する。**窓口は常に 1 本・使い捨てローテーション** (節目やコンテキスト劣化時に次代を開いて移り、旧窓口は退役)。タイトル `[PM] Mind Inbox ハブ (YYYY-MM-DD〜)` / 旧窓口の `[PM-retired]` 化は**推奨・必須ではない** (2026-08-11 PO 決定 — 下の既定 PM 化により「一覧からタイトルで窓口を探す」必要が「素で開いた最新セッションが窓口」に変わったため。ADR 0021 条項 6 の運用改訂として**次回 debrief で裁定する** — ADR 0040 は報告会 #8 (2026-08-11) でこの条項を含まずに Accept されたため、裁定先は 0040 ではなく ADR 0021 の運用改訂として残っている)。タイトル変更はエージェントから叩けないので、付ける場合は user が貼る。子の命名規約は無い。
- **user が対話で開いた新セッションは、既定で窓口 PM として振る舞う** (2026-08-11 PO 決定) — 起票パケット (対象 Issue / 完遂条件 / ファイル境界) や当番 Routine のプロンプトを与えられていない対話セッションは、user の最初のメッセージが何であれ (挨拶だけでも)、窓口 PM の運転から始める: GitHub のライブ状態 (open PR / needs-human / Proposed ADR / 自動起票 Issue) を復元し、冒頭「🙋 あなたの番」付きの /status 報告を出してから用件に入る。**起動プロンプトのコピペも `[PM]` タイトルの付与も不要** (タイトル規約は上の項のとおり推奨どまり)。複数の対話セッションを同時に開かないのは user 側の運用前提 (開いてしまった場合、古い方は続けず閉じる)
- **分配の基準は「作業の大きさ」** ([ADR 0033](docs/adr/0033-parent-implements-via-subagent-when-child-sessions-are-gated.md) / 2026-08-10 改訂) — この実行環境では `create_session` が承認ゲートで弾かれ子セッションを起動できない。**レビュー指摘への対応・1〜2 ファイルの修正・設定調整は親が直接書く**。**新規モジュール / 複数ファイル / 調査を伴う / 並行したい作業は subagent (`isolation: "worktree"`) に出す** — 出す理由は独立性ではなく**親のコンテキストの経済**(実装者とレビュアーの分離は Codex が担う / ADR 0035 D4)。**user にクリックを肩代わりさせない**。`create_session` が通る環境では子セッションへ分配してよい。subagent への指示文は起票パケットの要件 (対象 Issue / 完遂条件 / **触ってはいけないファイル境界** / CLAUDE.md 参照) をそのまま満たすこと
- セッション記録は [`docs/debrief/journal.md`](docs/debrief/journal.md)

### PR を出したあとの追従

PR を作成したら放置せず、**merge / close されるまで追従する**。

- PR を作ったら `subscribe_pr_activity` で監視を有効化する
- レビュー ([ADR 0008](docs/adr/0008-pr-review-via-cloud-routine.md) の Routine 含む) や CI コメントが付いたら調査し、**小さく確実な修正は push**、曖昧 / 重大な指摘は確認を取る。**再レビューが Resolve するまで追う**
- **レビュースレッドを resolve してよいのは、指摘者 (Codex) の再レビューが OK を出してから** (2026-08-11 PO 決定) — 修正 push → `@codex review` で再レビュー依頼 → **同じ指摘が再提起されないことを確認してから** resolve する。操作は PM、判定は指摘者。修正せず見送る場合 (別 Issue へ切り出し等) に再レビューでも再提起されたら、独断で畳まず PO に上げる。docs のみの PR 等 Codex レビュー対象外の指摘 (セルフレビュー・PM レビュー) は従来どおり対応確認で resolve してよい
- webhook は CI 成功・新規 push・マージ遷移を配信しないので、定期チェックインで取りこぼしを補い、merge / close で監視を終える
- **「緑になったらマージ」の主経路は GitHub の auto-merge** — 受け入れ (pm-accept) まで済ませたら `enable_pr_auto_merge` (squash) を掛けて終わってよい。マージはサーバー側で行われ、**セッションの生死に依存しない**。required check (CI + review-gate) が門を守る。常設承認の例外 (リリース PR / needs-human 等) には掛けないこと。リポ設定の有効化は Issue #234
- **定期チェックインは auto-merge の補助** (レビュー対応の取りこぼし確認など)。`send_later` ではなく `CronCreate` を使う (MCP 側は承認ゲートに当たる / [ADR 0031](docs/adr/0031-agent-reaches-outside-via-github-actions.md) D6) が、**CronCreate はセッション内メモリでありセッション終了と共に消える** — 2026-08-10 に PR #230 が「全 check 緑のままマージされず一晩放置」される実害が出た。チェックインだけに完遂を依存させない

#### マージの常設承認 (2026-08-09 PO 決定)

**main への PR は、CI と `review-gate` ([ADR 0036](docs/adr/0036-merge-gate-as-required-check-and-pm-cadence.md)) がともに緑ならエージェントがマージしてよい。** レビュー指摘の解決・PM 受け入れ・(コード PR の) Codex レビューの有無は review-gate が機械判定する — マージ可否を明文の解釈ではなく check の色で読む。都度の確認は不要 — 毎回聞かれる方が PO のコストになる、という判断。マージ後は関連 Issue の close と持ち越しの確認まで済ませる。

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
pnpm --dir apps/frontend test:e2e                 # Playwright (旧 L3 mock — 廃止方針、新規シナリオを足さない / strategy.md §6.3)
pnpm --dir apps/frontend build                    # tsc -b && vite build
```

### AI Agent (Python FastAPI + Microsoft Agent Framework / [ADR 0016](docs/adr/0016-ai-agent-orchestration-on-maf.md) — M1 移行完了)

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
