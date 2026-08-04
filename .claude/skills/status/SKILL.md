---
name: status
description: Mind Inbox の開発状況を GitHub の Issue / PR / sub-issue から集計してレポートする。user が「状況教えて」「今どこまで進んでる?」「進捗レポート」「/status」等と言ったとき、または開発の全体像を数字で把握したいときに起動。ライブの GitHub 状態を pull して要約する（board を作らなくても状況が見える）。設計背景は ADR 0011。
---

# status

Mind Inbox の「今どこまで進み・次に何をやるか」を **GitHub のライブ状態から集計**して 1 レポートにまとめる。ADR 0011 の「実行状態 = GitHub Issues + Projects が真実」に沿い、**skill は状態を持たず毎回 GitHub を引く**（スナップショットをハードコードしない）。

## いつ起動するか

- user が「状況」「進捗」「今どこ」「レポート」「/status」等を言ったとき
- 開発を再開する前に全体像を把握したいとき
- board（Projects の見る画面）を開かずに数字で状況を知りたいとき

## 設計思想

- **真実は GitHub 側**（ADR 0011）。skill は Issue 番号のスナップショットを持たず、都度 `list_issues` / `list_pull_requests` / sub-issue rollup を引く。アンカー（Epic 番号・タイトル接頭辞）だけを手がかりにする。
- **board の代替ではなく補完**。board = user が自分で眺める用 / このレポート = 聞かれた時に pull で出す用。
- **数字を主役に**。「Open 数 / v1 フェーズ N-of-4 / 次アクション」を必ず出す。散文で埋めない。
- **次の一手を1つ名指す**。レポートは眺めるためでなく動くために出す。

---

## 手順

### Step 1 — ライブ状態を引く

GitHub MCP ツール（`mcp__github__*`。ToolSearch で `select:` して読み込む）で以下を取得。repo は `yomote/mind-inbox`。

1. **Open Issue 一覧** — `list_issues` (state OPEN, perPage 40, fields: number/title/labels/updated_at)。
2. **直近マージ PR** — `list_pull_requests` (state closed, sort updated desc, perPage 10) から `merged_at` のあるものを数件。
3. **Epic の sub-issue rollup** — Open Issue のうちタイトルが `[epic]` で始まるものについて `issue_read` で `sub_issues_summary`（completed / total）を取る。特に v1 本流 Epic（タイトルに「Problem 中心」を含む、現状 #50）。

> 番号は変わりうるので**タイトル接頭辞で発見**する: `[epic]` = エピック、`[v1][Phase A/B/C]` = v1 フェーズ、ラベル `infra`/`security` = インフラ、`enhancement`+`ai-agent` 等で技術負債。

### Step 2 — 構造にマッピング

引いた Issue を次の束に振り分ける（ラベルとタイトル接頭辞で判定）:

- **v1 本流** — Epic（Problem 中心 2層）+ その sub-issue（Phase A/B/C）。Phase D は完了済み（PR #44）。`rollup` から N-of-4 を出す（D 済 + A/B/C の open/closed）。
- **テストハーネス** — Epic「テストハーネス」+ ラベル `testing` の残 Issue。
- **docs-as-code** — Epic「documentation as code」+ ラベル `docs` の残 Issue。
- **インフラ/運用** — ラベル `infra` / `security` / IaC 系。
- **技術負債** — `[tech-debt]` 接頭辞など。

### Step 3 — 数字を計算

- Open Issue 総数
- v1 フェーズ進捗（例: 1-of-4 = Phase D のみ完了）
- 各ストリームの残件数
- 直近マージ PR（何を進めたか 1 行）

### Step 4 — 次アクションを1つ選ぶ

v1 ロードマップ（`docs/design/implementation_plan_v1.md`）の順序（D→A→B→C）で、**まだ着手していない最も早い Phase** を「次アクション」に据える。v1 が動いていない場合のみ並行ストリームの小粒を候補にする。

### Step 5 — 出力

既定は下記の compact 形式（1 画面）。user が「詳しく」と言った時のみ各 Issue にリンク・更新日を付す。

```markdown
## 📊 Mind Inbox 開発ステータス（{YYYY-MM-DD}）

**直近の動き**: {直近マージ PR を 1 行}

**v1 本流 — Epic #{n}「Problem 中心 2層モデル」** … Phase {N}/4 完了
- ✅ Phase D（型&モック先行）— PR #44
- {⬜/🔄/✅} Phase A #{n}（AI Agent: 抽出/グルーピング/テーマ）
- ⬜ Phase B #{n}（BFF ルーター）
- ⬜ Phase C #{n}（結線・移行）

**並行ストリーム**
- 🧪 テストハーネス #{epic} — 残 {k}: {...}
- 📚 docs-as-code #{epic} — 残 {k}: {...}
- ☁️ インフラ — {...}
- 🔧 技術負債 — {...}

**数字**: Open {n} / v1 フェーズ {N}-of-4 / 次アクション = **{Phase X #n 着手}**
```

行が増えすぎる時は各ストリーム 1 行に圧縮し、v1 本流を優先表示する。

---

## やらないこと

- ❌ Issue 番号や進捗をこの skill 内にハードコード（毎回 GitHub を引く）
- ❌ board（Projects v2）の作成・更新（API 非対応。user の web UI 操作 / Runbook `github-projects-setup.md`）
- ❌ Issue の open/close やコメント投稿（レポートは読み取り専用。状態変更は明示依頼時のみ）
- ❌ 設計内容の記述（それは docs / ADR。ここは状態の要約のみ）

## 失敗時の挙動

- GitHub MCP ツールが未接続 → ToolSearch で `mcp__github__list_issues` 等を読み込む。それでも不可なら user に通知して中断。
- Epic / Phase が発見できない（タイトル規約変更）→ 引けた Open Issue をラベル別に素朴に集計して出す（構造化は諦めても数字は出す）。

## 関連

- ADR 0011（実行状態 = Issues + Projects）: `docs/adr/0011-github-projects-as-execution-dashboard.md`
- Runbook（board セットアップ）: `docs/runbooks/github-projects-setup.md`
- v1 ロードマップ: `docs/design/implementation_plan_v1.md`
