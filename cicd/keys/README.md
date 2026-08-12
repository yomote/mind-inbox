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
| 秘密鍵 | — | **しない**。管理系 RG の Azure Key Vault に置く (#302 完了までは暫定で PO の手元) |

**公開鍵が無い間、deploy は trace を残さず `::warning::` を出して続行する**
(ADR 0045 D6)。鍵の準備前に平文で上がる事故を構造的に防ぐため。

## ⚠️ GNUPGHOME は短いパスにする

**先に読むこと。** gpg 2.x の秘密鍵操作は gpg-agent 経由で、agent の Unix ドメイン
ソケットには**パス長制限 (約 108 文字)** がある。`GNUPGHOME` が長いと

```text
gpg-agent: socket name '…/S.gpg-agent.browser' is too long
gpg: agent_genkey failed: No agent running
```

で失敗し、**「この環境では gpg が使えない」と誤診しやすい** (2026-08-12 に実際に
踏んだ)。エージェントのサンドボックスは作業ディレクトリのパスが長いので、
`GNUPGHOME=/tmp/gk` のような短いパスを明示して使う。

## 鍵を作る (作成済み)

鍵ペアは 2026-08-12 に作成済み。公開鍵は `e2e-artifacts.pub.asc` として commit されて
おり、秘密鍵の設置先は [#301](https://github.com/yomote/mind-inbox/issues/301) で扱う
(恒久解は管理系 RG の Key Vault / [#302](https://github.com/yomote/mind-inbox/issues/302))。

作り直すときの手順:

```bash
export GNUPGHOME=/tmp/gk; mkdir -p $GNUPGHOME; chmod 700 $GNUPGHOME
echo "allow-loopback-pinentry" > $GNUPGHOME/gpg-agent.conf

# 1. 鍵ペア (パスフレーズ無し — Key Vault に入れて機械が使うため)
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key "mind-inbox e2e artifacts <noreply@mind-inbox.invalid>" rsa4096 encr never

# 2. 公開鍵 → cicd/keys/e2e-artifacts.pub.asc として commit
gpg --armor --export "mind-inbox e2e artifacts" > cicd/keys/e2e-artifacts.pub.asc

# 3. 秘密鍵 → 管理系 RG の Key Vault へ (#302 完了までは暫定で PO の手元)
#    az keyvault secret set --vault-name <Vault> --name e2e-artifact-private-key --value @-
gpg --armor --export-secret-keys "mind-inbox e2e artifacts"
```

**秘密鍵は GitHub Secrets にもサンドボックスの環境変数にも置かない。**

- GitHub Secrets: workflow が復号できても、**public リポジトリでは Actions のログが
  公開**なので復号結果の出力先が無い
- 環境変数: [ADR 0031](../../docs/adr/0031-agent-reaches-outside-via-github-actions.md)
  の「サンドボックスに長期クレデンシャルを置かない」に反する。Claude Code の公式
  ドキュメントも「cloud environments have no dedicated secrets store, so don't add
  API keys or other credentials」と明示している

**置き場所は管理系 RG の Key Vault** (ADR 0045 D5)。環境の RG に置くと
`cleanup-env.sh` が purge するため、撤収のたびに鍵が消える
([#302](https://github.com/yomote/mind-inbox/issues/302))。管理系 RG ができるまでは
暫定で PO の手元に置き、復号は PO が行う。

## 復号して trace を見る (エージェント / PO)

```bash
# 短い GNUPGHOME を用意する (上の警告を参照)
export GNUPGHOME=/tmp/gk; mkdir -p $GNUPGHOME; chmod 700 $GNUPGHOME
echo "allow-loopback-pinentry" > $GNUPGHOME/gpg-agent.conf

# 秘密鍵を取り込む (Key Vault から device-code ログインで取得 — ADR 0006 / 0045 D5)
az login --use-device-code
az keyvault secret show --vault-name <管理系RGのVault> --name e2e-artifact-private-key \
  --query value -o tsv | gpg --batch --import

# artifact を取得して復号
#   artifact 名: e2e-live-trace-<run_id>  /  中身: <test-name>_trace.zip.gpg
gpg --batch --yes --pinentry-mode loopback --passphrase '' \
    --decrypt -o trace.zip <name>_trace.zip.gpg
pnpm --dir apps/frontend exec playwright show-trace trace.zip
```

2026-08-12 に実鍵で往復を実測済み: 暗号化 → 別キーリングで取り込み → 復号で
**sha256 が元と一致**し、秘密鍵を持たない環境では `decryption failed: No secret key`
になることを確認した。

artifact の取得はエージェントからも可能 (`download_workflow_run_artifact` で署名付き
URL を得て取得する。2026-08-12 に実測済み)。

## 鍵を替えるとき

`e2e-artifacts.pub.asc` を差し替えて commit し、Key Vault の秘密鍵を入れ替えるだけ。
CI 側の変更は要らない (秘密を持っていないため)。**古い鍵で暗号化済みの artifact は
古い秘密鍵でしか開けない**点にだけ注意する (保持は 14 日)。
