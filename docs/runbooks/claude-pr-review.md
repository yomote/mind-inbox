# Claude PR Review (LLM-as-a-judge) のセットアップ

開発セッションとは**別軸**の「審査役」を、Claude Code on the web の **Routine** として動かす。
PR が来るたびにサブスク枠の Claude が別セッションで diff をレビューし、inline comment +
サマリを投稿する。**API キー不要・追加課金なし**(サブスク使用量を消費)。

「指摘 → 修正 → 再レビュー → 直ったら自動 Resolve → 解決するまでマージ不可」を**ループ**として
運用する (後述の「レビューのループと強制力」)。**merge は人間**が行う。
方式選択の判断記録: [ADR 0008](../adr/0008-pr-review-via-cloud-routine.md)。

## Trigger

PR 自動レビューを有効化したい / 止めたい / 観点を変えたい時。

## なぜ Routine か (方式の選択)

| 方式 | 課金 | プラン | 採否 |
| --- | --- | --- | --- |
| **Routine (これ)** | サブスク枠・追加課金なし | Pro / Max〜 | ✅ 採用 |
| Code Review (`/en/code-review`) | 1レビュー $15〜25 の従量 | Team / Enterprise 限定 | ✕ プラン外・高額 |
| GitHub Actions + `ANTHROPIC_API_KEY` | API 従量課金 | 不問 | ✕ 追加課金が避けられない |

Routine はサブスク枠で走り、日次実行上限を超えても**課金されず拒否される**(usage credits 未使用時)。
"$5 が溶ける" 事故が起きない。詳細は [ADR 0008](../adr/0008-pr-review-via-cloud-routine.md) /
<https://code.claude.com/docs/en/routines>。

## Prerequisites

- Claude **Pro / Max** 以上 + Claude Code on the web が有効
- `yomote/mind-inbox` に **Claude GitHub App** がインストール済み
  (webhook 配信に必須。`/web-setup` だけでは App は入らない)
- 審査基準: [`.github/claude/review-rubric.md`](../../.github/claude/review-rubric.md)

## Steps

すべて web UI (<https://claude.ai/code/routines>) で完結する。ローカル作業・シークレット登録は不要。

1. <https://claude.ai/code/routines> → **New routine**。

2. **名前**: `PR review (mind-inbox)`。**プロンプト**に以下を貼る:

   ```text
   この PR をレビューしてください。審査基準はリポジトリの
   .github/claude/review-rubric.md に厳密に従うこと。
   手順:
   1. .github/claude/review-rubric.md を読む。
   2. 対象 PR の diff と PR 本文を取得する。
   3. 必要なら戦略 doc・実装・テストを読んで裏取りする。
   4. blocker / major で行が特定できる指摘は該当行に inline comment、
      全体は verdict + 3軸所見 + findings テーブルのサマリ 1 本を PR に投稿する。
   5. 再レビュー (2回目以降) では rubric の収束ルールに従い、直ったスレッドは resolve する。
   コードの修正・push・merge はしない (resolve までが judge の責務、merge は人間)。
   ```

3. **リポジトリ**: `yomote/mind-inbox` を選択。**モデル**: Sonnet (コスト・速度重視)。

4. **トリガー** = **GitHub event**:
   - リポジトリ: `yomote/mind-inbox`
   - イベント: `pull_request` → **`opened` と `synchronize`**
     (`opened` = 初回レビュー / `synchronize` = push のたび再レビュー。これで
     「直したら再チェック」のループが閉じる)
   - フィルタ: `Is draft` = `false`(draft はレビューしない)
   - コスト最優先なら `opened` のみも可。ただし再レビューが走らずループは閉じない

5. **Permissions**: `Allow unrestricted branch pushes` は **OFF のまま**(judge は push しない)。
   **Connectors**: 不要なものは外す(レビューに GitHub 以外は要らない)。

6. **Create**。

7. **ブランチ保護**(強制力)を main に設定: `Settings → Branches` で
   **"Require conversation resolution before merging"** を有効化。未解決スレッドが
   残っているとマージできなくなる(後述)。

## レビューのループと強制力

```
PR 作成 (opened)
   → judge がレビュー (blocker/major は inline スレッド、全体はサマリ)
   → あなたが修正して push (synchronize)
   → judge が再レビュー: 直ったスレッドを自動 Resolve、未解決の重要指摘は残す、nit は再掲しない
   → 全スレッド解決 → あなたが merge ボタンを押す
```

- **自動 Resolve** は rubric の「再レビュー時の挙動（収束 + 自動 Resolve）」が担保する。
  Routine セッションは管理 GitHub 接続経由で resolve / merge ツールを**PAT なしで**持つ
  (実測確認済み)。ただし **merge は使わせない**方針 (rubric / プロンプトで明示)。
- **強制力**: ブランチ保護「会話の解決を必須」により、judge の指摘スレッドが未解決のままだと
  マージできない。人間の規律に頼らず「対応するまでマージ不可」を仕組みで担保する。
- **merge は人間**: 自動 Resolve はモデル判断であり決定論ではないため、最終的なマージ判断は
  人が行う (歯止めを残す)。

## Verification

- [ ] テスト PR を開くと <https://claude.ai/code> に新しいセッションが 1 本生える
- [ ] その PR に inline comment + サマリコメントが付く
- [ ] サマリ冒頭に verdict (`✅ LGTM` / `💬 コメントあり` / `🔧 要修正`) が出る
- [ ] **修正を push すると再レビューが走り、直った指摘のスレッドが自動 Resolve される**
- [ ] **未解決スレッドが残っているとマージできない**(会話解決ゲート)
- [ ] セッションを開くと judge の作業ログが読める(green = 起動成功であって task 成功ではない点に注意)

## セキュリティ (公開リポジトリ)

- Routine は**あなたの claude.ai / GitHub アカウントとして**走る。コメントはあなた名義。
- judge は PR の diff・本文 (= 第三者が書ける入力) を読む。万一プロンプトインジェクションを
  受けても、**push は `claude/` 接頭辞のブランチに限定**(`Allow unrestricted branch pushes` OFF)、
  かつ judge は merge しない方針なので被害は限定的。
- Connectors を絞り、ネットワークは **Trusted** 既定のままにしておく。

## Rollback / コスト制御

- 一時停止: routine 詳細ページの **Repeats** トグルで pause。
- 観点変更: `.github/claude/review-rubric.md` を直す(routine 側ではなく)。
- コスト抑制: トリガーを `opened` のみにして毎 push レビューを避ける / モデルを Sonnet のままにする。
- 恒久停止: routine を削除(過去セッションは残る)。

## Common Issues

### PR を開いてもレビューが走らない

- 原因: Claude GitHub App 未インストール(`/web-setup` だけでは webhook が来ない)/ draft で除外 /
  preview 中の webhook 時間上限に到達 / 日次実行上限に到達。
- 対処: App のインストールを確認。draft を解除。<https://claude.ai/code/routines> で残り実行回数を確認。

### 修正を push しても再レビューが走らない

- 原因: トリガーに `synchronize` が入っていない(`opened` のみ)/ draft への push(`Is draft` で除外)。
- 対処: routine のトリガーに `synchronize` を追加。

### 直したのにスレッドが Resolve されない

- 原因: 自動 Resolve はモデル判断。直りが不明瞭、または rubric の収束ルールが未反映。
- 対処: セッションログを確認。問題なければ手動 Resolve で進める(merge は元々人間)。

### セッションは green なのにコメントが無い

- 原因: green は「起動成功」であって task 成功ではない。プロンプトの取りこぼし等。
- 対処: 該当セッションを開いてログを読む。ネットワーク 403 や指示の誤解がそこに出る。

### test.yml と指摘が重複する

- 対処: rubric の「CI と重複しない」ルールが効いているか確認。基準を変えるなら
  `.github/claude/review-rubric.md` を直す。

## Related

- 判断記録: [ADR 0008](../adr/0008-pr-review-via-cloud-routine.md)
- 審査基準: [`.github/claude/review-rubric.md`](../../.github/claude/review-rubric.md)
- Routines ドキュメント: <https://code.claude.com/docs/en/routines>
- Claude Code on the web: <https://code.claude.com/docs/en/claude-code-on-the-web>
- テスト CI (役割分担の相手): [`.github/workflows/test.yml`](../../.github/workflows/test.yml)
- テスト戦略: [`docs/testing/strategy.md`](../testing/strategy.md)
- ドキュメント戦略: [`docs/documentation/strategy.md`](../documentation/strategy.md)
