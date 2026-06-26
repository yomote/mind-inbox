# Claude PR Review (LLM-as-a-judge) のセットアップ

## Trigger

PR に対する自動レビュー (`claude-review` ワークフロー) を有効化したい / 動かなくなった時。
開発を回している Claude セッションとは**別軸**で、PR が来るたびに審査役の Claude が
diff を戦略 doc・設計・PR テンプレ整合の 3 軸でレビューする仕組み。

## Prerequisites

- リポジトリの **Settings → Secrets and variables → Actions** への書き込み権限 (admin)
- Anthropic API キー (<https://console.anthropic.com/>) — **すべて web で完結 / ローカル作業不要 / 失効なし**。
  コストは Console の **spend limit** で上限を固定できる (従量課金だが低頻度ソロなら数十円/月レベル)
- 審査基準は [`.github/claude/review-rubric.md`](../../.github/claude/review-rubric.md)
- ワークフロー定義は [`.github/workflows/claude-review.yml`](../../.github/workflows/claude-review.yml)

## Steps

1. Anthropic Console で **API キーを発行**し、念のため **spend limit (例: 月 $5)** を設定する
   (<https://console.anthropic.com/> → API Keys / Limits)。

2. キーを GitHub Secret に登録する (キー名は `ANTHROPIC_API_KEY` 固定)。
   GUI: Settings → Secrets and variables → Actions → New repository secret → web 上で貼るだけ。

   ```bash
   # gh CLI を使う場合
   gh secret set ANTHROPIC_API_KEY --repo yomote/mind-inbox   # → キーを貼り付け
   ```

3. このワークフローを `main` にマージする (改ざん防止のため `claude-code-action` は
   default branch に同一内容で存在するまで自分をスキップする。Common Issues 参照)。

4. マージ後、別の小さな PR を立てて `claude-review` が起動するか確認する。

   ```bash
   gh run list --repo yomote/mind-inbox --workflow claude-review.yml
   ```

## Verification

実行後、次がすべて満たされていることを確認:

- [ ] `claude-review` ワークフローが PR で success になる (`gh run list --workflow claude-review.yml`)
- [ ] PR に `<!-- claude-pr-review -->` マーカー付きのサマリコメントが 1 本付く
- [ ] 同じ PR に再 push しても**コメントが増殖せず**、既存サマリが更新される
- [ ] blocker / major 指摘がある場合、該当行に inline comment が付く

## 認証 (API キー) について

`ANTHROPIC_API_KEY` を採用している。理由:

- **ローカル作業ゼロ**: 発行も登録も web で完結 (OAuth トークンの `claude setup-token` のような
  ローカルのブラウザ認可が不要)。
- **失効しない**: 定期ローテーションが不要。検知ワークフロー等のお守りもいらない。
- **コストは上限固定可**: Console の spend limit で青天井を防ぐ。低頻度ソロ運用なら実コストは小さい。
- **scoped・revocable**: 漏洩時はキーを 1 つ失効させれば済む (個人アカウント全体を背負わない)。

> サブスク枠の OAuth トークン (`claude_code_oauth_token`) も使えるが、ローカル認可 + 定期失効が
> 必要になるため本リポジトリでは採用しない。

## セキュリティ (公開リポジトリ)

このリポジトリは public。秘密情報の扱いで守るべき不変条件:

- **トリガーは `pull_request` のまま。`pull_request_target` に変えない。**
  `pull_request_target` は base ブランチの権限 + Secret + write 権限で untrusted な PR の
  文脈を実行する。judge は PR の diff・本文 (= 攻撃者が書ける入力) を読むので、
  prompt injection と Secret 窃取が組み合わさる典型的な穴になる。絶対に切り替えない。
- **fork PR では走らない (設計どおり)。** `pull_request` では fork からの PR に Secret が
  渡らない (GitHub 仕様)。`claude-review.yml` は `head.repo.full_name == github.repository`
  で同一リポジトリのブランチに限定し、外部 PR では起動しない。
  → 外部コントリビュータの PR は自動レビュー対象外。レビューしたい場合は信頼した上で
  ブランチを本リポジトリに取り込む / 手動で `gh workflow run` する。
- **GitHub 側の設定を確認** (Settings → Actions → General):
  - "Fork pull request workflows from outside collaborators" は
    *Require approval for first-time / all outside collaborators* にしておく。
  - Workflow permissions は read 既定にし、必要な write はワークフロー側の `permissions:` で個別付与
    (本ワークフローは既にそうしている)。
- **Secret = write 権限者なら誰でも読める。** public/private を問わず、write 権限を持つ
  collaborator はブランチに細工したワークフローを push すれば Secret を抜ける。
  → write 権限は信頼できる人だけに絞る。`ANTHROPIC_API_KEY` は scoped・revocable で
  spend limit も付くため、万一漏洩しても該当キーを失効させれば被害を箱に閉じ込められる。

## Rollback

レビューを止めたい / コストを抑えたい場合:

1. 一時停止: Actions タブ → `claude-review` → `Disable workflow`。
2. 恒久停止: `.github/workflows/claude-review.yml` を削除して commit。
3. 自動起動をやめて手動だけにしたい場合は `on:` を
   `workflow_dispatch` や label トリガーへ変更する (rubric は流用可)。

## Common Issues

### 新規追加直後、ジョブは success なのにレビューが付かない (最重要)

- 症状: ログに `Skipping action due to workflow validation ... must exist and have
  identical content to the version on the repository's default branch`。
- 原因: `claude-code-action` の改ざん防止機構。レビュー用ワークフローが**デフォルトブランチ
  (`main`) に同一内容で存在しない限り action は自分をスキップする** (PR 内でワークフローを
  書き換えて悪用する攻撃の防止)。新規追加時や `claude-review.yml` 自体を変更した PR では必ず起きる。
- 対処: この PR を `main` にマージする。以降、ワークフローを変更しない通常の PR では正常に走る。
  動作確認は**マージ後に別の小さな PR を立てる**こと。

### ワークフローは走るがコメントが付かない

- 原因: `ANTHROPIC_API_KEY` 未設定、`permissions:` に `id-token: write` か `pull-requests: write` の欠如、
  または Console の spend limit 到達でキーが弾かれている。
- 対処: Secret の登録と権限を確認。認証エラーなら Console でキーの有効性・残枠を確認。

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
- レビュー ワークフロー: [`.github/workflows/claude-review.yml`](../../.github/workflows/claude-review.yml)
- テスト CI (役割分担の相手): [`.github/workflows/test.yml`](../../.github/workflows/test.yml)
- テスト戦略: [`docs/testing/strategy.md`](../testing/strategy.md)
- ドキュメント戦略: [`docs/documentation/strategy.md`](../documentation/strategy.md)
