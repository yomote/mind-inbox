---
name: pr-readiness
description: Use BEFORE creating a pull request (e.g. before `gh pr create`) to check whether test and documentation updates are in place for the changes on the current branch. Combines mechanical file-pattern detection with LLM judgment against `docs/testing/strategy.md` and `docs/documentation/strategy.md`. Outputs a copy-pastable markdown checklist; does NOT auto-create tests or docs.
---

# pr-readiness

PR 作成前に「変更ファイル × テスト / ドキュメント対応関係」を引いて、抜けてる場所を **指摘だけ** する。修正は user の判断に委ねる。

## いつ起動するか

- user が「PR 出す前にチェック」「pr-readiness」「テスト/docs 抜けない?」等を言ったとき
- `/pr-readiness` で明示呼び出しされたとき
- `gh pr create` を打つ直前 (user の確認を取って)

## 設計思想

- **真実は strategy ドキュメント側に置く**。skill は対応表を持たない。`docs/testing/strategy.md` §4.1 と `docs/documentation/strategy.md` §5 / §2 をその場で読み、diff に当てる
- **指摘のみ。生成しない**。テストや doc を skill が書くと「実装の写し鏡テスト」「無駄な doc」を量産するリスク
- **「不要」も正解**。両 strategy doc が「書かない判断」を明文化しているのに合わせ、各指摘は user が "n/a" 宣言できる出口を持つ
- **hybrid**: 機械パターンで確実な trigger を先に固め、それ以外は LLM 判断 (strategy.md を rubric にして)

---

## 手順

### Step 1 — diff スコープ確定

base branch を確認 (デフォルト `main`)、変更ファイル一覧と概要を取得:

```bash
git rev-parse --abbrev-ref HEAD
git diff main...HEAD --name-status
git diff main...HEAD --stat
git status --short
```

判定:

- **`git diff main...HEAD` が空** (1 commit も無い) の場合: **「PR 作成不可」を出力先頭で警告**。`git status --short` に変更があれば WIP として扱い、uncommitted/untracked も対象に含めて readiness 判定は実施 (PR 出す前にコミット必要、と user に伝える)。両方空なら「変更なし」で終了
- **uncommitted のみ存在 / commit あり** どちらの場合も、判定対象は `commit 済みの diff ∪ uncommitted ∪ untracked` の和集合 (PR にこれから入る予定のものを全部見る)
- base が `main` で無い場合 (例: stacked PR) は user に確認

### Step 2 — 機械トリガー検出

下表を `git diff main...HEAD --name-only` の結果に当てる。**該当があれば必ず指摘**:

| パターン (変更検出) | 指摘内容 (Test) | 指摘内容 (Docs) |
| --- | --- | --- |
| `apps/bff/local.settings.json.example` 変更, または `process.env.*` の追加 | — | `apps/bff/CLAUDE.md` の環境変数表 (未設定時の挙動まで) / `documentation/strategy.md` §2 (環境変数) |
| `apps/bff/src/trpc/router.ts` または `apps/bff/src/trpc/routers/**` の追加・I/O 変更 | 契約 (ai-agent とまたぐ I/O 変更なら) + 入場条件 (strategy.md §2.2「壊れても例外が出ず、データが静かに間違う」) を満たすドメインルール変更なら単体 (プロパティ優先) — `apps/bff/**/*.{test,spec}.ts` を grep。**受け渡し・ルーティングだけならテスト不要 — 代わりに Verification 欄の実測を確認** | `docs/api/bff-trpc.yaml` 再生成 (`npm run docs:openapi` — 手書きしない) / `apps/bff/CLAUDE.md` (zod が真実 / 承認の門) |
| `apps/services/ai-agent/**/*.py` の endpoint 追加・I/O 変更 (FastAPI route) | 契約 + 入場条件を満たすロジック (パース等) なら単体 — `apps/services/ai-agent/**/test_*.py` または `tests/` を grep。受け渡しだけならテスト不要 (Verification 実測) | `docs/api/ai-agent.yaml` 再生成 |
| `apps/services/voicevox/**/*.py` の endpoint 変更 | 入場条件を満たすロジックのみ単体 (placeholder 解消は #2) | `docs/api/voicevox.yaml` 再生成 |
| `apps/frontend/src/components/screens/**` の新規/挙動変更 | 異常系スモーク (`apps/frontend/e2e-uc/`) / 実環境 E2E の hop に影響しないか確認。**廃止方針の旧 L3 mock (`apps/frontend/e2e/`) に新規シナリオを足さない** (strategy.md §6.3。E2E の本数も増やさない) | `docs/frontend/ui_specs/*.mdx` の対応 spec / `apps/frontend/src/spec/previews/` |
| `cicd/iac/**/*.bicep` でのリソース追加・命名変更 | smoke-test スクリプト側で疎通確認が必要か | `docs/runbooks/` (deploy/rollback) / `cicd/CLAUDE.md` (2-phase Bicep / リソース命名 / コスト前提) |
| `cicd/scripts/deploy/*.sh` または `cicd/scripts/smoke-test/*.sh` の追加・引数変更 | — | `docs/runbooks/` / `cicd/CLAUDE.md` のデプロイスクリプト一覧 |
| アーキテクチャ判断級の変更 (新サービス追加 / DB スキーマ変更 / 認証フロー変更 / 大幅な依存追加) | — | `docs/adr/NNNN-{slug}.md` を **実装より先に** 書く方針 (documentation/strategy.md §4.4) |
| `package.json` の dependency 追加 | — | 大物 (新フレームワーク級) なら ADR 検討 |
| `.github/workflows/**` の変更 | — | `.claude/skills/dev/SKILL.md` (コマンドが変わったなら) / `docs/runbooks/` |

検出は通常の `git diff --name-only` のグロブで十分。ファイル名だけで判定が苦しい場合 (例: `process.env.*` 追加検出) は `git diff main...HEAD -- apps/bff/` の中身を `grep` する。

### Step 3 — strategy ドキュメントを読む

機械トリガーで拾えない判断のため、必ず両方読む:

- `docs/testing/strategy.md` — 特に §1.2 (書かない判断), §4.1 (実装タイミング表), §4.2 (バグ修正は再現テスト必須), §1.4 (テスト可能性を設計基準にする), §7 (表で決まらない判断)
- `docs/documentation/strategy.md` — 特に §2 (真実の所在), §5 (更新タイミング), §8 (マトリクスで決まらない判断)

「最後に読んでから commit が積まれた可能性があるので、毎回読む」。skill 内に対応表を持って固定化しない。

### Step 4 — 既存テスト/docs 触れ判定

Step 2 で立った各 trigger について、**diff に対応するテスト/docs ファイルが含まれているか** を `git diff main...HEAD --name-only` で確認する。判定は trigger の性質で分岐する:

- **無条件トリガー** (該当したら必ず対応が要る行 — 言語間 I/O 変更の契約テスト、OpenAPI 再生成、Runbook / 領域別 `CLAUDE.md` 更新、実装より先の ADR): 対応ファイルが diff に含まれていれば「✅ 対応あり」、無ければ「⚠️ 未対応」
- **条件付きトリガー** (入場条件・影響判定に依存する行 — 単体テストの要否、スモーク / E2E への影響確認): 対応テストが diff に有れば「✅ 対応あり」。無くても機械分類で ⚠️ にせず、**diff の中身で条件を判定する** — 入場条件 (strategy.md §2.2「壊れても例外が出ず、データが静かに間違う」) を満たすドメインルール変更か / スモーク・E2E の対象シナリオに影響するか。**満たす・影響するのにテスト/更新が無ければ「⚠️ 未対応」。満たさない・影響しない (受け渡し・ルーティング・型の詰め替えだけ等) ならテストは要求せず、PR 本文の Verification 欄に実測が貼られているか (これから貼る前提が示されているか) の確認に切り替える** — 実測の予定すら無ければ「⚠️ Verification 実測なし」

例:

- `apps/bff/src/trpc/routers/consultation.ts` で言及回数の導出 (ドメインルール) を変えた diff に `apps/bff/**/*.{test,spec}.ts` が含まれない → ⚠️ 未対応 (入場条件を満たすのに単体が無い)
- 同ファイルで呼び出し先の付け替え (受け渡しだけ) の diff にテストが無い → テストは要求せず Verification 実測の確認へ。実測も無ければ ⚠️ Verification 実測なし

### Step 5 — LLM 判断 (rubric ベース)

機械パターンで拾えない変更について、Step 3 で読んだ strategy.md を rubric として diff 全体を読み:

- testing: §4.1 表のどの行に該当するか? 該当するなら必須レイヤのテストが diff にあるか
- testing: バグ修正コミットメッセージなら §4.2 の再現テストがあるか
- docs: §5 表のどの行に該当するか? 該当する更新があるか
- docs: §2 真実の所在で「実装が真実」のものなら手で doc 直してないか / 「ドキュメントが真実」のものなら先に doc 直したか

「必要なし」が明らかな変更 (typo 修正 / コメント修正 / 純粋な内部リファクタで public I/O 不変) は積極的に skip。

### Step 6 — 出力

**デフォルトは compact モード**。user が「詳しく」「verbose」「根拠も」等を明示した場合のみ verbose モード。

#### compact モード (既定)

1 画面に収まる粒度。各セクション最大 5 項目、検出根拠は省略 (聞かれたら答える):

```markdown
## PR Readiness ({branch} → main, NN files)

[⚠️ 1 commit も無し: PR 出す前にコミット必要] ← 該当時のみ

✅ {検出 trigger} → {対応ファイル}
   ...
⚠️ {未対応 trigger} → 期待: {action} / skip 可: {条件}
   ...
💡 {LLM 判断}: {一行で}
   ...

PR テンプレ貼り付け用 (`.github/PULL_REQUEST_TEMPLATE.md` の該当欄だけ):
\`\`\`markdown
## Verification

| Check | Result |
| --- | --- |
| Unit / contract | ... |
| E2E | ... |
| Live verification | ... |
| UI 確認 | ... |

## Known limitations

- ...
\`\`\`
```

貼り付け用は **Verification 表の結果と未検証事項だけ**にする。テストごとの理由や更新した docs の網羅列挙は PR 本文に出さない (テストコードと実 diff が持つ)。

#### verbose モード (要望時)

各 ⚠️ / 💡 に「検出根拠」(strategy.md の §, パターン名) と「skip 判断の例」を併記。

#### 共通の重要事項

- skill は **修正提案までで止める**。ファイル編集はしない。user が確認後に別途修正コミット
- 各 ⚠️ には「skip 可: 〜」の出口を必ず付ける (strategy.md の "書かない判断" 思想に整合)
- LLM 判断パートは確信度が低いので 💡 と分けて提示
- 1 画面に収まらない場合: ⚠️ を優先表示し ✅ と 💡 を要約 (詳細は verbose で)

---

## やらないこと

- ❌ テストファイルの自動生成
- ❌ ドキュメントの自動生成
- ❌ skill 内に対応表をハードコード (strategy.md を都度読む)
- ❌ 「念のため」の指摘 (確信度が低いものは 💡 セクションへ)
- ❌ コミット作成・PR 作成 (skill の責務外)

## 失敗時の挙動

- `docs/testing/strategy.md` または `docs/documentation/strategy.md` が存在しない → user に通知して中断 (このリポジトリ前提の skill)
- base branch が不明 → `main` を仮定し user に確認
- diff が空 → 「変更なし」を返して終了
