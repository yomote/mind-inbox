# テスト戦略

> Mind Inbox のテストハーネスに関する設計方針・運用ルール。
> 関連: [v0.1 test-harness マイルストーン](https://github.com/yomote/mind-inbox/milestone/1) / [#7 epic](https://github.com/yomote/mind-inbox/issues/7)

---

## 1. 目的と原則

### 1.1 ゴール

Mind Inbox は **コーディングエージェント駆動の高速開発** を前提に進める。
テストハーネスのゴールは「カバレッジを高めること」ではなく、次の 3 つを満たすこと:

1. **エージェントが壊した時に CI が必ず気づく** — 静かに通る変更を許さない
2. **ローカルで数十秒以内にフィードバックが返る** — エージェントの試行錯誤サイクルを止めない
3. **失敗時に "どの層が壊れたか" が一目で分かる** — 修正のために原因を探す時間を最小化する

### 1.2 設計原則

| 原則              | 内容                                                                                                    |
| ----------------- | ------------------------------------------------------------------------------------------------------- |
| **契約集約**      | tRPC の `zod` と AI Agent の `pydantic` は同じ I/O を別言語で書いている。両者の対称性を契約テストで担保 |
| **L2 重視**       | エージェントは unit を機械的に通す書き方を学習しているので、サービス結合層を主戦場に置く                |
| **mock 一元化**   | `apps/frontend/src/mockApi.ts` を共通 fixture に。テストごとに別の mock を増やさない                    |
| **snapshot 最小** | 境界 (tRPC レスポンス JSON / organize 出力 JSON) のみ。UI snapshot は採用しない                         |
| **失敗の局所化**  | テスト名に `[L0]` `[L1]` `[L2]` プレフィックスを付け、CI ログで層を即特定                               |

---

## 2. テスト階層と役割

```
┌────────────────────────────────────────────────────────────────┐
│ Layer  │ 何を守るか           │ 代表例                          │
├────────┼─────────────────────┼────────────────────────────────┤
│ L0 契約 │ 言語間スキーマ整合    │ tRPC zod ↔ pydantic schema     │
│ L1 単体 │ 純粋ロジック          │ deriveTitle / organize JSON 解析│
│ L2 結合 │ Router/API 全体動作  │ createCaller / FastAPI ASGI     │
│ L3 E2E  │ UI 描画 + 主要フロー  │ Playwright (1 シナリオ)         │
│ L4 smoke│ 実 Azure 環境疎通    │ cicd/scripts/smoke-test/...     │
└────────────────────────────────────────────────────────────────┘
```

各層の "重さ" と "捕まえる回帰の種類" は明確に違う。**層ごとに役割が重複しないように書く**。

---

## 3. 各レイヤの詳細

### L0 契約 (Contract Test)

| 項目               | 内容                                                                                            |
| ------------------ | ----------------------------------------------------------------------------------------------- |
| **目的**           | BFF と AI Agent が同じ I/O を別言語で書いているため、片側だけ変更されても気づけるようにする     |
| **対象**           | `consultation.organize` `consultation.createPlan` `consultation.approve` などの I/O JSON Schema |
| **フレームワーク** | TS スクリプト + JSON Schema diff                                                                |
| **書き方の指針**   | tRPC の zod と pydantic からそれぞれ JSON Schema を生成し、構造を比較。差分があれば fail        |
| **非ゴール**       | フィールドの値の妥当性 (それは L2 でやる)                                                       |
| **実行コマンド**   | `npm run test:contract`                                                                         |

### L1 単体 (Unit Test)

| 項目               | 内容                                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------------------------- |
| **目的**           | 純粋ロジック / パース / フォーマット関数の正しさ                                                         |
| **対象**           | `deriveTitle`、env 未設定時の stub フォールバック判定、organizer の JSON 抽出、historyRepository の CRUD |
| **フレームワーク** | TS: vitest / Python: pytest                                                                              |
| **書き方の指針**   | I/O は in-memory のみ。ネットワーク / ファイル / DB を触らない。1 テスト < 50ms                          |
| **非ゴール**       | コンポーネント描画の見た目検証、router 全体の挙動                                                        |
| **実行コマンド**   | `npm run test:bff` `npm run test:frontend` `pytest`                                                      |

### L2 結合 (Service Test)

| 項目               | 内容                                                                                                                |
| ------------------ | ------------------------------------------------------------------------------------------------------------------- |
| **目的**           | HTTP レイヤをバイパスして router / FastAPI を in-process で叩き、外部依存だけモック化したフロー単位の挙動を固定     |
| **対象**           | tRPC mutation 全部、FastAPI endpoint 全部、`start → send → organize → plan → save` の通しフロー                     |
| **フレームワーク** | TS: vitest + `appRouter.createCaller(ctx)` / Python: pytest + `httpx.AsyncClient(transport=ASGITransport(app=app))` |
| **書き方の指針**   | Azure OpenAI / Container App は必ずモック。in-memory repository を使う。フロー単位で 1 テスト = 1 シナリオ          |
| **非ゴール**       | UI、実 Azure 環境                                                                                                   |
| **実行コマンド**   | `npm run test:bff` (L1 と同じハーネス、ファイル名で区別)                                                            |

### L3 E2E (UI Flow)

| 項目               | 内容                                                                                                |
| ------------------ | --------------------------------------------------------------------------------------------------- |
| **目的**           | UI 描画レベルでの破壊検知。最終防衛線                                                               |
| **対象**           | `onboarding → newConsultation → session → organize → result → save → history` の **1 シナリオのみ** |
| **フレームワーク** | Playwright                                                                                          |
| **書き方の指針**   | BFF は env 未設定 = stub モードで起動。スクリーン単位の個別 E2E は書かない                          |
| **非ゴール**       | スクリーンの見た目スナップショット、エラーパスの網羅                                                |
| **実行コマンド**   | `npm run test:e2e`                                                                                  |

### L4 smoke (実環境疎通)

| 項目               | 内容                                                               |
| ------------------ | ------------------------------------------------------------------ |
| **目的**           | デプロイ後に実 Azure 環境が疎通していることを保証                  |
| **対象**           | SWA → Functions → Container Apps → Azure OpenAI の各エンドポイント |
| **フレームワーク** | bash + curl                                                        |
| **書き方の指針**   | 既存の `cicd/scripts/smoke-test/smoke-test.sh` を維持              |
| **非ゴール**       | 機能の正しさ (それは L1〜L3 で担保済み)                            |
| **実行コマンド**   | `cicd/scripts/smoke-test/smoke-test.sh`                            |

---

## 4. 実装タイミング (いつ書くか)

### 4.1 新機能追加時

新機能を追加する PR では、**最低 2 層** にテストを書く:

| 機能の性質                   | 必須レイヤ                        |
| ---------------------------- | --------------------------------- |
| 純粋関数の追加               | L1                                |
| tRPC mutation の追加         | L1 (ロジック) + **L2 (router)**   |
| AI Agent endpoint の追加     | L1 (パース等) + **L2 (FastAPI)**  |
| BFF と AI Agent をまたぐ機能 | L0 (契約) + L2 (両側)             |
| UI フロー全体に影響          | L3 を更新 (新規 E2E は増やさない) |

### 4.2 バグ修正時

**バグ修正 PR には必ず再現テストを 1 本入れる。**
書く層は「最も再現コストが低い層」を選ぶ:

```
バグ報告
  │
  ├─ 純粋関数の問題? ─── Yes → L1 で再現
  │       │
  │       No
  ├─ Router/API レベル? ── Yes → L2 で再現
  │       │
  │       No
  └─ UI 描画 / 遷移? ───── Yes → L3 で再現 (既存シナリオを拡張)
```

### 4.3 リファクタリング時

- **既存テストを緑のまま動かす** ことが成功条件
- テストを書き換える必要がある = リファクタの粒度が大きすぎる可能性。分割を検討
- snapshot を更新する必要が出たら、**必ず差分を目視レビュー**してから commit

---

## 5. 実行タイミング (いつ走らせるか)

| タイミング                   | 走るレイヤ          | 想定時間 | 失敗時の挙動             |
| ---------------------------- | ------------------- | -------- | ------------------------ |
| **エディタ保存中 (watch)**   | L1 (該当ファイル分) | < 2s     | エディタ内で表示         |
| **コミット前 (任意)**        | L1 + L2             | < 30s    | 開発者の手で実行         |
| **PR push (CI)**             | L0 + L1 + L2 + L3   | < 3min   | マージブロック           |
| **main マージ後 (CI)**       | L4 smoke (dev 環境) | 数分     | 通知のみ (rollback 検討) |
| **デプロイ後 (手動 / 週次)** | L4 smoke (各環境)   | 数分     | 環境ごとに判断           |

### 5.1 ローカル開発中

```bash
# 推奨: 該当 app のディレクトリで watch を流しっぱなし
cd apps/bff && npm run test -- --watch
```

エージェントには **最後に `npm run test:fast` を 1 回叩いてから PR を出す** ことを CLAUDE.md で要求する。

### 5.2 pre-commit hook の方針

- pre-commit を **重くしない**。L1 だけに絞る (or 無し)
- 重いチェックは CI に任せる。エージェントの commit サイクルを止めない

### 5.3 CI gate の構成

`.github/workflows/test.yml`:

```
1. install (node + python, キャッシュあり)
2. npm run test:contract  ← L0
3. npm run test:fast       ← L1 + L2 (並列)
4. npm run test:e2e        ← L3
```

main ブランチ保護で 2〜4 を必須チェックに設定する。

---

## 6. コーディングエージェント運用ガイド

### 6.1 エージェントに期待してはいけないこと

- **snapshot を慎重に更新すること** — 機械的に更新する。よって snapshot は最小限
- **テスト全体を見渡して粒度を調整すること** — 渡した範囲しか見ない。粒度はレビュー時に人間が確認
- **暗黙の契約を察すること** — L0 契約テストで明示する。コメントに頼らない

### 6.2 PR セルフチェックリスト (エージェントに渡す)

```markdown
- [ ] `npm run test:fast` がローカルで緑
- [ ] 新機能なら L2 を最低 1 本追加した
- [ ] バグ修正なら再現テストを追加した
- [ ] snapshot を更新した場合、差分を目視で確認した
- [ ] 新しいモックを増やしていない (既存 fixture を再利用)
```

### 6.3 テストが落ちた時の切り分け順序

```
落ちたテスト名のプレフィックスを見る:
  [L0] → スキーマ不整合。BFF 側 or AI Agent 側のどちらかが先行
  [L1] → ロジック単体のバグ。該当関数だけ見る
  [L2] → 結合層の挙動変化。フローを通して確認
  [L3] → UI または BFF 起動の問題。frontend / BFF dev サーバを見る
  smoke → 実環境のみで再現する問題。デプロイ・env を疑う
```

---

## 7. 関連 issue / マイルストーン

- マイルストーン: [v0.1 test-harness](https://github.com/yomote/mind-inbox/milestone/1)
- 親 epic: [#7 テストハーネス整備](https://github.com/yomote/mind-inbox/issues/7)

| Layer        | Issue                                               |
| ------------ | --------------------------------------------------- |
| L0 contract  | [#1](https://github.com/yomote/mind-inbox/issues/1) |
| L1 unit      | [#2](https://github.com/yomote/mind-inbox/issues/2) |
| L2 service   | [#3](https://github.com/yomote/mind-inbox/issues/3) |
| L3 e2e       | [#4](https://github.com/yomote/mind-inbox/issues/4) |
| infra script | [#5](https://github.com/yomote/mind-inbox/issues/5) |
| ci gate      | [#6](https://github.com/yomote/mind-inbox/issues/6) |

導入順序:

1. Week 1: #2 (L1)
2. Week 2: #3 (L2)
3. Week 3: #1 (L0)
4. Week 4: #4 (L3) + #6 (CI)
5. 並行: #5 (infra)

---

## 8. FAQ — このテストはどの層に書くべき?

```
新しく書くテストの対象は何か?
  │
  ├─ 純粋関数 (引数を受けて戻り値を返すだけ)
  │       → L1
  │
  ├─ tRPC mutation / FastAPI endpoint の挙動
  │       → L2
  │
  ├─ BFF と AI Agent の I/O 整合性
  │       → L0
  │
  ├─ ユーザがクリックして画面が遷移する
  │       → L3 (既存シナリオを拡張)
  │
  └─ 実 Azure 環境でしか起きない問題
          → L4 (smoke-test.sh に追加)
```

### よくある判断

- **「L1 と L2 のどちらに書く?」** → ロジックが純粋なら L1、router を経由するなら L2。**両方に同じテストは書かない**
- **「snapshot を増やしたい」** → まず "境界の JSON" 以外で snapshot を取らない方針を思い出す。代替で assertion を書けないか検討
- **「mock を新規に作りたい」** → `apps/frontend/src/mockApi.ts` に必要なデータがあるか先に確認。無ければ既存ファイルに追加 (新規ファイルを作らない)
- **「テストが遅い」** → L2 に書いていないか確認。L2 は 1 テスト < 500ms が目安。超えるなら L1 に分割
