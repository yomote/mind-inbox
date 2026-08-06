# Mind Inbox — v2 実装方針計画（MAF 移行とプロアクティブ基盤）

作成: 2026-08-06 / 対象: [ADR 0015](../adr/0015-proactive-agentic-workflow.md)（能動化・思想転換）+ [ADR 0016](../adr/0016-ai-agent-orchestration-on-maf.md)（SK → MAF 移行）を実装に落とすロードマップ
承認: design-gate #1（2026-08-06, [journal](../debrief/journal.md)）で両 ADR とも Accepted
関連: [`implementation_plan_v1.md`](./implementation_plan_v1.md)（v1 = Problem 中心 2層の実装。完了）

---

## 0. 前提と方針

### 0.1 現状 → v2 のギャップ

| レイヤ | 現状 | v2 要件 | ギャップ |
| --- | --- | --- | --- |
| オーケストレーション | 手書き FSM（`workflow.py`, 7状態直列） | graph Workflow + checkpoint | MAF へ置換（M1） |
| HITL 承認 | 自前 `ApprovalRepository` | MAF 組み込み HITL | MAF へ置換（M1） |
| LLM 呼び出し | SK `ChatHistory` + 単発 structured | MAF ChatAgent / structured output | MAF へ置換（M1） |
| tools | PoC スタブ（FAQ / inbox） | `@ai_function`、read / write 区分 | 置換 + 実体化（M1 / M3） |
| ナレッジ | `rag.py` スタブ | Mention / Problem embedding 索引 + RAG | 新規（M2・**再ゲート**） |
| プロアクティブ | なし（受動のみ） | 内部整理 / ウォッチ / 深掘り + 受信箱 | 新規（M3） |
| スケジュール実行 | なし | 定期ワークフロー実行基盤 | 新規（M3） |

### 0.2 実装原則（v1 計画を踏襲 + M1 特有）

- **変化は 1 度に 1 種類** — M1（基盤差し替え）に機能追加を混ぜない。移行のバグと機能のバグを切り分け可能に保つ
- **BFF 契約は M1 で不変** — `/chat` `/extract` `/organize` `/plan` `/approve` の I/O を変えない。`docs/api/ai-agent.yaml` の再生成 diff = 0 が M1 の合格ライン
- **防御仕様は仕様として維持** — 壊れた LLM JSON → 空結果 / 未知テーマ → 未分類 / confidence clamp。移行前にテストで金網を張ってから中身を差し替える
- **stub fallback 維持** — `AI_AGENT_BASE_URL` 未設定でも BFF が動く特性を壊さない
- **課金・外界は再ゲート** — M2 の索引リソース選定・M3 の通知チャネル / 外部 write は着手前に design-gate（ADR 0015/0016 で合意済み）

### 0.3 スコープ外

- Magentic 型オーケストレーション — M3 で必要になるまで採用遅延（ADR 0016）
- 外部サービスへの write 連携（タスク同期等）— 個別再ゲート
- Problem 永続化（Cosmos 等）— v1 からの持ち越し課題。M2 の索引設計と同時に検討

---

## 1. 実装ステップ全体像

```text
M1: 等価移行（ユーザーに見える変化なし。契約不変が合格ライン）
 ├─ M1-1. agent-framework 導入 + クライアント初期化の置換
 ├─ M1-2. 単発呼び出し系の移行（/extract /organize /plan）+ 防御仕様のテスト固定
 ├─ M1-3. /chat + /approve: FSM → MAF Workflow / ApprovalRepository → HITL checkpoint
 ├─ M1-4. tools の @ai_function 化（read/write 区分維持）
 └─ M1-5. SK 依存除去 + OpenAPI diff 0 + smoke で完了確認

M2: ナレッジ層（着手前に design-gate 再ゲート — 課金リソース選定を含むため）
 ├─ M2-1. Mention / Problem の embedding 索引
 ├─ M2-2. グルーピングの embedding 化（v1 計画の「Phase 2」を吸収）
 └─ M2-3. RAG 実体化（rag.py スタブ → 索引接続）

M3: プロアクティブ（ADR 0015 の 3 系統 + ガードレール）
 ├─ M3-1. スケジュール実行基盤（実体は着手時に選定）
 ├─ M3-2. 内部整理ワークフロー（棚卸し / 再グルーピング / ダイジェスト。可逆のみ = G3）
 ├─ M3-3. 受信箱 + 気づきカード（UI は MDX 先行 / ADR 0005）
 ├─ M3-4. ウォッチ + 深掘り質問生成（プッシュ + 頻度予算 = G2。チャネル実装は再ゲート）
 └─ M3-5. 外部データ read-only ツール接続
```

PR 分割の目安: M1-1+M1-2 / M1-3 / M1-4+M1-5 の 3 PR。各 PR で契約不変（diff 0 + 既存 L2 緑）を示す。

---

## 2. M1: 等価移行（詳細）

### M1-1. 依存差し替えと初期化

- **変更対象**: `apps/services/ai-agent/pyproject.toml`（`agent-framework` 追加）/ `app/kernel.py` → `app/agents.py`（Azure OpenAI クライアント + ChatAgent 初期化）。環境変数は既存の Azure OpenAI 設定を流用（新規シークレットなし）
- **要点**: SK と MAF の併存を許す過渡期を作らず、モジュール単位で置き換える
- **完了条件**: `/health` 起動 + 既存テストが緑のまま

### M1-2. 単発呼び出し系の移行

- **変更対象**: `extractor.py` / `organizer.py` / `planner.py`
- **要点**: `ChatHistory` 整形 → MAF の message 型へ。structured 出力は MAF の機構に乗せるが、**防御層（`_coerce_theme` / `_coerce_affect` / `_clamp_confidence` / JSON 失敗 → 空結果）は仕様として維持**。先に防御仕様の L1/L2 テストが無ければ足してから移行する（金網 → 差し替えの順）
- **完了条件**: 3 エンドポイントの L2 緑 + `ai-agent.yaml` 再生成 diff 0

### M1-3. /chat + /approve の移行

- **変更対象**: `workflow.py`（FSM 廃止 → MAF Workflow）/ `repositories.py`（`ApprovalRepository` 廃止）
- **要点**: RECEIVE→…→RESPOND の 7 状態を graph Workflow に写す。APPROVAL_IF_NEEDED の「中断 → BFF へ requiresApproval 返却 → /approve で再開」を **MAF の checkpoint + HITL** に置換。`approvalRequestId` は checkpoint 参照への写像とし、**API 契約は 1 ミリも変えない**
- **完了条件**: 承認フロー（要承認ツール → 承認 / 却下 → 応答）の L2 が現行と同一挙動で緑

### M1-4. tools の @ai_function 化

- **変更対象**: `tools.py`
- **要点**: `kernel_function` → `@ai_function`。**read-only / side-effecting の区分をメタデータとして維持**（G1 = HITL の判定源）。中身は引き続きスタブで良い（実体化は M3-5）。`rag.py` はインターフェース不変のままスタブ維持（M2-3 で実体化）
- **完了条件**: 承認要否の判定が現行と同一

### M1-5. 検証・後片付け

- **変更対象**: `pyproject.toml`（`semantic-kernel` 除去）/ CI
- **完了条件（= ADR 0016 M1 の完了条件）**: ① `semantic-kernel` への import が 0 件 ② ai-agent テスト全緑 + BFF の L2 緑 ③ `docs/api/ai-agent.yaml` diff 0 ④ dev へデプロイして `smoke-test.sh` 通過

---

## 3. M2: ナレッジ層（着手前に再ゲート）

- **内容**: Mention / Problem の embedding 索引、グルーピングの embedding 化、RAG 実体化。v1 計画で「Phase 2」と呼んでいた宿題を吸収する
- **再ゲート対象**: 索引の実体選定（Azure AI Search 等 = **課金リソース**）。選択肢と待機コストを可視化して design-gate にかける
- **着手条件**: M1 完了 + design-gate 通過

## 4. M3: プロアクティブ

- ADR 0015 の 3 系統（内部整理 / 働きかけ / 継続するリアクティブ）を実装。ガードレール: G1 副作用 HITL / G2 プッシュ + 頻度予算・静穏時間・カテゴリ設定 / G3 内部整理は可逆のみ
- 受信箱・気づきカードの UI は **MDX 先行**（ADR 0005）→ mock（ADR 0004）→ 結線、の v1 と同じ順
- スケジュール実行基盤の実体（Container Apps Jobs 等)・プッシュ通知チャネルの実装方式は着手時に選定（通知チャネルと外部 write は個別再ゲート）

---

## 5. テスト / docs 方針（v2 での適用）

- **M1 の主戦場は「回帰検知」**: 既存 L2 を書き換えずに通すこと自体が移行の検証。新規テストは防御仕様の固定（M1-2）だけ先に足す
- **OpenAPI は手書きしない**（既存ルール）: `ai-agent.yaml` の diff 0 を M1 各 PR のチェック項目にする
- **M3 の新画面は MDX 先行**、mockApi 拡張 → 結線の順（v1 の Phase D→C と同型）
- **Runbook**: デプロイ・運用手順に変更が出た時点で更新

## 6. 宿題（着手時に詰める）

- MAF structured output の具体 API（RC → GA の差分吸収。M1-2 着手時に最新ドキュメントで確認）
- `approvalRequestId` ↔ checkpoint 参照の写像詳細（M1-3）
- Problem 永続化と M2 索引の関係（同一ストアか分離か。M2 再ゲートで一緒に判断）
- スケジュール実行基盤の実体（M3-1）
