# 0012. Mention のグルーピングは AI Agent が担い、BFF が既存 Problem 候補を渡す

- Status: Accepted
- Date: 2026-08-04
- Deciders: omoteforlab
- Consulted: —
- Informed: —

Technical Story: Phase A（`/extract`）実装 (#51 / PR #57)。[ADR 0007](0007-problem-centric-two-layer-domain-model.md) が「自動グルーピング + 事後トリアージ」を決めたが、**その計算をどのサービスで行うか**は未決だった。

## Context and Problem Statement

Problem 中心 2層モデル（ADR 0007）では、吐き出し（Dump）から抽出した Mention を既存 Problem に寄せるか新規を起こすかを**自動グルーピング**する。この判定を **AI Agent（Python / LLM を持つ）** と **BFF（tRPC / Problem リポジトリを持つ）** のどちらが担うかを決める必要がある。AI Agent は LLM 判定ができるが Problem の状態を持たず、BFF は Problem リポジトリ（Phase B, in-memory）を持つが LLM を持たない。グルーピングは v1 では LLM 単発判定（embedding は Phase 2）。

## Decision Drivers

- **判定ロジックの一貫性** — 抽出（A1）・テーマ分類（A3）と同じ LLM 呼び出しの中で完結させたい
- **責務の単純さ** — 「Mention を生む知的処理は AI Agent」「永続と状態遷移は BFF」の境界を保つ
- **v1 の実装コスト** — LLM 単発で寄せ/新規を返せる範囲に留める
- **Phase 2 への発展** — embedding 類似に差し替えても契約が変わらないこと

## Considered Options

- Option A: **AI Agent が抽出→グルーピングまで担い、BFF が既存 Problem 候補を `/extract` リクエストで渡す**
- Option B: AI Agent は Mention 抽出のみ、グルーピングは BFF（Phase B）でルール/LLM
- Option C: グルーピング専用サービス / 判定を BFF から AI Agent へ二段呼び

## Decision Outcome

Chosen option: **"Option A"**。`/extract` は `ExtractRequest{ session_id, existing_problems }` を受け、`ExtractionResult{ items:[{mention, grouping}], newProblemCount, updatedProblemCount }`（`domain.ts` 準拠）を返す。BFF（Problem リポジトリの所有者）が既存 Problem の候補（id / title / theme / summary / mentionCount / status）を渡し、AI Agent が LLM 単発で `new` / `existing` を判定する。`reignited` は候補の `status != open`、`mentionCount` は候補値 + バッチ内累積で算出する。

抽出・テーマ・グルーピングを 1 回の LLM 呼び出しに束ねられ、`ExtractionResult` 型（grouping を内包）と素直に対応する。Option B は抽出とグルーピングが 2 サービスに割れ、AI Agent が持たない Problem 状態を BFF 側で再判定する二度手間になる。Option C は v1 には過剰。

## Positive Consequences

- 抽出 / テーマ / グルーピングが `/extract` 1 呼び出しで完結し、`ExtractionResult` 型に一致する
- 「知的処理 = AI Agent、永続 = BFF」の責務境界が保たれる
- Phase 2 で embedding 類似に差し替えても、`existing_problems` を渡す契約は不変

## Negative Consequences

- **AI Agent が Problem の候補集合を入力に取る**ため、BFF は毎回候補を渡す責務を負う（候補の絞り込み戦略は Phase 2 の課題）
- 新規 Problem の id は AI Agent が採番する（`prob-<uuid>`）。BFF が独自採番に差し替える場合は Phase B で調整が要る
- グルーピング判定が LLM 単発のため精度は限定的（v1 の割り切り。embedding は Phase 2）

## Links

- 関連 ADR: [0007](0007-problem-centric-two-layer-domain-model.md)（Problem 中心 2層 / 自動グルーピング + 事後トリアージ）
- 実装: PR #57（Phase A `/extract`） / [implementation_plan_v1 §3](../design/archive/implementation_plan_v1.md)
- 契約: `apps/bff/src/trpc/domain.ts`（`ExtractionResult` / `GroupingOutcome`）
