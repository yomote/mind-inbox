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
|---|---|---|
| `apps/bff/local.settings.json.example` 変更, または `process.env.*` の追加 | — | `CLAUDE.md` Environment Variables 表 / `documentation/strategy.md` §2 (環境変数) |
| `apps/bff/src/trpc/router.ts` または `apps/bff/src/trpc/routers/**` の追加・I/O 変更 | L0 契約 + L2 (router 経由テスト) — `apps/bff/**/*.{test,spec}.ts` を grep して該当 mutation がカバーされているか | `docs/api/bff-trpc.yaml` 再生成 / `CLAUDE.md` BFF: tRPC Router 節 |
| `apps/services/ai-agent/**/*.py` の endpoint 追加・I/O 変更 (FastAPI route) | L0 契約 + L1 (パース) + L2 (FastAPI) — `apps/services/ai-agent/**/test_*.py` または `tests/` を grep | `docs/api/ai-agent.yaml` 再生成 |
| `apps/services/voicevox/**/*.py` の endpoint 変更 | L1 + L2 | `docs/api/voicevox.yaml` 再生成 |
| `apps/frontend/src/components/screens/**` の新規/挙動変更 | L3 シナリオに影響しないか確認 (新規 E2E は増やさない方針) | `docs/frontend/ui_specs/*.mdx` の対応 spec / `apps/frontend/src/spec/previews/` |
| `cicd/iac/**/*.bicep` でのリソース追加・命名変更 | smoke-test スクリプト側で疎通確認が必要か | `docs/runbooks/` (deploy/rollback) / `CLAUDE.md` Azure Infrastructure 節 |
| `cicd/scripts/deploy/*.sh` または `cicd/scripts/smoke-test/*.sh` の追加・引数変更 | — | `docs/runbooks/` / `CLAUDE.md` Deployment Scripts 節 |
| アーキテクチャ判断級の変更 (新サービス追加 / DB スキーマ変更 / 認証フロー変更 / 大幅な依存追加) | — | `docs/adr/NNNN-{slug}.md` を **実装より先に** 書く方針 (documentation/strategy.md §4.4) |
| `package.json` の dependency 追加 | — | 大物 (新フレームワーク級) なら ADR 検討 |
| `.github/workflows/**` の変更 | — | `CLAUDE.md` Commands / `docs/runbooks/` |

検出は通常の `git diff --name-only` のグロブで十分。ファイル名だけで判定が苦しい場合 (例: `process.env.*` 追加検出) は `git diff main...HEAD -- apps/bff/` の中身を `grep` する。

### Step 3 — strategy ドキュメントを読む

機械トリガーで拾えない判断のため、必ず両方読む:

- `docs/testing/strategy.md` — 特に §1.2 (書かない判断), §4.1 (実装タイミング表), §4.2 (バグ修正は再現テスト必須), §8 (FAQ 決定木)
- `docs/documentation/strategy.md` — 特に §2 (真実の所在), §5 (更新タイミング), §9 (FAQ 決定木)

「最後に読んでから commit が積まれた可能性があるので、毎回読む」。skill 内に対応表を持って固定化しない。

### Step 4 — 既存テスト/docs 触れ判定

Step 2 で立った各 trigger について、**diff に対応するテスト/docs ファイルが含まれているか** を `git diff main...HEAD --name-only` で確認。含まれていれば「✅ 対応あり」、無ければ「⚠️ 未対応」と分類。

例: `apps/bff/src/trpc/routers/consultation.ts` が変わった diff に `apps/bff/**/*.test.ts` が含まれない → ⚠️。

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

PR テンプレ貼り付け用:
\`\`\`markdown
## テスト設計
- 対象レイヤ: ...
- 追加 or 変更したテスト: ...
- 書かなかった理由: ...

## Docs 更新
- 更新したドキュメント: ...
- 更新しなかった理由: ...
\`\`\`
```

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
