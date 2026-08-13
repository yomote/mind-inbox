# CLAUDE.md

**このファイルはエージェント向けの作業規約**。プロダクトの説明・構成・起動手順は [README.md](README.md) が入口 (ここには再掲しない)。

> **`AGENTS.md` との関係**: Codex など `AGENTS.md` を読むエージェント向けに、実装時に効く規約だけを抜き出した [`AGENTS.md`](AGENTS.md) を置いている。**このファイルが正典**で、規約を変えたら AGENTS.md も同じ PR で直す。

**成果物は日本語で書く** — PR タイトル・本文、コミットメッセージ、コードコメント、ドキュメント、Issue コメント。コード中の識別子は英語。

## このファイルに何を書くか

**毎セッション・毎ターン読まれる**ので、置いてよいのは 1 種類だけ:

> **破っても気づけない / 壊れてから気づくルール。**

作業を始めた瞬間に気づくもの (ADR の書き方・作業の分け方・マージの可否) は **skill** にある。自分では気づけないもの (自分の仕事の審査) は **subagent** にある。

**ここに新しいルールを足すときは、既存の 1 行を消すか、skill に置けないかを先に考える。** 追記だけを続けたので 191 行まで膨れ、履歴が本文に埋まって「今のルール」が読めなくなった。

### 作業に入る前に呼ぶ skill

| skill | いつ |
| --- | --- |
| `/adr` | ADR を書く / 採番する / 棚を触る |
| `/dispatch` | 作業を分ける / 子セッション・subagent を起こす |
| `/merge` | PR を出したあと / マージしてよいかを判断する |
| `/design-gate` | 新機能・Phase 着手・ADR 級判断の**実装を始める前** |
| `/debrief` `/briefing` | マージや Proposed ADR が溜まった / リリース級の節目 |
| `/release-gate` | リリース PR (`main → release`) の Go/No-Go |
| `/status` `/explain` `/po-feedback` | 戦況図 / 「あれなんだっけ」/ 指示の出し方の講評 |

**独立に走らせられる筋を先に数えてから着手する** (詳細は `/dispatch`)。窓口セッションで直列に回さない。

## 破ると気づけないこと

### 事実の扱い

- **取れなかったものを「異常なし」と書かない** — このリポジトリで最も繰り返している事故。取得・検証に失敗したら成功と区別できる形で出す (`未検証: 理由` / status を error にする / run を落とす)。握り潰し (`2>/dev/null` / `|| true` / 空の catch) を足すときは、**それで何が見えなくなるか**をコメントに書く
- **自動テストが緑でも「動かせば見つかる」層は残る** — 実際に叩いた結果を PR に貼る。「設定したか」ではなく**振る舞い**で書く。UI 変更はローカル (mock + 認証なし) でブラウザ確認する
- **判定の 1 行を壊してテストが落ちることを確認してから「テスト済み」と言う** — データの文字列を assert しているだけのテストは、ロジックが壊れても気づけない

### 真実の所在 (取り違えると静かに壊れる)

- **`mockApi.ts` は mock 兼テスト fixture** — テストごとに別 mock を増やさない ([ADR 0004](docs/adr/0004-mockapi-as-frontend-truth.md))
- **UI 仕様は MDX が真実** — 乖離したら実装を直す ([ADR 0005](docs/adr/0005-mdx-ui-spec-as-truth.md))
- **型は tRPC の zod / pydantic が真実** — OpenAPI は生成物なので手書きせず再生成する
- **実行状態 (計画・進捗) は GitHub Issues** — docs は「なぜ/何を」、Issues は「いつ/誰が/今どこ」。**open Issue にはちょうど 1 個の `stream:*` ラベル**を付ける (`product` / `improve-loop` / `concept` / `factory` / `infra`。迷ったら `product`。定義は [`streams.json`](cicd/scripts/status-page/streams.json))
- **自動化の生死は <https://yomote.github.io/mind-inbox/status/>** が見る場所 (GitHub の実データから毎回生成。手書きの台帳は作らない)。**自動化を足したら [`watchers.json`](cicd/scripts/status-page/watchers.json) に 1 行足す。足せないなら作らない**

### 壊しやすい不変条件

- **BFF は chat の素通しではない** — アーティファクト生成を組み立てる。副作用ツールは `requiresApproval` で人間に返す
- **stub fallback を壊さない** — `AI_AGENT_BASE_URL` / `VOICEVOX_BASE_URL` 未設定でも BFF は動く (ローカルで外部サービス無しに触れる特性)
- **SWA は Free、フロントは Functions を直叩き** (linked backend は使わない)。認可は Functions EasyAuth の 401 が担う
- **image は ghcr に事前ビルド、デプロイは不変 sha タグの差し替え**。ACR は廃止、`:latest` は使わない ([ADR 0025](docs/adr/0025-deploy-container-images-by-immutable-sha-tag.md))
- **Container Apps は scale-to-zero** (AKS ではない / [ADR 0002](docs/adr/0002-container-apps-not-aks.md)) で、**組み込み認証で閉じる** — IP 許可リストに戻さない ([ADR 0017](docs/adr/0017-container-apps-access-via-auth-gate.md))
- **SQL は既定で作らない** (`enableSql=false`)。有効化すると VNet + Private Endpoint 一式が付き待機課金が乗る

### テストを書く前に

正典は [`docs/testing/strategy.md`](docs/testing/strategy.md)。破ると気づけないのはこの 3 つ:

- **「無いと何が静かに通るか?」を 1 文で書けないテストは書かない。仕様を指せないテストも書かない** — 「仕様がない」と言う
- **判定はシェルや workflow の中に埋めず、純粋関数に切り出してテストする** — 状態・副作用を持つ新モジュールはテストファーストで切る。テストが書けない構成は設計の警報
- **`npm run test:fast` をローカルで緑にしてから PR を出す**

### ドキュメントを書く前に

正典は [`docs/documentation/strategy.md`](docs/documentation/strategy.md)。

- **アーキテクチャに関わる判断は ADR を先に書く** — 実装より前に。後から書くと意図が薄れる (書き方・採番は `/adr`)
- **運用手順は Runbook に集約する** — README や CLAUDE.md に書かない

## Commands

### リポジトリ全体 (まずこれ)

```bash
npm run test:fast   # bff / frontend / ai-agent / scripts を並列
npm run lint        # eslint + ruff + markdownlint
npm test            # test:contract → test:fast → test:e2e
```

`cicd/scripts/` 配下の Python テストは `npm run test:scripts` に登録する (登録しないと CI で走らない)。

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
pnpm --dir apps/frontend test:e2e                 # Playwright (旧 L3 mock — 廃止方針、新規シナリオを足さない)
pnpm --dir apps/frontend build                    # tsc -b && vite build
```

### AI Agent / VOICEVOX Wrapper (Python FastAPI)

```bash
cd apps/services/ai-agent && pip install -e .   # uv.lock が正典
uvicorn app.main:app --reload --port 8000

cd apps/services/voicevox && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001

cicd/scripts/local-voicevox/start-voicevox.sh   # Docker-based VOICEVOX
```

## Architecture

**構成と責務の正典は [`docs/design/basic_design.md`](docs/design/basic_design.md)** (ここには再掲しない)。踏み外しやすい不変条件は上の「壊しやすい不変条件」に置いた。

### Environment Variables (BFF)

| Variable                                  | Purpose                            | 未設定時                                      |
| ----------------------------------------- | ---------------------------------- | --------------------------------------------- |
| `AI_AGENT_BASE_URL`                       | AI Agent service URL               | stub 応答                                     |
| `VOICEVOX_BASE_URL`                       | VOICEVOX Wrapper URL               | `/api/tts` が 204 → ブラウザ読み上げへ        |
| `AI_AGENT_AUDIENCE` / `VOICEVOX_AUDIENCE` | Container Apps 認証の門 (ADR 0017) | Authorization を付けない (門の無いローカル用) |
| `SPEECH_RESOURCE_ID` / `SPEECH_REGION`    | Azure Speech STT (ADR 0023)        | `speech.issueToken` が `available:false`      |

**正典は [`apps/bff/local.settings.json.example`](apps/bff/local.settings.json.example)** — 増やしたらそちらを先に更新する。

## Azure Infrastructure

**2-phase Bicep** — `bootstrap` (`cicd/iac/main-bootstrap.bicep`) で全リソースを作り、`config` (`cicd/iac/main-config.bicep`) で Entra ID 認証と secret を入れる ([ADR 0003](docs/adr/0003-two-phase-bicep.md))。命名は `{resourcetype}-{env}-{appname}` (`func-dev-mindbox` 等)、環境は `dev` / `stg` / `prod`。

```bash
cicd/scripts/deploy/deploy-all.sh              # Frontend + BFF
cicd/scripts/deploy/deploy-frontend.sh         # SWA へ静的配信 (VITE_* をビルド時に焼き込み)
cicd/scripts/deploy/deploy-backend.sh          # BFF zip deploy to Functions
cicd/scripts/deploy/deploy-ai-agent.sh         # ghcr の image を Container App に差し替え
cicd/scripts/deploy/deploy-voicevox-wrapper.sh # ghcr の image を Container App に差し替え
cicd/scripts/smoke-test/smoke-test.sh          # Post-deploy verification
```

手順の正典は [`docs/runbooks/`](docs/runbooks/README.md)。

## 戦略 doc の入口

- [`docs/testing/strategy.md`](docs/testing/strategy.md) — 4 層のテスト階層 / 書く・書かない判断基準
- [`docs/documentation/strategy.md`](docs/documentation/strategy.md) — 真実の所在マトリクス / 生成物 commit ルール
- [`docs/design/`](docs/design/requirements.md) — 要件 → ユースケース → ドメインモデル → 実装計画
- [`docs/adr/README.md`](docs/adr/README.md) — アーキテクチャ判断の不変記録 (21 本)。**運用・プロセスの決め事は ADR ではない** — 過去に ADR として書かれた 29 本は [`docs/adr/archive/`](docs/adr/archive/README.md) にあり、現行ルールではない
- [`docs/debrief/journal.md`](docs/debrief/journal.md) — セッション記録
