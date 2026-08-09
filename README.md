# Mind Inbox

**AI との対話を、使い捨てのチャットではなく「育つ自己理解の地図」に変える。**

モヤモヤを話す → AI が構造化する → 困りごと (Problem) がセッションを跨いで蓄積される。
同じ悩みを何度も話しているのに、何も残らない — その状態を構造で解く。

## このリポジトリの 2 つの顔

| 側面                    | 何をやっているか                                                                                                                                                                                                                                                                                                                                                           |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **AI ネイティブアプリ** | Azure OpenAI + VOICEVOX を使った音声対話アプリ。会話から困りごとを抽出・分類・追跡する ([ADR 0007](docs/adr/0007-problem-centric-two-layer-domain-model.md))                                                                                                                                                                                                               |
| **AI 駆動開発の実験場** | 実装のほぼ全量をコーディングエージェントが書き、人間は PO として判断だけを担う。理解ゲート・独立 judge・UX 自律改善ループなど、その運用そのものを設計対象にしている ([ADR 0014](docs/adr/0014-design-comprehension-gate-and-debrief.md) / [0019](docs/adr/0019-independent-judge-agents-security-qa-release.md) / [0022](docs/adr/0022-autonomous-ux-improvement-loop.md)) |

## 現在の完成範囲

| 段階                                    | 状態      | 内容                                                                                                                                 |
| --------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| PoC                                     | ✅ 完了   | 話す → 整理 → プラン生成が実 AI と一気通貫で疎通                                                                                     |
| v1 — Problem 中心 2層モデル             | ✅ 完了   | 困りごとが会話を跨いで残り、一覧・詳細・状態遷移ができる                                                                             |
| v2 — MAF 移行 + プロアクティブ          | 🚧 進行中 | オーケストレーションを Microsoft Agent Framework へ移行し、AI から働きかける ([#79](https://github.com/yomote/mind-inbox/issues/79)) |
| 常設 dev 環境 + main マージ自動デプロイ | ✅ 稼働中 | Azure 上に待機コスト最小の dev を常設 ([ADR 0013](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md))                      |

永続化は現在 in-memory。認証は Entra ID (SPA + Functions EasyAuth)。

## アーキテクチャ

```text
Browser ─→ Static Web Apps (React 19 + Vite + MUI)
        └─→ Azure Functions BFF (tRPC / 単一エントリ /api/trpc/*)
              ├─→ AI Agent    (Container Apps / FastAPI) ─→ Azure OpenAI
              └─→ VOICEVOX Wrapper (Container Apps) ─→ VOICEVOX Engine
```

- **BFF はチャットの素通しではない** — アーティファクト生成を組み立て、副作用ツールは人間の承認を挟む
- **tRPC** でフロント ↔ BFF の型をコード生成なしに共有する ([ADR 0001](docs/adr/0001-bff-as-trpc-not-rest.md))
- **Container Apps** を scale-to-zero で使い、image は ghcr に事前ビルド ([ADR 0002](docs/adr/0002-container-apps-not-aks.md) / [0013](docs/adr/0013-standing-low-cost-dev-env-with-auto-deploy.md))

## 最短で動かす (BFF も Azure も要らない)

```bash
pnpm --dir apps/frontend install
VITE_USE_MOCK=true pnpm --dir apps/frontend dev   # → http://localhost:5173/
```

モックモードは `mockApi.ts` で自己完結し、BFF・認証・課金リソースを一切必要としない ([ADR 0004](docs/adr/0004-mockapi-as-frontend-truth.md))。画面と動線はこれで一周できる。

**声と実 AI 応答まで見たい場合**は BFF と VOICEVOX が要る → [ローカルフルスタック起動 Runbook](docs/runbooks/local-fullstack-dev.md)

```bash
npm run test:fast   # BFF / frontend / ai-agent / scripts の単体・結合
npm run lint
```

## ドキュメントの入口

**まず読む 3 つ:**

| 知りたいこと           | 読む場所                                                                   |
| ---------------------- | -------------------------------------------------------------------------- |
| 何を作ろうとしているか | [コンセプト](docs/concept_deck.md) → [要件](docs/design/requirements.md)   |
| なぜこの構成なのか     | [ADR 一覧](docs/adr/README.md) — 覆す前に必ず読む不変の判断記録            |
| どう操作するか         | [Runbook 一覧](docs/runbooks/README.md) — デプロイ・認証・CD・レビュー運用 |

**その他:**

- [ドメインモデル](docs/design/domain_model.md) / [ユースケース](docs/design/use_cases.md) / [v2 実装計画](docs/design/implementation_plan_v2.md)
- [UI 仕様 (MDX が真実)](docs/frontend/ui_specs/) / [BFF API (OpenAPI は生成物)](docs/api/README.md)
- [テスト戦略](docs/testing/strategy.md) / [ドキュメント戦略](docs/documentation/strategy.md)
- [セッション記録 (何をなぜ決めたかの時系列)](docs/debrief/journal.md)
- 計画・進捗の現在地は **GitHub Issues** が真実 ([ADR 0011](docs/adr/0011-github-projects-as-execution-dashboard.md))

## 開発に参加する / エージェントを走らせる

エージェント向けの規約・コマンド・運用ループは [`CLAUDE.md`](CLAUDE.md) にまとめてある。人間が読む必要はないが、このリポジトリの開発がどう回っているかを知りたい場合はそこが実体。

## ライセンス

未設定 (private)。
