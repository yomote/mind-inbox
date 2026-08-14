# Environment lifecycle scripts

このフォルダは、環境そのものの後片付け・クリーンアップ系スクリプトを配置します。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

**既定ではリソースグループを削除するだけ**で、soft-delete による救済は残します ([ADR 0046](../../../docs/adr/0046-environment-rebuildable-from-declaration.md) D5/D6)。再 deploy 時の同名衝突を退けたいときだけ、**衝突した種類の purge を明示的に有効化**します。

### 層ガード — 何も消さずに拒否する条件 ([ADR 0056](../../../docs/adr/0056-management-and-app-layers-with-backup-based-data-protection.md) / [#302](https://github.com/yomote/mind-inbox/issues/302))

**撤収の対象はアプリ系だけです。** 破壊系の処理に入る前に [`persistent_layer_guard.py`](persistent_layer_guard.py) が判定し、次のいずれかなら**1 つも消さずに exit 3** で止まります (ファイル名は初版の「持続層」という呼び方が残ったもので、中身は下の 2 種類を守るガードです)。

**止める理由は 2 種類あり、意味が違います。**

| 種類                    | 何を止めるか                                          | 性質                                                                         |
| ----------------------- | ----------------------------------------------------- | ---------------------------------------------------------------------------- |
| **管理系** (management) | Key Vault / バックアップ Storage / Log Analytics など | **恒久**。運用のためのものはアプリの生死と無関係                             |
| **復元未実証データ**    | Cosmos (ユーザーデータ)                               | **暫定**。バックアップ + 復元を 1 回通すまで (ADR 0046 D9 / ADR 0018) の措置 |

Cosmos が**アプリ系 RG に居るのは正しい姿**です ([#302](https://github.com/yomote/mind-inbox/issues/302) の PO 整理: Cosmos / OpenAI / Speech は「アプリそのもの」)。守り方は「別の RG へ逃がす」ではなく「**管理系 RG の非公開 Storage にバックアップして戻せるようにする**」。ただし「復元したことのないバックアップはバックアップではない」ので、往復を 1 回通すまでは撤収を止めます。**通したら、この一律拒否はバックアップ鮮度の確認に差し替えます** (差し替えないと週次プロビジョンテストが毎回 override を要求し、逃げ道が常用になってガードが死にます)。

**OpenAI / Speech は撤収を止めません。** アプリそのものでデータを持たないためですが、**黙って通しはしません** — 「再作成でクォータ / F0 枠を取り直せるかは未検証」を削除前に `#` 行で出します。

判定は**破壊系の 1 つ手前でそのつど取り直します** (Entra アプリ削除 / LA workspace の force-delete / RG 削除の各直前)。1 回で使い回すと、判定から削除までの間に RG の中身が変わっても気づけません。とくに「RG は不在」で通したあとに provision が RG を作り直した場合、**中身を一度も検証していない RG を消す**ことになります。

この「不在 → 再出現」の判定も [`persistent_layer_guard.py`](persistent_layer_guard.py) 側 (`decide`) にあります。シェルは**これまでの判定コードを溜めて `--previous-code` で渡すだけ**で、比較はしません — 遷移の `if` をシェルに置くと、壊してもテストが 1 つも落ちないためです。再出現と判定されたときのコードは `rg-reappeared-after-absent` で、**`ALLOW_PROTECTED_DELETE` でも通りません**。

| 状況                                                                                    | 判定コード                       | 挙動                                      | 逃げ道                                                 |
| --------------------------------------------------------------------------------------- | -------------------------------- | ----------------------------------------- | ------------------------------------------------------ |
| `RG` が管理系 RG (`MGMT_RG` / 既定 `rg-mgmt-mindbox`)                                   | `target-is-management-rg`        | **拒否**                                  | **無い** (どのフラグでも消せない — これが層分断の実体) |
| `RG` の中に管理系のリソースが居る (下記「何を管理系と見なすか」)                        | `management-resources-present`   | 拒否 (恒久)                               | `ALLOW_PROTECTED_DELETE=true`                          |
| `RG` の中に**復元を実証していないデータ**が居る (Cosmos)                                | `data-restore-unproven`          | 拒否 (**暫定** / ADR 0046 D9 まで)        | `ALLOW_PROTECTED_DELETE=true`                          |
| `RG` が**存在するかを確かめられなかった** (`az group exists` が失敗)                    | `rg-existence-unknown`           | 拒否 (「RG が無い」と読み替えない)        | `ALLOW_PROTECTED_DELETE=true`                          |
| `RG` の中身を**確かめられなかった** (未ログイン / 権限不足で `az resource list` が失敗) | `inventory-unavailable`          | 拒否 (「保護対象は無い」と読み替えない)   | `ALLOW_PROTECTED_DELETE=true`                          |
| `RG` が存在しない (確認は成功した)                                                      | `rg-absent`                      | 許可 (消すものが無い / 冪等)              | —                                                      |
| `RG` は不在だったのに、**破壊系の直前で再出現した** (別の provision が作った)           | `rg-reappeared-after-absent`     | 拒否 (中身を検証していない RG は消さない) | **無い** (最初からやり直す)                            |
| **soft-delete 済みの保護対象**を purge しようとしている (`PURGE_DELETED_*=true` のとき) | `protected-soft-deleted-present` | 拒否 (purge は復旧手段を恒久的に消す)     | `ALLOW_PROTECTED_DELETE=true`                          |
| soft-delete 一覧 (`list-deleted`) を**確かめられなかった**                              | `protected-soft-deleted-present` | 拒否 (「保護対象は無い」と読み替えない)   | `ALLOW_PROTECTED_DELETE=true`                          |

> **管理系とデータで判定コードを分けてあるのは飾りではありません。** 復元実証 (ADR 0046 D9) が済んだときに緩めてよいのは `data-restore-unproven` の側だけで、同じコードにすると「どちらを緩めるつもりだったか」がログからもコードからも読めなくなります。
>
> **soft-delete 済みの保護対象は `az resource list` に出ません。** live だけを見ていると判定は `ok` になり、`PURGE_DELETED_KEYVAULTS=true` などを立てた実行が **E2E trace 復号鍵やバックアップの復旧手段を恒久的に消します**。そこで **purge を有効にした種類だけ** `list-deleted` も判定材料に渡します (有効にしていない種類は触らないので渡さない = 無関係な soft-delete で撤収が止まらない)。この判定は **RG の存在とは独立**に効きます — purge は RG を消した後に走るので、`RG が存在しない` の下に置くと素通りするためです。

#### 何を管理系と見なすか (判定は 2 段)

**型だけでは層を区別できない。** Key Vault はアプリ系にも居る (`bootstrap-core.bicep` の SQL 管理者パスワード用 vault)、Storage も居る (Function App の実行 storage)、Log Analytics も居る (ops workspace)。型で一括りにすると**正当なアプリ系の撤収まで常に拒否**され、`ALLOW_PROTECTED_DELETE=true` が常用になってガードが意味を失う。逆に型から外すとバックアップ Storage と監査履歴が黙って消える。

| # | 根拠                                   | 効き方                                                                                        |
| - | -------------------------------------- | --------------------------------------------------------------------------------------------- |
| 1 | **層タグ** `mindInboxLayer=management` | `main-mgmt.bicep` が刻む。**型を問わず**管理系 (誤った RG へ mgmt を流した場合もここで捕まる) |
| 2 | **名指し** `PROTECTED_RESOURCE_NAMES`  | 層タグの無い移行前リソースを守る逃げ道 (空白区切り)                                           |

Key Vault / Storage / Log Analytics は 1 か 2 でだけ管理系になる。どれにも当たらなかったものは**アプリ系として通すが黙っては通さない** — 「何をアプリ系と見なしたか」を削除前に `#` 行で出す。

```
[layer-guard] OK (ok): rg-dev-mind-inbox に管理系 / 復元未実証データのリソースはありません。
  # Microsoft.KeyVault/vaults/kv-dev-mindbox-sql2 は層タグ (mindinboxlayer=management) も名指しも無いため**アプリ系**として扱った (…)
  # Microsoft.CognitiveServices/accounts/oai-dev-mindbox は撤収で消える — OpenAI / Speech は…クォータ / F0 枠を取り直せるかは未検証
```

> ⚠️ **復元実証が済むまで、`rg-dev-mind-inbox` の撤収は既定で拒否されます** (`data-restore-unproven`)。この RG に Cosmos が居るのは**正しい姿**ですが、バックアップからの復元をまだ 1 回も通していないためです ([#302](https://github.com/yomote/mind-inbox/issues/302) / ADR 0046 D9)。どうしても畳むなら `ALLOW_PROTECTED_DELETE=true` を明示してください — **消えたユーザーデータは戻りません。**

### 削除対象の流れ

| #   | 対象                                                                     | 既定       | 有効化する変数                          |
| --- | ------------------------------------------------------------------------ | ---------- | --------------------------------------- |
| 1   | 自動作成された Entra アプリ登録                                          | **しない** | `DELETE_ENTRA_APP=true`                 |
| 2   | Log Analytics workspace の permanent delete (14 日の soft-delete を回避) | **しない** | `FORCE_DELETE_LOG_ANALYTICS=true`       |
| 3   | **リソースグループ本体**                                                 | **する**   | — (常に実行)                            |
| 4   | Soft-deleted Key Vault の purge                                          | **しない** | `PURGE_DELETED_KEYVAULTS=true`          |
| 5   | Soft-deleted Cognitive Services / Azure OpenAI account の purge          | **しない** | `PURGE_DELETED_COGNITIVE_SERVICES=true` |

purge を有効化した場合、Key Vault と Cognitive Services は RG が既に削除済みでも `list-deleted` をフォールバックとしてスキャンし、過去にこの RG に存在したものを拾って purge します。**つまり有効化した種類は soft-delete からも救えなくなる**ので、必要な種類だけを立ててください。

### 主な環境変数

| 変数                               | 既定値              | 役割                                                      |
| ---------------------------------- | ------------------- | --------------------------------------------------------- |
| `RG`                               | `rg-dev-mind-inbox` | 対象リソースグループ                                      |
| `DELETE_ENTRA_APP`                 | `false`             | 自動作成された Entra アプリを削除                         |
| `FORCE_DELETE_LOG_ANALYTICS`       | `false`             | LA workspace を `--force` で即時削除                      |
| `PURGE_DELETED_KEYVAULTS`          | `false`             | Key Vault の soft-delete を purge                         |
| `PURGE_DELETED_COGNITIVE_SERVICES` | `false`             | Cognitive Services / OpenAI の soft-delete を purge       |
| `NO_WAIT`                          | `true`              | `az group delete --no-wait` で非同期削除                  |
| `PURGE_WAIT_SECONDS`               | `1800`              | RG 削除や soft-delete 状態の最大待機秒                    |
| `MGMT_RG`                          | `rg-mgmt-mindbox`   | 管理系 RG。**この RG は撤収できない**                     |
| `PROTECTED_RESOURCE_NAMES`         | (空)                | 層タグの無いリソースを名指しで管理系扱いする (空白区切り) |
| `ALLOW_PROTECTED_DELETE`           | `false`             | 保護対象が居ても撤収を続行する (不可逆)                   |

> **破壊系の既定は off** ([ADR 0046](../../../docs/adr/0046-environment-rebuildable-from-declaration.md) D5/D6)。
> purge は **soft-delete という唯一の復旧手段を消す**ので、明示的に頼まれた時だけ行う。
> `DELETE_ENTRA_APP` も既定 off — アプリ登録は RG ではなく**テナントのオブジェクト**で、
> RG の撤収が持ち主ではない。

### 例

```bash
# 既定（RG は消すが、soft-delete による救済は残す）
RG=rg-dev-mind-inbox ./scripts/env/cleanup-env.sh

# 再 provision が名前衝突で失敗したときの手当て。
# ★ 衝突した種類のフラグ「だけ」を立てること — 巻き添えで別の種類まで purge すると、
#   衝突解消に不要な soft-delete の救済まで永久に失う
#
# OpenAI / Speech (Cognitive Services) が衝突した場合:
RG=rg-dev-mind-inbox PURGE_DELETED_COGNITIVE_SERVICES=true ./scripts/env/cleanup-env.sh

# Key Vault が衝突した場合:
RG=rg-dev-mind-inbox PURGE_DELETED_KEYVAULTS=true ./scripts/env/cleanup-env.sh

# ヘルプ
./scripts/env/cleanup-env.sh --help
```

### 注意

- `purge` 系は permanent delete です。誤って実行しないよう RG 名を必ず確認してください。
- Cognitive Services / OpenAI の purge にはサブスクリプションで `Microsoft.CognitiveServices/locations/deletedAccounts/delete` 権限が必要です（通常 Owner / Contributor で OK）。
- LA workspace の `--force` 削除は、再 deploy で同名 workspace を作る際の「soft-deleted state から復元するか？」プロンプトを回避するためのものです。
