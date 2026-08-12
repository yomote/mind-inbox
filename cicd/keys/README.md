# E2E 成果物の暗号化鍵

実環境 E2E (`e2e-live`) の **trace を暗号化するための公開鍵**を置く場所。判断の正典は
[ADR 0045](../../docs/adr/0045-e2e-artifacts-are-secret-by-default.md)。

## なぜ鍵が要るのか

trace には **実 BFF アクセストークンが入る**。`e2e-live/entra-login.ts` が Entra の
token エンドポイントを偽装して `access_token` に実トークンを返しており (BFF の
EasyAuth が本物なので偽装できない)、`trace` はネットワークのやり取りを丸ごと記録
するため。

**このリポジトリは public** で、GitHub Actions の artifact はサインイン済みの
GitHub ユーザーなら誰でもダウンロードできる。したがって trace を素で上げることは
できない。

スクラブ (文字列置換) ではなく暗号化を選んだのは、**暗号化は中身の形に依存しない**
から。2026-08-12 の実測で、秘密は trace 内の 3 種類のエントリ (spec のソース /
アクション記録 / DOM スナップショット) に散らばっており、外科的な除去は列挙漏れで
静かに漏れる。

## ファイル

| ファイル | 中身 | commit する? |
| --- | --- | --- |
| `e2e-artifacts.pub.asc` | GPG **公開**鍵 | **する** (公開鍵は公開してよい) |
| 秘密鍵 | — | **しない**。Claude Code 実行環境の環境変数 `E2E_ARTIFACT_PRIVATE_KEY` に置く |

**公開鍵が無い間、deploy は trace を残さず `::warning::` を出して続行する**
(ADR 0045 D6)。鍵の準備前に平文で上がる事故を構造的に防ぐため。

## 鍵を作る (人間の作業・1 回だけ)

エージェントは秘密鍵を生成しない (生成すると秘密鍵がセッションの記録に残るため)。
PO が手元で実行する:

```bash
# 1. 鍵ペアを作る (パスフレーズ無し — 環境変数に入れて機械が使うため)
gpg --batch --quick-generate-key "mind-inbox e2e artifacts <noreply@example.com>" default default never

# 2. 公開鍵を書き出す → この中身を cicd/keys/e2e-artifacts.pub.asc として commit
gpg --armor --export "mind-inbox e2e artifacts"

# 3. 秘密鍵を書き出す → Claude Code 環境の環境変数 E2E_ARTIFACT_PRIVATE_KEY に貼る
#    (あわせてパスワードマネージャにも控える。失うと過去の artifact は開けない)
gpg --armor --export-secret-keys "mind-inbox e2e artifacts"
```

**秘密鍵は GitHub Secrets に置かない。** workflow が復号できても、public リポジトリ
では Actions のログが公開なので出力先が無い (ADR 0045 D5)。

## 復号して trace を見る (エージェント / PO)

```bash
# 秘密鍵を取り込む (エージェントは環境変数から)
printf '%s' "$E2E_ARTIFACT_PRIVATE_KEY" | gpg --batch --import

# artifact を取得して復号
#   artifact 名: e2e-live-trace-<run_id>  /  中身: <test-name>__trace.zip.gpg
gpg --batch --yes --decrypt -o trace.zip <name>__trace.zip.gpg
pnpm --dir apps/frontend exec playwright show-trace trace.zip
```

artifact の取得はエージェントからも可能 (`download_workflow_run_artifact` で署名付き
URL を得て取得する。2026-08-12 に実測済み)。

## 鍵を替えるとき

`e2e-artifacts.pub.asc` を差し替えて commit し、環境変数の秘密鍵を入れ替えるだけ。
CI 側の変更は要らない (秘密を持っていないため)。**古い鍵で暗号化済みの artifact は
古い秘密鍵でしか開けない**点にだけ注意する (保持は 14 日)。
