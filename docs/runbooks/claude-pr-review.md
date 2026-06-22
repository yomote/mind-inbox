# Claude PR Review (LLM-as-a-judge) のセットアップ

## Trigger

PR に対する自動レビュー (`claude-review` ワークフロー) を有効化したい / 動かなくなった時。
開発を回している Claude セッションとは**別軸**で、PR が来るたびに審査役の Claude が
diff を戦略 doc・設計・PR テンプレ整合の 3 軸でレビューする仕組み。

## Prerequisites

- リポジトリの **Settings → Secrets and variables → Actions** への書き込み権限 (admin)
- 認証はどちらか:
  - **既定 (推奨)**: Claude Pro/Max サブスク枠を使う OAuth トークン — 追加の従量課金なし。レート上限は個人サブスクと共有
  - **代替**: Anthropic API キー (<https://console.anthropic.com/>) — 従量課金が発生する
- 審査基準は [`.github/claude/review-rubric.md`](../../.github/claude/review-rubric.md)
- ワークフロー定義は [`.github/workflows/claude-review.yml`](../../.github/workflows/claude-review.yml)

## Steps

1. 認証トークンをリポジトリ Secret に登録する。

   **既定: サブスク枠 OAuth トークン (従量課金なし)**

   ```bash
   # ローカルで Pro/Max にログイン済みの Claude Code から発行
   claude setup-token
   # → 出力されたトークンを Secret に登録 (キー名は CLAUDE_CODE_OAUTH_TOKEN 固定)
   gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo yomote/mind-inbox
   ```

   **代替: API 従量課金で回す場合**

   ```bash
   gh secret set ANTHROPIC_API_KEY --repo yomote/mind-inbox   # → API キーを貼り付け
   ```

   この場合は `.github/workflows/claude-review.yml` の `claude_code_oauth_token:` 行を
   `anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}` に差し替える。
   GUI なら Settings → Secrets and variables → Actions → New repository secret。

2. `anthropics/claude-code-action` を GitHub Actions が利用できることを確認する
   (public action なので追加設定は不要。Org でサードパーティ action を制限している場合のみ許可登録)。

3. 適当な PR を開く / 既存 PR に push して `claude-review` ワークフローが起動するか確認する。

   ```bash
   gh run list --repo yomote/mind-inbox --workflow claude-review.yml
   ```

## Verification

実行後、次がすべて満たされていることを確認:

- [ ] `claude-review` ワークフローが PR で success になる (`gh run list --workflow claude-review.yml`)
- [ ] PR に `<!-- claude-pr-review -->` マーカー付きのサマリコメントが 1 本付く
- [ ] 同じ PR に再 push しても**コメントが増殖せず**、既存サマリが更新される
- [ ] blocker / major 指摘がある場合、該当行に inline comment が付く

## Rollback

レビューを止めたい / コストを抑えたい場合:

1. 一時停止: Actions タブ → `claude-review` → `Disable workflow`。
2. 恒久停止: `.github/workflows/claude-review.yml` を削除して commit。
3. 自動起動をやめて手動だけにしたい場合は `on:` を
   `workflow_dispatch` や label トリガーへ変更する (rubric は流用可)。

## Common Issues

### ワークフローは走るがコメントが付かない

- 原因: 認証 Secret (`CLAUDE_CODE_OAUTH_TOKEN` または `ANTHROPIC_API_KEY`) 未設定 / 失効、
  または `permissions: pull-requests: write` 欠如。
- 対処: Secret の登録を確認。OAuth トークンは失効するので、認証エラー時は `claude setup-token` で再発行。

### コメントが push のたびに増えていく

- 原因: 既存 `<!-- claude-pr-review -->` コメントの検出に失敗し新規作成にフォールバックしている。
- 対処: ジョブログで `gh api .../comments` の取得結果を確認。マーカー文字列が rubric/プロンプトと一致しているか確認。

### test.yml と指摘が重複する

- 原因: judge がテスト pass/fail や lint を再指摘している。
- 対処: rubric の「CI と重複しない」ルールが効いているか確認。基準を変えるなら
  `.github/claude/review-rubric.md` を直す (ワークフロー側ではなく)。

### コストが高い

- 対処: `claude-review.yml` の `--model` を `claude-sonnet-4-6` のまま使う (既定)。
  draft PR は既に除外済み。`concurrency` で連続 push 時は古い実行をキャンセルしている。

## Related

- 審査基準: [`.github/claude/review-rubric.md`](../../.github/claude/review-rubric.md)
- ワークフロー: [`.github/workflows/claude-review.yml`](../../.github/workflows/claude-review.yml)
- テスト CI (役割分担の相手): [`.github/workflows/test.yml`](../../.github/workflows/test.yml)
- テスト戦略: [`docs/testing/strategy.md`](../testing/strategy.md)
- ドキュメント戦略: [`docs/documentation/strategy.md`](../documentation/strategy.md)
