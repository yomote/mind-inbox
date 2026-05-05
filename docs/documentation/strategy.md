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

| 領域                | 真実 (single source of truth)       | 派生物                                                 |
| ------------------- | ----------------------------------- | ------------------------------------------------------ |
| UI 仕様             | **MDX (`docs/frontend/ui_specs/`)** | preview コンポーネント / mockApi.ts / 実装             |
| BFF API (tRPC)      | **TS の zod schema**                | OpenAPI (`docs/api/bff-trpc.yaml` 自動生成)            |
| AI Agent / VOICEVOX | **FastAPI コード**                  | OpenAPI (`docs/api/{ai-agent,voicevox}.yaml` 自動生成) |
| アーキテクチャ判断  | **ADR (`docs/adr/`)**               | CLAUDE.md 内のリンクのみ                               |
| 運用手順            | **Runbook (`docs/runbooks/`)**      | (なし)                                                 |
| コンセプト          | `docs/concept_deck.md`              | (現状維持)                                             |
| 基本設計            | `docs/design/`                      | (現状維持)                                             |
| テスト戦略          | `docs/testing/strategy.md`          | (現状維持)                                             |

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
  concept_deck.md       # コンセプト (現状維持)
  design/               # 基本設計・実装計画 (現状維持)
  api/                  # ★ 新設 — 生成 OpenAPI を commit (手書き禁止)
    README.md
    bff-trpc.yaml       # CI 生成
    ai-agent.yaml       # CI 生成
    voicevox.yaml       # CI 生成
  frontend/
    ui_specs/           # MDX UI 仕様 (現状維持、真実)
    ui_design.md        # (現状維持)
  adr/                  # ★ 新設 — 意思決定記録
    README.md
    template.md
    NNNN-*.md           # 4 桁連番
  runbooks/             # ★ 新設 — 運用手順
    README.md
    template.md
    deploy.md, rollback.md, ...
  documentation/        # この戦略
    strategy.md
  testing/
    strategy.md         # 既存
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

| 項目     | 内容                                                      |
| -------- | --------------------------------------------------------- |
| 場所     | `docs/api/bff-trpc.yaml` (生成、commit 必須)              |
| 真実か   | 派生 (真実は `apps/bff/src/trpc/router.ts` の zod schema) |
| 生成方法 | `trpc-to-openapi` 等で router から YAML 出力スクリプト    |
| 守り方   | CI で再生成 → `git diff --exit-code`                      |
| いつ更新 | 自動 (zod 変更時に CI が再生成)                           |
| 非ゴール | 手書き編集                                                |

### 4.3 FastAPI OpenAPI (AI Agent / VOICEVOX)

| 項目     | 内容                                              |
| -------- | ------------------------------------------------- |
| 場所     | `docs/api/ai-agent.yaml` `docs/api/voicevox.yaml` |
| 真実か   | 派生 (真実は FastAPI 実装)                        |
| 生成方法 | `app.openapi()` の出力を YAML 化するスクリプト    |
| 守り方   | CI で再生成 → diff fail                           |
| いつ更新 | 自動                                              |
| 非ゴール | 手書き編集                                        |

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
| OpenAPI (FastAPI x2) | 同上                                                          |
| MDX UI 仕様          | preview コンポーネントが TS コンパイル可 + 簡易 render テスト |
| ADR                  | 番号衝突なし / template の必須セクションが揃う                |
| Runbook              | リンク切れなし (markdownlint)                                 |
| 全 .md               | prettier + markdownlint (既存 lint-staged の延長)             |

---

## 7. コーディングエージェント運用ガイド

### 7.1 PR テンプレート

`.github/pull_request_template.md` に Docs 更新欄を追加 (#13 で実施):

```markdown
## Docs 更新

- [ ] UI 仕様 (MDX) を更新した / 不要
- [ ] OpenAPI が再生成済み (CI 緑) / 不要
- [ ] アーキテクチャ判断は ADR に書いた / 不要
- [ ] 運用手順の変更は Runbook に反映した / 不要
```

### 7.2 CLAUDE.md への反映 (#13 で実施)

- 「ドキュメント更新が必要な時の判断は `docs/documentation/strategy.md` を参照」
- 「アーキテクチャに関わる判断をするなら ADR を先に書く」
- 「OpenAPI を手書きしない (再生成する)」

### 7.3 エージェントが間違えやすい点

- **OpenAPI を手書きしようとする** → 生成物なので触らない。Router/FastAPI を直す
- **ADR と Runbook を混ぜる** → 「なぜそうしたか」は ADR、「どうやるか」は Runbook
- **過去 ADR を書き換える** → 不可。新規 ADR で supersede。過去は不変
- **README に詳細を書く** → README は最小限。本体は ADR / Runbook / 戦略ドキュメントへ

---

## 8. 関連 issue / マイルストーン

- マイルストーン: [v0.1 docs-as-code](https://github.com/yomote/mind-inbox/milestone/2)
- 親 epic: [#14 documentation as code 整備](https://github.com/yomote/mind-inbox/issues/14)

| トピック                | Issue                                                 |
| ----------------------- | ----------------------------------------------------- |
| BFF tRPC → OpenAPI      | [#8](https://github.com/yomote/mind-inbox/issues/8)   |
| FastAPI → OpenAPI       | [#9](https://github.com/yomote/mind-inbox/issues/9)   |
| MDX 仕様の保護          | [#10](https://github.com/yomote/mind-inbox/issues/10) |
| ADR 初期セット          | [#11](https://github.com/yomote/mind-inbox/issues/11) |
| Runbook 集約            | [#12](https://github.com/yomote/mind-inbox/issues/12) |
| PR template + CLAUDE.md | [#13](https://github.com/yomote/mind-inbox/issues/13) |

---

## 9. FAQ — このドキュメントはどこに書くべき?

```
書こうとしている内容は何か?
  │
  ├─ 画面の挙動 / フロー
  │       → docs/frontend/ui_specs/*.mdx
  │
  ├─ API の I/O
  │       → 該当する router / FastAPI コードの schema を更新
  │       → CI が OpenAPI を再生成
  │
  ├─ なぜそういう構成 / 技術選択をしたか
  │       → docs/adr/NNNN-{slug}.md
  │
  ├─ どうやって運用するか (デプロイ / 障害対応)
  │       → docs/runbooks/{name}.md
  │
  ├─ アプリのコンセプト / 価値提案
  │       → docs/concept_deck.md (現状維持)
  │
  ├─ 全体設計 / 実装計画
  │       → docs/design/ (現状維持)
  │
  └─ テスト方針
          → docs/testing/strategy.md (現状維持)
```

### よくある判断

- **「README に書こうかと思った」** → README は最小限。本体は ADR / Runbook / 戦略ドキュメントのいずれかに
- **「OpenAPI を手で直したい」** → router/FastAPI を直す。OpenAPI は派生
- **「ADR を書き換えたい」** → 不可。新規 ADR で supersede
- **「Runbook が長くなった」** → 1 手順 = 1 ファイルに分割
- **「MDX と実装が違う」** → 実装を直す (MDX が真実)
- **「FastAPI と OpenAPI が違う」** → OpenAPI を再生成する (実装が真実)
