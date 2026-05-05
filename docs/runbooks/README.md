# Runbooks

> 運用手順を集約する場所。「何をどの順序で実行するか」を Runbook に書く。
> 戦略全体: [`docs/documentation/strategy.md`](../documentation/strategy.md)

## Runbook とは

特定のオペレーション (デプロイ / ロールバック / 障害対応 / 環境クリーンアップ等) を**人が再現できる手順書**として残したもの。

## いつ書くか

- 新しい運用手順を導入する時
- 既存 README に手順が散らばっていて見つけづらい時
- インシデント対応の学びを残したい時 (incident-response.md に追記)

書かなくて良いもの:

- アーキテクチャ判断 (ADR の領域)
- 一時的な調査メモ
- スクリプトの内部実装 (`cicd/scripts/*/README.md` で十分)

## 書き方

### 1. ファイルを作る

`docs/runbooks/{name}.md` の形式 (kebab-case)。

```bash
cp docs/runbooks/template.md docs/runbooks/my-procedure.md
```

### 2. 書く

[`template.md`](./template.md) のセクションを埋める:

- **Trigger** — いつこの Runbook を使うか (具体的な状況)
- **Prerequisites** — 必要なアクセス / ツール / env var
- **Steps** — 番号付きで実行可能なコマンドを書く
- **Verification** — 成功確認方法 (チェックリスト)
- **Rollback** — 途中失敗時の戻し方
- **Common Issues** — 既知のハマりどころ

### 3. レビュー

Runbook PR は実際にその手順を**踏んだ人**が書くか、レビューに入ること。机上のものは劣化が速い。

## 既存スクリプトとの関係

`cicd/scripts/*/README.md` はスクリプトの実装説明として残す。Runbook は「いつ・なぜ・どう実行するか」を書き、スクリプトの README にリンクする。

## 既存 Runbook

(初期 4 本は #12 で起こす)

- `deploy.md` — `deploy-all.sh` の前提・流れ・検証
- `rollback.md` — Functions zip 戻し / Container App revision 切り替え
- `cleanup-env.md` — `cleanup-env.sh` の使いどころ
- `incident-response.md` — インシデント発生時の最初の動き
