# E2E trace の暗号化鍵を扱う (生成 / 復号 / ローテーション / 失効)

## Trigger

実環境 E2E (`e2e-live`) が残した **暗号化済み trace を開きたい**とき、**鍵を作り直す / ローテーションする**とき、**復号に使った資格情報が漏れた疑いがある**とき。

鍵ファイルそのものの置き場と commit 可否は [`cicd/keys/README.md`](../../cicd/keys/README.md)。**なぜ暗号化するのか**は [2026-08-12 の裁定記録](../adr/archive/operations/e2e-artifacts-are-secret-by-default.md) (当時 ADR 0045。[#385](https://github.com/yomote/mind-inbox/pull/385) で運用文書へ退避したので**現行ルールの置き場ではありません**)。以下に出てくる **D1〜D9 はその裁定記録の中の決定番号**です。

> **いま有効なのはどちらの方式か。** リポジトリに置かれている公開鍵ファイルの形で判ります。
>
> | 公開鍵ファイル           | 方式                                        | 復号できる人                   |
> | ------------------------ | ------------------------------------------- | ------------------------------ |
> | `e2e-artifacts.pub.asc`  | **GPG** (管理系 RG の適用前 / **いまここ**) | **PO のみ**                    |
> | `e2e-artifacts.pub.json` | **封筒暗号 + Key Vault の非エクスポート鍵** | **エージェントも実行してよい** |
>
> 切り替えの条件は `rg-mgmt-mindbox` への適用 ([#302](https://github.com/yomote/mind-inbox/issues/302))。鍵の宣言 (`main-mgmt.bicep` の `e2eTraceKey` / `exportable: false`) は [#419](https://github.com/yomote/mind-inbox/pull/419) で main に入っているので、残っているのは [`mgmt-layer-apply.md`](mgmt-layer-apply.md) の**一度きりの手動適用**だけです。

## Prerequisites

- **鍵に触ってよい場所は操作によって違います。**

  |                          | GPG (適用前)          | 恒久 (Key Vault の非エクスポート鍵オブジェクト) |
  | ------------------------ | --------------------- | ----------------------------------------------- |
  | 鍵の生成・ローテーション | **PO の管理環境のみ** | **PO の管理環境のみ**                           |
  | **復号**                 | **PO の管理環境のみ** | **エージェントが実行してよい**                  |

  **GPG で復号をエージェントにやらせない理由**: `gpg --import` した時点でパスフレーズ無しの長期秘密鍵がディスク (`$GNUPGHOME/private-keys-v1.d/*.key`) に置かれ、そのセッション内の任意コードから読める状態になる。一度読み出せばセッションの外へ持ち出せるため、**鍵を交換するまでの全 artifact** が復号可能になる。サンドボックスが使い捨てであることは被害を限定しない (2026-08-12 の Codex レビュー P1 / 裁定記録の「エージェント復号をどう扱ったか (経緯)」)。

  **恒久方式で許される理由**: `az keyvault key decrypt` は Key Vault の中で復号するので、**RSA 秘密鍵は一度も外に出ない**。ただし露出がゼロになるわけではない — 侵害されたセッションは保持中の wrapped key を順に開けるし、**返ってきた AES 鍵は失効できない**。残る露出の内訳は裁定記録 D5「消える穴 / 消えない穴」に書き出してあります (露出モデルは Codex の指摘で 4 回訂正した結果なので、**要約せずにそちらを読むこと**)。運用で守る手順は下の「侵害が疑われたら」。

- `az` (Azure CLI)。サンドボックスからは device-code で入る → [`claude-web-azure-access.md`](claude-web-azure-access.md)
- 恒久方式では Key Vault の **Crypto User** ロール。`main-mgmt.bicep` の `keyVaultCryptoUserPrincipalIds` は**既定で空**で、後から個別に付ける運用です ([`mgmt-layer-apply.md`](mgmt-layer-apply.md) Prerequisites)。**付与した principal がそのまま「復号できる人」**になるので、誰に付けたかを控えておくこと
- GPG 方式では `gpg` 2.x

### gpg 実行時の注意: GNUPGHOME は短いパスにする

gpg 2.x の秘密鍵操作は gpg-agent 経由で、agent の Unix ドメインソケットには**パス長制限 (約 108 文字)** があります。`GNUPGHOME` が長いと

```text
gpg-agent: socket name '…/S.gpg-agent.browser' is too long
gpg: agent_genkey failed: No agent running
```

で失敗し、**「この環境では gpg が使えない」と誤診しやすい** (2026-08-12 に実際に踏んだ)。深い階層で作業している場合は短いパスを明示してください。

## Steps

### A. 復号して trace を見る — 管理系 RG の適用**前** (GPG / **PO の管理環境でのみ**)

> ⚠️ **エージェントのサンドボックスで実行しないこと。** 理由は Prerequisites の表。

```bash
# GNUPGHOME を用意する (パス長の注意は上を参照)
export GNUPGHOME="$HOME/.gnupg-e2e"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
echo "allow-loopback-pinentry" > $GNUPGHOME/gpg-agent.conf

# 秘密鍵を取り込む (Key Vault から / 適用前は手元の控えから)
az login --use-device-code
az keyvault secret show --vault-name <管理系 RG の Vault> --name e2e-artifact-private-key \
  --query value -o tsv | gpg --batch --import

# artifact を取得して復号
#   artifact 名: e2e-live-trace-<run_id>  /  中身: <test-name>_trace.zip.gpg
gpg --batch --yes --pinentry-mode loopback --passphrase '' \
    --decrypt -o trace.zip <name>_trace.zip.gpg
pnpm --dir apps/frontend exec playwright show-trace trace.zip
```

**この方式の間、エージェントが読めるのは平文で上げている証拠だけ** (スクリーンショット / `error-context.md`)。実際 2026-08-12 の [#293](https://github.com/yomote/mind-inbox/issues/293) はスクリーンショット 1 枚で原因を特定できており、trace が要る場面は限られます。artifact の取得自体はエージェントからも可能 (`download_workflow_run_artifact` で署名付き URL を得る / [`ops-inspect.md`](ops-inspect.md))。

### B. 復号して trace を見る — 管理系 RG の適用**後** (封筒暗号 / **エージェントも実行してよい**)

恒久方式では**秘密鍵が Key Vault から出ない**ので、この手順はエージェントのサンドボックスで実行してよい (裁定記録 D5)。鍵 (`e2e-artifacts`) は `main-mgmt.bicep` が **`exportable: false`** で宣言しており、鍵 URI の控え方は [`mgmt-layer-apply.md`](mgmt-layer-apply.md) の手順 6。

```bash
az login --use-device-code            # ADR 0006。短命トークン

# artifact 名: e2e-live-trace-<run_id> / 中身: <test-name>_trace.zip.enc
#   .enc には version / wrapped AES 鍵 + 鍵バージョン / nonce / 暗号文+タグ が入る (D9)

# 1. .enc から wrapped AES 鍵と「wrap したときの鍵バージョン」を取り出す
# 2. Key Vault に開かせる (鍵は外に出ない。バージョンは .enc に書かれたものを使う)
az keyvault key decrypt --vault-name <管理系 RG の Vault> --name e2e-artifacts \
  --version "<.enc に記録されたバージョン>" --algorithm RSA-OAEP-256 \
  --value "<wrapped AES 鍵>" --data-type base64
# 3. 返った AES 鍵で AES-256-GCM 復号する。**タグ検証に失敗したら平文を出さず落とす** (D9)
```

> ⚠️ **バージョンは必ず `.enc` に記録されたものを指定する。** 版なしの鍵 URI (`e2eTraceKeyUri`) を渡すと Key Vault は**常に最新バージョン**で処理するので、**ローテーション後は旧バージョンで wrap された artifact が開けません**。しかも失敗するのは「証拠が要る」と気づいた瞬間で、そのとき元の trace はもうありません。
>
> ⚠️ **返ってきた AES 鍵は失効できない。** Key Vault は秘密鍵を守りますが、`decrypt` が返す AES 鍵は手元の平文であり、資格情報を revoke しても**その時点で開いた artifact は読める状態のまま**になります。復号は必要な artifact に絞ること。

### C. 侵害が疑われたら: 更新資格情報を revoke する

**`az login` はアクセストークンだけでなく更新資格情報 (refresh token) をディスクに置きます。** 侵害された環境の任意コードは**これを持ち出して別環境から `decrypt` を呼び続けられる**ので、**時間が経てば閉じる、は成り立ちません**。閉じるのは revoke したときだけ。

> 2026-08-12 に実測: エージェントセッションが、失効したアクセストークンを保存済みの refresh token だけで更新し、PO の関与なしに Azure API を叩き直せた。

```bash
# 1. 手元の資格情報を捨てる (これだけでは不十分 — 既に持ち出されていたら効かない)
az logout

# 2. その identity の更新資格情報を無効化する (これが本体)
#    アクション名は revokeSignInSessions。invalidateAllRefreshTokens は beta 専用の
#    旧名で v1.0 に存在せず、叩くと 404 で落ちる (= 侵害時に「本体」だけが失敗し、
#    露出が無期限に残る)。v1.0 のアクション名を使うこと
az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/me/revokeSignInSessions"

# 3. 上が 403 (Authorization_RequestDenied 等) を返したら、az CLI の同意済み委任権限に
#    このアクションに必要なもの (User.RevokeSessions.All / Directory.AccessAsUser.All)
#    が含まれていない。**そこで止めず、必ず次のどちらかに切り替える**:
#      (a) Entra 管理者が他人の identity として止める
az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/users/<objectId>/revokeSignInSessions"
#      (b) Entra 管理センター > ユーザー > 該当ユーザー > 「セッションの取り消し」
#          (画面操作。az が通らない環境でも確実に届く)

# 4. Key Vault のアクセスログで、想定外の decrypt が無いか確認する
```

> **未検証: 手順 2 が az CLI 経由で通るかは、この環境で実測できていません。** `az rest` は az CLI 自身のクライアント ID の委任権限で Graph を叩くため、テナントの同意状況によって 403 になり得ます。**403 を「異常なし」と読まないでください** — 侵害時に「本体」だけが静かに失敗し、露出が無期限に残ります。
>
> **PO への宿題**: 平時に 1 度だけ手順 2 を叩き、**返った HTTP コードをこの節に貼ってください**。実測が入ったらこの注記を消します。

**恒久的に露出を絞るなら**、復号専用の最小権限プリンシパルにするか、JIT 権限 / 復号ブローカーを入れる必要があります。どちらも [#302](https://github.com/yomote/mind-inbox/issues/302) 実装時の PO 裁定事項 (裁定記録 D5)。それまでは、**復号に使う資格情報が PO 個人のもの = サブスクリプション全体に届く**ことを承知のうえで使ってください。

### D. 鍵を作る / 作り直す — 管理系 RG の適用**前** (GPG)

鍵ペアは 2026-08-12 に作成済み。作り直すときは **PO の管理環境でのみ**実行します。

```bash
export GNUPGHOME="$HOME/.gnupg-e2e"; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
echo "allow-loopback-pinentry" > $GNUPGHOME/gpg-agent.conf

# 1. 鍵ペア (パスフレーズ無し — Key Vault に入れて機械が使うため)
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key "mind-inbox e2e artifacts <noreply@mind-inbox.invalid>" rsa4096 encr never

# 2. 公開鍵 → cicd/keys/e2e-artifacts.pub.asc として commit
gpg --armor --export "mind-inbox e2e artifacts" > cicd/keys/e2e-artifacts.pub.asc

# 3. 秘密鍵 → 管理系 RG の Key Vault へ (適用までは暫定で PO の手元)
#    az keyvault secret set --vault-name <Vault> --name e2e-artifact-private-key --value @-
gpg --armor --export-secret-keys "mind-inbox e2e artifacts"
```

CI 側の変更は要りません (秘密を持っていないため)。**古い鍵で暗号化済みの artifact は古い秘密鍵でしか開けない**ので、**保持期限 (14 日) が切れるまで旧秘密鍵を捨てないこと**。

### E. 鍵を替える — 管理系 RG の適用**後** (Key Vault の非エクスポート RSA 鍵オブジェクト)

鍵は Key Vault から出せないので、「作り直して差し替える」ではなく**新しいバージョンを足す**形になります。

1. **新バージョンを作る** — `az keyvault key rotate` (またはポリシー自動ローテーション)。**旧バージョンは無効化も削除もしない** — 保持中 (14 日) の artifact は旧バージョンで wrap されており、`az keyvault key decrypt` は wrap 時のバージョンを指定して呼ぶ必要がある (`.enc` にどのバージョンで wrap したかが入っている / 裁定記録 D9)
2. **`e2e-artifacts.pub.json` を「スクリプトで」作り直して commit する** — 手で 2 つのコマンド結果を貼り合わせない。**CI は Azure の資格情報を持たないので、バージョンはこのファイルからしか受け取れない** (裁定記録 D4)。

   **`publicKeyPem` と `keyVersion` が食い違うと、暗号化は成功して復号だけができない artifact ができます** — JSON の読み込みも公開鍵での wrap も通るので、成功パスは緑のまま。気づくのは復号を試みた時 (最大 14 日後)。1 ファイルにしても**この 2 つは独立に編集できる**ので、ファイルを分けないだけでは防げません。だから 2 つを契約にします:
   - **JSON 全体を 1 つの Azure 応答から生成する** — `az keyvault key show` の応答は `key.kid` (末尾がバージョン) と公開鍵の材料 (`key.n` / `key.e`) を**同時に**返す。ここから PEM を組み立てれば、バージョンと鍵が同じ応答に由来することが保証される。**`show` と `download` を別々に呼んで貼り合わせない** (その 2 回の間にローテーションが挟まりうるし、人が片方だけ更新できてしまう)
   - **commit 前に wrap → decrypt の往復を実測する** — 書いた `publicKeyPem` で試しに wrap し、書いた `keyVersion` を指定した `az keyvault key decrypt` で開いて、元に戻ることを確かめる。**これが通らない JSON は commit しない**

   **この更新を忘れると CI は旧バージョンで wrap し続けます** (壊れはしないが、ローテーションが効いていない)

3. **旧バージョンを消してよいのは、それで wrap された artifact が全部期限切れになってから**。消すと**その artifact は永久に開けません** (鍵が Key Vault の外に無い = 復旧手段が存在しない)

### F. 方式を GPG から封筒暗号へ切り替える

**`.gpg` → `.enc` の切り替えは、`encrypt-e2e-traces.sh` を呼ぶ workflow を 1 つでも取り残すと、その workflow の trace が無言で消えます** (裁定記録 D7)。upload の条件が `hashFiles(...*.gpg) != ''` なので、**スクリプトだけ `.enc` にすると条件が偽になり、ステップが「スキップ」として緑のまま通る**からです。

同じ PR で全部替えること:

| 替えるもの                                        | 場所                                                                                          |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| 出力拡張子と D7 の許可拡張子検査                  | `cicd/scripts/deploy/encrypt-e2e-traces.sh`                                                   |
| upload の `hashFiles(...)` 条件と `path:` の glob | **`encrypt-e2e-traces.sh` を呼ぶ全 workflow** (現在 `deploy.yml` / `golden-path-monitor.yml`) |
| `PUBKEY` (`*.pub.asc` → `*.pub.json`)             | 同じ全 workflow                                                                               |

**取り残しは機械が捕まえます** — `cicd/scripts/deploy/test_encrypt_e2e_traces.py` の `test_単体_スクリプトの許可拡張子と_workflow_の_glob_が一致する` が、スクリプトの許可拡張子と、スクリプトを呼ぶ全 workflow の glob 拡張子を突き合わせて落とします。**このテストが赤いまま「片方だけ替えた」で進まないこと。**

## Verification

| 何を確かめるか               | どう確かめるか                                                                             | 緑の条件                                                                                                      |
| ---------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------- |
| 鍵がエクスポート不可         | `az keyvault key show --vault-name <Vault> -n e2e-artifacts --query key.exportable -o tsv` | **`false`**                                                                                                   |
| 復号が通る (恒久方式)        | 手順 B を実際の `.enc` で 1 件通す                                                         | 平文の trace が開ける / **タグ検証失敗時は 1 バイトも出力せず非ゼロ終了**                                     |
| 往復 (GPG 方式)              | 暗号化 → 別キーリングで取り込み → 復号                                                     | **sha256 が元と一致** / 秘密鍵の無い環境では `decryption failed: No secret key` (2026-08-12 に実鍵で実測済み) |
| 方式切り替えの取り残しが無い | `npm run test:scripts`                                                                     | 拡張子の突き合わせテストが緑                                                                                  |
| 失効が届いた                 | 手順 C の 2 (または 3) の HTTP コード                                                      | **2xx**。403 で止めない (**未検証**: 手順 C の注記)                                                           |

## Rollback

- **GPG の鍵を戻す**: 旧 `e2e-artifacts.pub.asc` を commit し直す。**旧秘密鍵を捨てていなければ**過去の artifact は開ける
- **Key Vault の鍵バージョンを戻す**: 旧バージョンは消していない前提なので、`e2e-artifacts.pub.json` の `keyVersion` / `publicKeyPem` を旧バージョンのものに戻して commit する (手順 E の 2 と同じ契約を通す)
- **鍵そのものを失った場合、保持中の artifact は永久に開けません。** 恒久方式では鍵が Key Vault の外に無いので**復旧手段が存在しません** — これは受け入れた設計で、代償は「artifact の保持は 14 日」で限定しています

## Common Issues

### `gpg-agent: socket name '…' is too long` / `No agent running`

- 原因: `GNUPGHOME` のパスが長い (約 108 文字の制限)
- 対処: Prerequisites の「GNUPGHOME は短いパスにする」

### `decryption failed: No secret key`

- 原因: 秘密鍵を持たない環境で復号しようとした。**GPG 方式では正しい振る舞い**です (CI ランナーでこれが出るのは想定どおり)
- 対処: PO の管理環境で手順 A を実行する

### 恒久方式で `az keyvault key decrypt` が復号に失敗する

- 原因の第 1 候補: **バージョン違い**。版なしの鍵 URI を渡した / `.enc` に記録されたバージョンではなく最新版を指定した
- 対処: `.enc` に記録されたバージョンを `--version` で指定する (手順 B)

### `az rest ... revokeSignInSessions` が 403 を返す

- 原因: az CLI の同意済み委任権限にこのアクションぶんが含まれていない
- 対処: **止めずに** Entra 管理者経路 (`/users/{id}/revokeSignInSessions`) か管理センターの「セッションの取り消し」に切り替える (手順 C の 3)。**403 のまま放置すると露出が無期限に残ります**

### 公開鍵が無いのに deploy が緑

- 原因: **異常ではありません。** 公開鍵が無い間は trace を残さず `::warning::` を出して続行する設計 (裁定記録 D6 / fail closed)
- 対処: warning が出ていることを確認する。**沈黙していたらそちらが異常**です

## Related

- 鍵ファイルの置き場と commit 可否: [`cicd/keys/README.md`](../../cicd/keys/README.md)
- 管理系 RG の適用 (鍵の作成はここ): [`mgmt-layer-apply.md`](mgmt-layer-apply.md)
- device-code で Azure に入る: [`claude-web-azure-access.md`](claude-web-azure-access.md) / [ADR 0006](../adr/0006-azure-access-via-device-code.md)
- 落ちた run の artifact を取る: [`ops-inspect.md`](ops-inspect.md)
- 判断の記録 (**ADR ではありません**): [E2E 成果物は既定で秘密扱い](../adr/archive/operations/e2e-artifacts-are-secret-by-default.md)
- 層の定義: [ADR 0056](../adr/0056-management-and-app-layers-with-backup-based-data-protection.md) D1 (Proposed)
