# {Runbook 名}

## Trigger

{この Runbook をいつ使うか — 具体的な状況を 1〜3 文}

## Prerequisites

- {必要なアクセス権 / ロール}
- {必要なツール (例: Azure CLI、gh CLI)}
- {必要な環境変数 / シークレット}
- {確認すべき前提状態}

## Steps

1. {ステップ 1 — 実行可能なコマンドで書く}

   ```bash
   # 例
   az login
   ```

2. {ステップ 2}

   ```bash
   # 例
   cicd/scripts/...
   ```

3. {ステップ 3}

## Verification

実行後、次がすべて満たされていることを確認:

- [ ] {確認項目 1 — できれば具体的なコマンドで}
- [ ] {確認項目 2}
- [ ] {確認項目 3}

## Rollback

{途中失敗 / 結果が NG だった場合の戻し方}

1. {ロールバック手順 1}
2. {ロールバック手順 2}

## Common Issues

### {症状 / エラーメッセージ}

- 原因: {なぜ起きるか}
- 対処: {どう直すか}

### {症状 2}

- 原因:
- 対処:

## Related

- ADR: [{ADR タイトル}](../adr/NNNN-xxx.md)
- 関連 Runbook: [{タイトル}](./xxx.md)
- スクリプト: `cicd/scripts/xxx/`
