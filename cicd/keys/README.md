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

| ファイル                | 中身           | commit する?                                                                      |
| ----------------------- | -------------- | --------------------------------------------------------------------------------- |
| `e2e-artifacts.pub.asc` | GPG **公開**鍵 | **する** (公開鍵は公開してよい)                                                   |
| 秘密鍵                  | —              | **しない**。管理系 RG の Azure Key Vault に置く (#302 完了までは暫定で PO の手元) |

**公開鍵が無い間、deploy は trace を残さず `::warning::` を出して続行する**
(ADR 0045 D6)。鍵の準備前に平文で上がる事故を構造的に防ぐため。

**公開鍵は既に commit 済みなので、暗号化された trace は現時点から artifact に残ります。**
秘密鍵の設置状態は workflow が確認しません (確認する必要がない — CI は暗号化しかしない)。
つまり秘密鍵がまだ Key Vault に無くても **artifact は溜まり、後から復号できます**。

## ⚠️ 鍵に触る操作はすべて PO の管理環境で行う

**鍵の生成・ローテーション・復号は、いずれも PO の管理環境でのみ行う。**
**エージェントのサンドボックスで実行しないこと** — パスフレーズ無しの長期秘密鍵が
ディスクに置かれ、そのセッション内の任意コードから読める状態になる
([ADR 0045](../../docs/adr/0045-e2e-artifacts-are-secret-by-default.md)
「エージェント復号を保留する理由」)。

### gpg 実行時の注意: GNUPGHOME は短いパスにする

gpg 2.x の秘密鍵操作は gpg-agent 経由で、agent の Unix ドメインソケットには
**パス長制限 (約 108 文字)** がある。`GNUPGHOME` が長いと

```text
gpg-agent: socket name '…/S.gpg-agent.browser' is too long
gpg: agent_genkey failed: No agent running
```

で失敗し、**「この環境では gpg が使えない」と誤診しやすい** (2026-08-12 に実際に
踏んだ)。深い階層で作業している場合は短いパスを明示すること。

## 鍵を作る (作成済み)

鍵ペアは 2026-08-12 に作成済み。公開鍵は `e2e-artifacts.pub.asc` として commit されて
おり、秘密鍵の設置先は [#301](https://github.com/yomote/mind-inbox/issues/301) で扱う
(恒久解は管理系 RG の Key Vault / [#302](https://github.com/yomote/mind-inbox/issues/302))。

作り直すときの手順 (**PO の管理環境でのみ実行する**。エージェントのサンドボックスでは実行しない):

```bash
export GNUPGHOME="$HOME/.gnupg-e2e"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
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

**置き場所は持続層 (管理系) RG の Key Vault** (ADR 0045 D5 /
[ADR 0046](../../docs/adr/0046-environment-rebuildable-from-declaration.md) D1 が層の実体を定義)。
環境の RG に置くと `cleanup-env.sh` の RG 削除に巻き込まれる
([#302](https://github.com/yomote/mind-inbox/issues/302))。

> **2026-08-12 更新**: ADR 0046 D6 で `PURGE_DELETED_KEYVAULTS` の既定を `false` にしたため、
> 環境 RG に置いた場合でも **soft-delete による救済は残る**ようになった (以前は purge まで走り、
> 救済不能に消えていた)。ただしこれは事故を止める安全弁であって置き場所の答えではない —
> **秘密鍵は持続層に置く**方針は変わらない。

持続層 RG ができるまでは暫定で PO の手元に置き、復号は PO が行う。

> **⚠️ 2026-08-12 の debrief で方式が変わりました (ADR 0045 D5 改訂)。**
> 恒久形は「Key Vault の**鍵オブジェクト**に非エクスポートで置き、`az keyvault key decrypt` で
> **Key Vault の中で復号する**」= 秘密鍵は一度も外に出ず、**エージェントも復号してよい**。
> 暗号方式も gpg から封筒暗号 (AES + RSA-OAEP) に変わります。
>
> **ただし切り替えは [#302](https://github.com/yomote/mind-inbox/issues/302) の持続層 RG ができてから。**
> それまでは下の GPG 手順が正典で、**復号できるのは PO のみ**です。
> 既存の `.gpg` artifact は移行後も現行の GPG 鍵で復号します。

## 復号して trace を見る (**PO の管理環境でのみ** / #302 完了までの手順)

> ⚠️ **エージェントのサンドボックスで復号しないこと。** `gpg --import` した時点で秘密鍵が
> ディスク (`$GNUPGHOME/private-keys-v1.d/*.key`) に置かれ、**そのセッション内の任意の
> コードが読める**。一度読み出せばセッションの外へ持ち出せるため、**鍵を交換するまでの
> 全 artifact** が復号可能になる。サンドボックスが使い捨てであることは被害を限定しない
> ([ADR 0045](../../docs/adr/0045-e2e-artifacts-are-secret-by-default.md) 「エージェント
> 復号を保留する理由」/ 2026-08-12 の Codex レビュー P1)。
>
> **エージェント復号を有効にできるのは、鍵をサンドボックスに出さない方式** (Key Vault の
> 非エクスポート鍵オブジェクト + `az keyvault key decrypt`、または隔離された復号ブローカー)
> **が用意できてから**。

PO の管理環境で実行する:

```bash
# GNUPGHOME を用意する (パス長の注意は上を参照)
export GNUPGHOME="$HOME/.gnupg-e2e"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
echo "allow-loopback-pinentry" > $GNUPGHOME/gpg-agent.conf

# 秘密鍵を取り込む (Key Vault から / #302 完了までは手元の控えから)
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

**エージェントが読めるのは平文で上げている証拠だけ** (スクリーンショット / `error-context.md`)。
実際 2026-08-12 の [#293](https://github.com/yomote/mind-inbox/issues/293) は
スクリーンショット 1 枚で原因を特定できており、trace が要る場面は限られる。
artifact の取得自体はエージェントからも可能 (`download_workflow_run_artifact` で
署名付き URL を得る。2026-08-12 に実測済み)。

## 鍵を替えるとき

**方式が移行中なので手順が 2 つある** ([ADR 0045](../../docs/adr/0045-e2e-artifacts-are-secret-by-default.md) D5 / D9)。
今どちらかは、リポジトリに置かれている公開鍵の形 (`*.pub.asc` = GPG / `*.pub.pem` = RSA 鍵オブジェクトの公開部) で判る。

### #302 完了まで — GPG (現行)

**PO の管理環境で**鍵を作り直し、`e2e-artifacts.pub.asc` を差し替えて commit するだけ。
CI 側の変更は要らない (秘密を持っていないため)。**古い鍵で暗号化済みの artifact は
古い秘密鍵でしか開けない**ので、**保持期限 (14 日) が切れるまで旧秘密鍵を捨てない**。

### #302 完了後 — Key Vault の非エクスポート RSA 鍵オブジェクト (恒久)

鍵は Key Vault から出せないので、「作り直して差し替える」ではなく**新しいバージョンを足す**形になる。

1. **新バージョンを作る** — `az keyvault key rotate`（またはポリシー自動ローテーション）。
   **旧バージョンは無効化も削除もしない** — 保持中 (14 日) の artifact は旧バージョンで wrap されており、
   `az keyvault key decrypt` は wrap 時のバージョンを指定して呼ぶ必要がある
   (`.enc` にどのバージョンで wrap したかが入っている / ADR 0045 D9)
2. **公開鍵の写しを更新して commit する** — `az keyvault key download --version <新> --encoding PEM` で
   取り出し、リポジトリの公開鍵ファイルを差し替える。**ここを忘れると CI は旧バージョンで wrap し続ける**
   (壊れはしないが、ローテーションが効いていない)
3. **旧バージョンを消してよいのは、それで wrap された artifact が全部期限切れになってから**。
   消すと**その artifact は永久に開けない** (鍵が Key Vault の外に無い = 復旧手段が存在しない)
