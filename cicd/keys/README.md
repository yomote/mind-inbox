# E2E 成果物の暗号化鍵

実環境 E2E (`e2e-live`) の **trace を暗号化するための公開鍵**を置く場所。

> **⚠️ 手順はここにありません。** 鍵の生成・復号・ローテーション・失効の手順は
> **[`docs/runbooks/e2e-trace-keys.md`](../../docs/runbooks/e2e-trace-keys.md)** が正典です
> (運用手順は `docs/runbooks/` に置く / root [`CLAUDE.md`](../../CLAUDE.md))。
> このファイルが持つのは**ここに何のファイルを置くか**だけです。

| 何を知りたいか                              | どこを見るか                                                                                                                                                                                                                          |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **鍵をどう扱うか (手順)**                   | [`docs/runbooks/e2e-trace-keys.md`](../../docs/runbooks/e2e-trace-keys.md)                                                                                                                                                            |
| **鍵をどの RG / どの層に置くか**            | [ADR 0056](../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D1 (管理系 RG `rg-mgmt-mindbox`。**Accepted** / 2026-08-15 PO 裁定)                                                                    |
| **鍵の実体 (非エクスポート / 鍵長 / 権限)** | [`cicd/iac/main-mgmt.bicep`](../iac/main-mgmt.bicep) の `e2eTraceKey` と、適用手順の [`docs/runbooks/mgmt-layer-apply.md`](../../docs/runbooks/mgmt-layer-apply.md)                                                                   |
| **なぜ暗号化するのか / 何を秘密扱いするか** | [2026-08-12 の裁定記録](../../docs/adr/archive/operations/e2e-artifacts-are-secret-by-default.md) (当時 ADR 0045。[#385](https://github.com/yomote/mind-inbox/pull/385) で運用文書へ退避したので**現行ルールの置き場ではありません**) |

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

| ファイル                 | 中身                                                                | commit する?                                                                               |
| ------------------------ | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `e2e-artifacts.pub.asc`  | GPG **公開**鍵 (**管理系 RG の適用まで**の暫定)                     | **する** (公開鍵は公開してよい)                                                            |
| `e2e-artifacts.pub.json` | 恒久形。**公開鍵 (PEM) と Key Vault の鍵バージョンを 1 ファイルに** | **する** (管理系 RG を適用したら作る)                                                      |
| 秘密鍵                   | —                                                                   | **しない**。管理系 RG (`rg-mgmt-mindbox`) の Key Vault に置く (適用までは暫定で PO の手元) |

**いまリポジトリに置かれている公開鍵の形が、そのまま「いまどちらの方式か」**を表す
(`*.pub.asc` = GPG / `*.pub.json` = 封筒暗号 + Key Vault の非エクスポート鍵)。
方式ごとに誰が復号してよいかが変わるので、**必ず
[Runbook](../../docs/runbooks/e2e-trace-keys.md) の表を見てから作業すること。**

> **「#302 完了まで」は「`rg-mgmt-mindbox` に `main-mgmt.bicep` を適用するまで」と読んでください。**
> 宣言は [#419](https://github.com/yomote/mind-inbox/pull/419) で main に入っており
> ([`cicd/iac/main-mgmt.bicep`](../iac/main-mgmt.bicep) の `e2eTraceKey` — 非エクスポートの RSA 鍵オブジェクト)、
> 残っているのは**一度きりの手動適用**だけです ([`mgmt-layer-apply.md`](../../docs/runbooks/mgmt-layer-apply.md))。
> **適用が済んでいないうちは GPG 方式が現行**で、**復号できるのは PO だけ**です。

### `e2e-artifacts.pub.json` を 1 ファイルにする理由

裁定記録 D4 / D9。**CI は Azure の資格情報を持たない**ので、「どの鍵バージョンで wrap したか」を
リポジトリから受け取る以外に知る方法が無い。公開鍵とバージョンを別ファイルにすると、
片方だけ更新して**食い違ったまま暗号化し続ける**ことができてしまい、それに気づくのは
復号しようとした時 (最大 14 日後) になる。

```json
{
  "vault": "https://<管理系 RG の Vault>.vault.azure.net/",
  "keyName": "e2e-artifacts",
  "keyVersion": "<Key Vault の鍵バージョン ID>",
  "wrapAlgorithm": "RSA-OAEP-256",
  "publicKeyPem": "-----BEGIN PUBLIC KEY-----\n…"
}
```

**1 ファイルにしても食い違いは防げない** — `publicKeyPem` と `keyVersion` は同じ JSON の中で
独立に編集できる。だから生成手順そのものを契約にしてある
([Runbook](../../docs/runbooks/e2e-trace-keys.md) の「鍵を替える」)。

**このファイルが読めない / 壊れているときは、CI は暗号化せず落とす** (D4 の fail closed)。
バージョンが取れないまま暗号化すると、**開けない artifact** ができる。

## CI 側の振る舞い (このディレクトリを読むのは誰か)

- **公開鍵が無い間、deploy は trace を残さず `::warning::` を出して続行する** (裁定記録 D6)。
  鍵の準備前に平文で上がる事故を構造的に防ぐため。**沈黙はしない**
- **秘密鍵の設置状態は workflow が確認しない** (確認する必要がない — CI は暗号化しかしない)。
  つまり秘密鍵がまだ Key Vault に無くても **artifact は溜まり、後から復号できる**
- 読んでいるのは [`cicd/scripts/deploy/encrypt-e2e-traces.sh`](../scripts/deploy/encrypt-e2e-traces.sh) で、
  呼び元は **`deploy.yml` と `golden-path-monitor.yml` の 2 つ**。
  **方式を替えるときは呼び元を全部同時に替える** — 片方だけだと upload の
  `hashFiles` 条件が偽になり、**その workflow の trace が無言で消える**
  ([Runbook](../../docs/runbooks/e2e-trace-keys.md) の「方式を GPG から封筒暗号へ切り替える」/
  取り残しは `cicd/scripts/deploy/test_encrypt_e2e_traces.py` が落とす)

## 秘密鍵を GitHub Secrets / 環境変数に置かない

- GitHub Secrets: workflow が復号できても、**public リポジトリでは Actions のログが
  公開**なので復号結果の出力先が無い
- 環境変数: [ADR 0031](../../docs/adr/archive/operations/agent-reaches-outside-via-github-actions.md)
  の「サンドボックスに長期クレデンシャルを置かない」に反する。Claude Code の公式
  ドキュメントも「cloud environments have no dedicated secrets store, so don't add
  API keys or other credentials」と明示している

**置き場所は管理系 RG (`rg-mgmt-mindbox`) の Key Vault** (裁定記録 D5 /
[ADR 0056](../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D1 が層を定義)。
環境の RG に置くと `cleanup-env.sh` の RG 削除に巻き込まれる
([#302](https://github.com/yomote/mind-inbox/issues/302))。

> **2026-08-12 更新**: ADR 0046 D6 で `PURGE_DELETED_KEYVAULTS` の既定を `false` にしたため、
> 環境 RG に置いた場合でも **soft-delete による救済は残る**ようになった (以前は purge まで走り、
> 救済不能に消えていた)。ただしこれは事故を止める安全弁であって置き場所の答えではない —
> **秘密鍵は管理系に置く**方針は変わらない。
