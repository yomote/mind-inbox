# Mind Inbox — v1 実装方針計画（Problem 中心 2層モデル）

作成: 2026-06-22 / 対象: [ADR 0007](../adr/0007-problem-centric-two-layer-domain-model.md) の 2層モデル（Mention → Problem）を実装に落とすロードマップ
関連: [`requirements.md`](./requirements.md) / [`use_cases.md`](./use_cases.md) / [`domain_model.md`](./domain_model.md) / PoC: [`implementation_plan.md`](./implementation_plan.md)

---

## 0. 前提と方針

### 0.1 現状（PoC）→ v1 のギャップ

PoC は **Session 中心**（`OrganizedResult.priorities: string[]` / `HistoryItem`）。v1 は **Problem 中心 2層**（Mention → Problem）。

| レイヤ | PoC 現状 | v1 要件 | ギャップ |
| --- | --- | --- | --- |
| ドメイン型 | ChatMessage / ConsultationSession / OrganizedResult / HistoryItem | + **Mention** / **Problem** / Theme | 新規 |
| AI Agent | `/chat` `/organize` `/plan` `/approve` | + `/extract`（Dump→Mention[]）+ グルーピング + テーマ分類 | 新規エンドポイント |
| グルーピング | なし | 意味類似で既存 Problem に寄せる（v1 は簡易、embedding は Phase 2） | 新規（v1 簡易） |
| BFF ルーター | `consultation.*` / `history.*` | + `problem.*` / `mention.*`（list/get/create/triage/plan） | 新規 |
| BFF リポジトリ | History（in-memory） | + Mention / Problem リポジトリ（in-memory） | 新規 |
| Frontend 画面 | onboarding/home/session/result/actionPlan/history/settings | + 困りごと一覧 / 詳細 / トリアージ | 新規（UI 設計要） |
| Frontend mock | mockApi（Session/History） | + Mention/Problem mock | 追加 |

### 0.2 実装原則（PoC 計画を踏襲）

- **mock 先行で新体験を可視化** — フロントの `mockApi.ts` に Mention/Problem を入れ、新画面を mock で動かしてから BFF を繋ぐ（[ADR 0004](../adr/0004-mockapi-as-frontend-truth.md)）。
- **UI は MDX が真実** — 新画面は `docs/frontend/ui_specs/*.mdx` を先に書く（[ADR 0005](../adr/0005-mdx-ui-spec-as-truth.md)）。
- **既存 PoC を壊さない併存移行** — `consultation.*`（吐き出し対話）は残し、その出力を Mention 抽出に接続する。`history.*` は Problem 一覧に置き換わるが段階的に。
- **stub fallback 維持** — `AI_AGENT_BASE_URL` 未設定でも BFF が動く特性を保ち、各段を単独 smoke test。
- **グルーピングは v1 簡易 → Phase 2 で embedding** — v1 はルールベース/スタブのグルーピングで **2層構造とトリアージ UX を確立**し、類似精度は Phase 2 に回す。

### 0.3 スコープ外（次フェーズ）

- 永続化（Cosmos DB / Redis）— Phase 2（v1 は in-memory）
- embedding 意味類似の本実装 — Phase 2
- プロアクティブ（定期再グルーピング / ウォッチ / アナウンス）— Phase 4（[`requirements.md §2.2`](./requirements.md)）

---

## 1. 実装ステップ全体像

```text
Phase D: 型 & モック先行（フロントだけで新体験が見える）
 ├─ D1. ドメイン型定義（Mention / Problem / Theme）
 ├─ D2. UI 仕様 MDX（困りごと一覧 / 詳細 / トリアージ）← 先に書く
 ├─ D3. mockApi に Mention/Problem モック + 新画面の mock 挙動
 └─ D4. 新画面実装（mock で動作）: 一覧(UC-02) / 詳細 / 棚卸し(UC-04) / トリアージ

Phase A: AI Agent（抽出 + グルーピング + テーマ分類）
 ├─ A1. /extract（Dump → Mention[]、§9-2 粒度ルール）
 ├─ A2. グルーピング（v1 簡易: 既存 Problem へ寄せる / 新規を起こす）
 └─ A3. テーマ分類（固定7分類 + 未分類）

Phase B: BFF（新ルーター + in-memory リポジトリ）
 ├─ B1. Mention / Problem リポジトリ（in-memory）
 ├─ B2. aiAgentClient に extract/group 追加（stub fallback）
 └─ B3. tRPC: problem.list / problem.get / mention.create / problem.triage / problem.createPlan

Phase C: 結線・後片付け
 ├─ C1. フロント api/ 層を mock→real に切り替え
 ├─ C2. 3モード smoke test（stub のみ / AI Agent / フルスタック）
 └─ C3. consultation.organize → /extract 接続、history.* の段階廃止
```

各ステップは「変更対象」「動作確認」「完了条件」で定義する（着手時に詳細化）。**Phase D を 1 PR**にまとめ、mock で新体験が成立することを確認してから Phase A 以降へ進む。

---

## 2. Phase D: 型 & モック先行

### D1. ドメイン型定義

- **変更対象**: `apps/bff/src/trpc`（型のソース。basic_design §8.3 に従い型はここから import）に `Mention` / `Problem` / `Theme` を追加。
- **要点**: `domain_model.md §2` の属性に従う。`Theme` は固定7分類 + `未分類` の union 型。
- **完了条件**: フロント / BFF 双方から参照できる型が定義され、コンパイルが通る。

### D2. UI 仕様 MDX（先に書く）

- **変更対象**: `docs/frontend/ui_specs/` に `problem-list.mdx` / `problem-detail.mdx` / `triage.mdx` を新設。
- **要点**: UC-02（一覧: テーマ/状態/再出現回数で並べる）/ UC-03（再出現の気づき提示）/ UC-04（棚卸し）/ トリアージ（分割・統合・別 Problem 化・再リンク）の画面を仕様化。
- **完了条件**: 新画面の MDX プレビューがレビュー可能。

### D3. mockApi 拡張

- **変更対象**: `apps/frontend/src/mockApi.ts`。
- **要点**: 再出現を含む Problem（複数 Mention を持つ）・テーマ分布・棚卸し済みなどを網羅するモックを用意。トリアージ操作も mock で状態が変わるように。
- **完了条件**: BFF なしで一覧→詳細→トリアージ→棚卸しが一周する。

### D4. 新画面実装（mock 動作）

- **変更対象**: `apps/frontend/src/components/screens/`（新規）+ `Router.tsx` / `Layout.tsx`。
- **完了条件**: `VITE_USE_MOCK=true` で UC-02/03/04 + トリアージが触れる。

---

## 3. Phase A: AI Agent（抽出 + グルーピング + テーマ分類）

### A1. `/extract`（Dump → Mention[]）

- **変更対象**: `apps/services/ai-agent/app/`（`extractor.py` 新設 + `main.py` にエンドポイント）。
- **要点**: 粒度は「独立して再出現・独立して解決しうるか」（domain_model §6）。facet は親 Mention の文脈に抱える。出力に `statement` / `excerpt` / `affect` / `proposedTheme` / `proposedTags`。
- **完了条件**: 1 Dump → 0..N Mention が返る（例: 「転職…睡眠…」→ 2 Mention）。

### A2. グルーピング（v1 簡易）

- **要点**: 既存 Problem 群と新 Mention を突き合わせ、寄せる / 新規を起こすを判定。**v1 はルールベース or LLM 単発判定のスタブ**（embedding は Phase 2）。自動グルーピング + 事後トリアージ（A 案）。
- **完了条件**: 既存 Problem に寄る / 新規が起きる の両方が再現できる。

### A3. テーマ分類

- **要点**: `domain_model §2.4` の固定7分類 + `未分類` に主テーマを1つ割り当て、下位は自由タグ。
- **完了条件**: 各 Problem に主テーマ1つ + タグが付く。

---

## 4. Phase B: BFF（新ルーター + リポジトリ）

### B1. Mention / Problem リポジトリ（in-memory）

- **変更対象**: `apps/bff/src/repositories/`。`historyRepository.ts` と同じ in-memory パターン（再起動で消える TODO コメントは残す）。

### B2. aiAgentClient 拡張

- **要点**: `extract` / `group` を追加。`AI_AGENT_BASE_URL` 未設定時の **stub fallback** を維持（決め打ちの Mention/Problem を返す）。

### B3. tRPC ルーター

- **手続き（提案シェイプ）**:
  - `mention.create({ sessionId, dumpText })` → `{ mentions, affectedProblems }`（抽出 + 自動グルーピング）
  - `problem.list({ theme?, status?, range? })` → `Problem[]`
  - `problem.get({ id })` → `Problem`（mentions / plans 含む）
  - `problem.triage({ action, ... })` → 分割 / 統合 / 再リンク / 状態遷移（resolve / shelve / reopen）
  - `problem.createPlan({ problemIds })` → `ActionPlan`（既存 `/plan` 再利用）
- **完了条件**: 全手続きが stub でも 200 応答。

---

## 5. Phase C: 結線・後片付け

- **C1**: フロント `api/` 層を mock→real に切り替え（`VITE_USE_MOCK=false`）。
- **C2**: 3モード smoke test（stub のみ / AI Agent 起動 / フルスタック）。
- **C3**: `consultation.organize` を `/extract` 接続に寄せ、`history.*` は Problem 一覧へ段階廃止（PoC を急に壊さない）。

---

## 6. 未決 / 要設計（着手前に詰める）

- **新画面の UI 設計**（D2）— 一覧の並び（再出現重点の見せ方）/ トリアージの操作粒度。
- **グルーピングの v1 アルゴリズム**（A2）— ルールベースか LLM 単発か。閾値の置き方。
- **吐き出し（Dump）の境界**（A1）— 既存 `consultation`（対話）と Mention 抽出の接続点。セッション終了をどうトリガするか。
- **既存 `OrganizedResult` / `HistoryItem` の移行**（C3）— 併存期間と廃止タイミング。
