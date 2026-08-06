# 0016. AI Agent のオーケストレーション基盤を Semantic Kernel から Microsoft Agent Framework へ移行する

- Status: Accepted (design-gate #1, 2026-08-06)
- Date: 2026-08-06
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: [ADR 0015](0015-proactive-agentic-workflow.md) (プロアクティブ・エージェントワークフロー解禁) の実行基盤選定。design-gate セッション (2026-08-06)。

## Context and Problem Statement

ADR 0015 のワークフロー (多段オーケストレーション・スケジュール実行・HITL 承認・ツール活用・ナレッジ層) を何の上に実装するかを決める。現行 AI Agent は Semantic Kernel (SK) を使うが、依存は薄い: `ChatHistory` と chat 呼び出しのラッパーのみで、FSM (`workflow.py`)・承認 (`ApprovalRepository`)・tools・RAG はすべて自前 (tools / RAG は PoC スタブのまま)。

一方 SK は Microsoft Agent Framework (MAF) への統合が確定した: MAF は SK + AutoGen の統合後継 (1.0 RC 2026-02, GA 2026-Q1 目標) で、**マルチエージェント・オーケストレーション、graph-based Workflows、checkpointing、human-in-the-loop、MCP 統合は MAF 側にのみ実装される**。SK のエージェント抽象は GA 後の維持モード (クリティカル修正中心)。

## Decision Drivers

- **ADR 0015 の要件を自前実装しない** — HITL・checkpoint・スケジュール可能な多段ワークフローを標準機能で得る
- **SK の将来性** — 新機能が入らない基盤の上に新ワークフローを積まない
- **移行コストが最小の今やる** — 現行 SK 依存が薄く、tools / RAG がスタブの今が最安の乗り換え時期
- **Azure スタックとの整合** — Azure OpenAI / Container Apps / (将来) Azure AI Search との統合導線
- **BFF 契約の不変** — `/chat` `/extract` `/plan` `/approve` の API 契約を変えず、OpenAPI で回帰検知できること

## Considered Options

- Option A: **SK 継続 + 自前 FSM 拡張**
- Option B: **MAF へ移行** (ChatAgent + `@ai_function` tools + graph-based Workflows)
- Option C: **LangGraph 等の他フレームワークへ移行**
- Option D: **フレームワークなしの完全自前オーケストレーション**

## Decision Outcome

Chosen option: **"Option B" (MAF へ移行)**。段階は 3 フェーズ:

| フェーズ | 内容 | 検証 |
| --- | --- | --- |
| **M1: 等価移行** | 現行 4 エンドポイントの中身を MAF に置換。自前 FSM → MAF Workflow、`ApprovalRepository` → MAF の HITL / checkpoint、tools を `@ai_function` 化 | BFF 契約不変を L2 + OpenAPI diff で検証 |
| **M2: ナレッジ層** | Mention / Problem の embedding 索引 (ADR 0015)。RAG スタブを実体化 | 索引の実体 (Azure AI Search 等の課金リソース) は着手前に再ゲート |
| **M3: プロアクティブ** | 棚卸し / ウォッチ / 深掘りワークフローをスケジュール実行に載せる (Magentic 型オーケストレーションは必要になった時点で) | ADR 0015 のガードレール準拠 |

Option A は HITL・checkpoint・スケジューラを自前で作り続けることになり Driver 1/2 に反する (現 FSM は 7 状態の直列で、0015 の並行・多段ワークフローに耐える設計ではない)。Option C は機能的には同等だが、Azure 統合と SK からの公式移行導線 (migration guide) の分だけ MAF に劣後し、エコシステムを分散させる。Option D は 0015 規模では保守不能。

### Positive Consequences

- HITL / checkpoint / 多段オーケストレーション / MCP を標準機能で得て、自前 FSM と承認基盤を廃止できる
- SK 維持モード化のリスクを今の最安タイミングで解消する
- BFF 契約不変の等価移行 (M1) を挟むことで、移行自体の回帰を既存テスト階層で検知できる
- ナレッジ層 (M2) がグルーピング精度向上 (Phase 2 の embedding 化) と同一基盤に乗る

### Negative Consequences

- GA 直後の若いフレームワークへの依存 (1.0 API 安定はコミット済みだが、周辺事例・エコシステムは薄い)
- `semantic-kernel` → `agent-framework` パッケージ差し替えと学習コスト
- M1 (等価移行) 自体はユーザー価値を生まない準備投資 (M2/M3 の前提として受け入れる)
- research preview 級の機能 (Magentic 等) は仕様変動リスクがある → M3 で必要になるまで採用を遅延

## Pros and Cons of the Options

### Option A: SK 継続 + 自前 FSM 拡張

- Good, because 移行作業ゼロで着手できる
- Bad, because HITL / checkpoint / スケジューラ / 並行オーケストレーションを全部自作する
- Bad, because 新機能が入らない基盤に新規投資を積む

### Option B: MAF へ移行 (採用)

- Good, because 0015 の要件が標準機能で揃い、自前基盤を廃止できる
- Good, because SK からの公式移行ガイドがあり、現依存が薄いため移行が小さい
- Bad, because GA 直後の若さ。実績・事例が薄い

### Option C: LangGraph 等

- Good, because graph オーケストレーション・checkpoint は同等に強い
- Bad, because Azure 統合・SK 移行導線で MAF に劣後し、スタックの一貫性を失う

### Option D: 完全自前

- Good, because 依存ゼロ・全制御
- Bad, because 0015 規模のワークフロー基盤の自作・保守は個人開発の範囲を超える

## Links

- 関連 ADR: [0015](0015-proactive-agentic-workflow.md) (ワークフロー要件 — 本 ADR の上位判断)
- 現行実装: `apps/services/ai-agent/app/workflow.py` (自前 FSM) / `tools.py` / `rag.py` (スタブ)
- MAF: [Overview](https://learn.microsoft.com/en-us/agent-framework/overview/) / [SK からの移行ガイド](https://learn.microsoft.com/en-us/agent-framework/migration-guide/from-semantic-kernel/)
