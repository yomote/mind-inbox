# ドキュメント戦略 (Documentation as Code)

> Mind Inbox のドキュメントを「コードと同じ品質保証で」管理するための設計方針。
> 関連: [v0.1 docs-as-code マイルストーン](https://github.com/yomote/mind-inbox/milestone/2) / [#14 epic](https://github.com/yomote/mind-inbox/issues/14)

---

## 1. 目的と原則

### 1.1 ゴール

- **仕様が書かれていない機能は受け入れない** — レビュー時に「ドキュメントが無いから判断できない」を発生させない
- **書いたドキュメントが古びない** — CI でドキュメントと実装の乖離を検知する
- **エージェントが判断材料を持てる** — ADR / Runbook で過去判断と運用知識をコード化

### 1.2 設計原則

| 原則                               | 内容                                                           |
| ---------------------------------- | -------------------------------------------------------------- |
| **真実は 1 か所**                  | 同じ情報を複数ドキュメントに書かない。生成 or 手書きのどちらか |
| **生成物は commit**                | OpenAPI を CI で再生成 → diff があれば fail                    |
| **MDX を型で守る**                 | UI 仕様は preview コンポーネントとして TS コンパイル対象       |
| **意思決定は ADR、手順は Runbook** | README に混ぜない                                              |
| **乖離時のルールを明文化**         | UI は MDX が真実、API は実装が真実                             |

---

## 2. 真実の所在マトリクス

| 領域                  | 真実 (single source of truth)       | 派生物                                                      |
| --------------------- | ----------------------------------- | ----------------------------------------------------------- |
| UI 仕様               | **MDX (`docs/frontend/ui_specs/`)** | preview コンポーネント / mockApi.ts / 実装                  |
| BFF API (tRPC)        | **TS の zod schema**                | OpenAPI (`docs/api/bff-trpc.yaml` 自動生成)                 |
| AI Agent / VOICEVOX   | **FastAPI コード (pydantic)**       | OpenAPI 生成は未整備 (#9 未完。生成物・CI ゲートとも無い)   |
| アーキテクチャ判断    | **ADR (`docs/adr/`)**               | CLAUDE.md 内のリンクのみ                                    |
| 運用手順              | **Runbook (`docs/runbooks/`)**      | (なし)                                                      |
| 実行状態 (計画・進捗) | **GitHub Issues + Projects**        | docs へのリンクのみ (設計内容は board に書かない, ADR 0011) |
| コンセプト            | `docs/concept_deck.md`              | (現状維持)                                                  |
| 基本設計              | `docs/design/`                      | (現状維持)                                                  |
| テスト戦略            | `docs/testing/strategy.md`          | (現状維持)                                                  |

### 乖離した時のルール

| 領域                   | どちらを直すか                            |
| ---------------------- | ----------------------------------------- |
| UI (MDX vs 実装)       | **実装を直す** (MDX が真実)               |
| API (実装 vs OpenAPI)  | **OpenAPI を CI で再生成** (実装が真実)   |
| ADR (記述 vs 実装)     | ADR は不変。新規 ADR で superseded を宣言 |
| Runbook (手順 vs 実態) | Runbook を直す (手順は人が決める)         |

---

## 3. ディレクトリ構造

```
docs/
  concept_deck.md       # コンセプト
  design/               # 要件・ユースケース・ドメインモデル・現行の実装計画
    archive/            # 役目を終えた計画書 (現行方針をここから読まない)
  api/                  # 生成 OpenAPI を commit (手書き禁止)
    README.md
    bff-trpc.yaml       # CI 生成 (FastAPI 2 サービス分は未整備 — #9)
  frontend/
    ui_specs/           # MDX UI 仕様 (真実)
    ui_design.md
  adr/                  # 意思決定記録
    README.md
    template.md
    NNNN-*.md           # 4 桁連番
  runbooks/             # 運用手順
    README.md
    template.md
    {name}.md           # 1 手順 = 1 ファイル
  debrief/              # design-gate / debrief / briefing の累積ログ
    journal.md
    archive/            # 一過性のセッション記録
  documentation/        # この戦略
    strategy.md
  testing/
    strategy.md
```

---

## 4. 各ドキュメントタイプの詳細

### 4.1 UI 仕様 (MDX)

| 項目     | 内容                                                                                                            |
| -------- | --------------------------------------------------------------------------------------------------------------- |
| 場所     | `docs/frontend/ui_specs/*.mdx`                                                                                  |
| 真実か   | **真実**                                                                                                        |
| 形式     | MDX (preview 用 React コンポーネントを inline に書ける)                                                         |
| 守り方   | preview コンポーネント (`apps/frontend/src/spec/previews/`) を TS コンパイル / 簡易 render テストで型安全に保つ |
| いつ書く | 新規画面 / 既存画面の挙動変更**前**                                                                             |
| 非ゴール | コンポーネント実装の細部、CSS 詳細                                                                              |

### 4.2 BFF tRPC OpenAPI

| 項目     | 内容                                                                                                                   |
| -------- | ---------------------------------------------------------------------------------------------------------------------- |
| 場所     | `docs/api/bff-trpc.yaml` (生成、commit 必須)                                                                           |
| 真実か   | 派生 (真実は `apps/bff/src/trpc/router.ts` の zod schema)                                                              |
| 生成方法 | `apps/bff/scripts/generate-openapi.mjs` (router introspection。`trpc-to-openapi` は不使用 — `docs/api/README.md` 参照) |
| 守り方   | CI で再生成 → `git diff --exit-code`                                                                                   |
| いつ更新 | 自動 (zod 変更時に CI が再生成)                                                                                        |
| 非ゴール | 手書き編集                                                                                                             |

### 4.3 FastAPI OpenAPI (AI Agent / VOICEVOX)

**未整備** (#9 未完)。生成物 (`ai-agent.yaml` / `voicevox.yaml`)・生成スクリプト・CI ゲートのいずれも
まだ存在しない。真実は FastAPI 実装 (ai-agent は `app/schemas.py` の pydantic) にあり、BFF との
整合は L0 契約テスト (`apps/bff/scripts/contract-check.mjs`) が守っている。#9 を実装したらこの節を戻す。

### 4.4 ADR (Architecture Decision Records)

| 項目        | 内容                                                     |
| ----------- | -------------------------------------------------------- |
| 場所        | `docs/adr/NNNN-{slug}.md` (4 桁連番)                     |
| 形式        | MADR 3.0 (`docs/adr/template.md`)                        |
| 真実か      | **真実** (一度書いたら基本不変)                          |
| いつ書く    | アーキテクチャに関わる決定をする**前**。実装より先に書く |
| Status 遷移 | Proposed → Accepted → (Deprecated / Superseded by NNNN)  |
| 非ゴール    | 実装詳細、コードレベルの判断                             |

### 4.5 Runbook

| 項目     | 内容                                                                                    |
| -------- | --------------------------------------------------------------------------------------- |
| 場所     | `docs/runbooks/{name}.md`                                                               |
| 形式     | Trigger / Prerequisites / Steps / Verification / Rollback (`docs/runbooks/template.md`) |
| 真実か   | **真実**                                                                                |
| いつ書く | 運用手順を新規追加 / 変更する時                                                         |
| 守り方   | リンク切れチェック (markdownlint)                                                       |
| 非ゴール | アーキテクチャ判断 (ADR の領域)                                                         |

---

## 5. 更新タイミング

| 変更内容                   | 更新が必要なドキュメント                                                  |
| -------------------------- | ------------------------------------------------------------------------- |
| 新しい UI 画面             | MDX 仕様 → preview → mockApi → 実装 (この順)                              |
| 既存 UI の挙動変更         | MDX を**先に**更新 → 実装を追従                                           |
| tRPC mutation 追加/変更    | zod を変更 → CI で OpenAPI 再生成 → AI Agent 側 schema との整合 (L0 契約) |
| FastAPI endpoint 追加/変更 | 実装 → CI で OpenAPI 再生成 → BFF client を調整                           |
| アーキテクチャ判断         | **ADR を書いてから実装**                                                  |
| デプロイ手順を変える       | Runbook を更新                                                            |
| インシデント発生           | 事後に Runbook (incident-response) に学びを反映                           |

---

## 6. CI で守るルール

| ドキュメント         | チェック                                                      |
| -------------------- | ------------------------------------------------------------- |
| OpenAPI (BFF)        | 再生成 → `git diff --exit-code docs/api/bff-trpc.yaml`        |
| OpenAPI (FastAPI x2) | 未整備 (#9) — CI ゲートはまだ無い                             |
| MDX UI 仕様          | preview コンポーネントが TS コンパイル可 + 簡易 render テスト |
| ADR                  | 番号衝突なし / template の必須セクションが揃う                |
| Runbook              | リンク切れなし (markdownlint)                                 |
| 全 .md               | prettier + markdownlint (既存 lint-staged の延長)             |

---

## 7. コーディングエージェント運用ガイド

### 7.1 docs 更新を誰が要求するか

**§5 更新タイミング**が正典。PR 本文はマージ判断の案内板なので、更新した docs を網羅列挙させない (更新しなかった理由の列挙も不要) — 実 diff との整合は PR レビュー judge の軸 A ([`review-rubric.md`](../../.github/claude/review-rubric.md)) と `pr-readiness` skill が見る。CLAUDE.md からは本戦略・ADR ディレクトリ・「OpenAPI を手書きしない」方針を参照している。

### 7.2 エージェントが間違えやすい点

- **OpenAPI を手書きしようとする** → 生成物なので触らない。Router/FastAPI を直す
- **ADR と Runbook を混ぜる** → 「なぜそうしたか」は ADR、「どうやるか」は Runbook
- **過去 ADR を書き換える** → 不可。新規 ADR で supersede。過去は不変
- **README に詳細を書く** → README は最小限。本体は ADR / Runbook / 戦略ドキュメントへ

---

## 8. FAQ — マトリクスで決まらない判断

「どの領域を、どこに書くか」と「乖離したときにどちらを直すか」は **§2 真実の所在マトリクス** と同 § の「乖離した時のルール」が正典 (ここには再掲しない)。エージェントが実際に踏みやすい間違いは **§7.2 エージェントが間違えやすい点**。

マトリクスに載っていない判断だけを置く:

- **「Runbook が長くなった」** → 1 手順 = 1 ファイルに分割 (§4.5 Runbook)
- **「役目を終えた計画書をどうするか」** → 削除ではなく `docs/{design,debrief}/archive/` へ退避し、archive の README に「何だったか / なぜ archive したか」を 1 行書く。現行 docs から archive へのリンクはラベルに (archive) を付ける
