# Environment lifecycle scripts

このフォルダは、環境そのものの後片付け・クリーンアップ系スクリプトを配置します。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

**既定ではリソースグループを削除するだけ**で、soft-delete による救済は残します ([ADR 0046](../../../docs/adr/0046-environment-rebuildable-from-declaration.md) D5/D6)。再 deploy 時の同名衝突を退けたいときだけ、**衝突した種類の purge を明示的に有効化**します。

### 持続層ガード — 何も消さずに拒否する条件 ([ADR 0046](../../../docs/adr/0046-environment-rebuildable-from-declaration.md) D1 / [#302](https://github.com/yomote/mind-inbox/issues/302))

**撤収の対象は環境層だけです。** 破壊系の処理に入る前に [`persistent_layer_guard.py`](persistent_layer_guard.py) が判定し、次のいずれかなら**1 つも消さずに exit 3** で止まります。

判定は**破壊系の 1 つ手前でそのつど取り直します** (Entra アプリ削除 / LA workspace の force-delete / RG 削除の各直前)。1 回で使い回すと、判定から削除までの間に RG の中身が変わっても気づけません。とくに「RG は不在」で通したあとに provision が RG を作り直した場合、**中身を一度も検証していない RG を消す**ことになります。この経路は不在判定を覚えておいて別扱いで拒否します。

| 状況                                                                                    | 挙動                                      | 逃げ道                                                 |
| --------------------------------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------ |
| `RG` が持続層 RG (`PERSISTENT_RG` / 既定 `rg-shared-mindbox`)                           | **拒否**                                  | **無い** (どのフラグでも消せない — これが層分断の実体) |
| `RG` の中に持続層のリソースが居る (下記「何を持続層と見なすか」)                        | 拒否                                      | `ALLOW_PERSISTENT_DELETE=true`                         |
| `RG` が**存在するかを確かめられなかった** (`az group exists` が失敗)                    | 拒否 (「RG が無い」と読み替えない)        | `ALLOW_PERSISTENT_DELETE=true`                         |
| `RG` の中身を**確かめられなかった** (未ログイン / 権限不足で `az resource list` が失敗) | 拒否 (「持続層は無い」と読み替えない)     | `ALLOW_PERSISTENT_DELETE=true`                         |
| `RG` が存在しない (確認は成功した)                                                      | 許可 (消すものが無い / 冪等)              | —                                                      |
| `RG` は不在だったのに、**破壊系の直前で再出現した** (別の provision が作った)           | 拒否 (中身を検証していない RG は消さない) | **無い** (最初からやり直す)                            |
| **soft-delete 済みの持続層**を purge しようとしている (`PURGE_DELETED_*=true` のとき)   | 拒否 (purge は復旧手段を恒久的に消す)     | `ALLOW_PERSISTENT_DELETE=true`                         |
| soft-delete 一覧 (`list-deleted`) を**確かめられなかった**                              | 拒否 (「持続層は無い」と読み替えない)     | `ALLOW_PERSISTENT_DELETE=true`                         |

> **soft-delete 済みの持続層は `az resource list` に出ません。** live だけを見ていると判定は `ok` になり、`PURGE_DELETED_COGNITIVE_SERVICES=true` などを立てた実行が **Cosmos の復元元や OpenAI の復旧手段を恒久的に消します**。そこで **purge を有効にした種類だけ** `list-deleted` も判定材料に渡します (有効にしていない種類は触らないので渡さない = 無関係な soft-delete で撤収が止まらない)。この判定は **RG の存在とは独立**に効きます — purge は RG を消した後に走るので、`RG が存在しない` の下に置くと素通りするためです。

#### 何を持続層と見なすか (判定は 3 段)

**型だけでは層を区別できない。** Key Vault は環境層にも居る (`bootstrap-core.bicep` の SQL 管理者パスワード用 vault)、Storage も居る (Function App の実行 storage)、Log Analytics も居る (ops workspace)。型で一括りにすると**正当な環境層の撤収まで常に拒否**され、`ALLOW_PERSISTENT_DELETE=true` が常用になってガードが意味を失う。逆に型から外すとバックアップ Storage と監査履歴が黙って消える。

| #   | 根拠                                   | 効き方                                                                                            |
| --- | -------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 1   | **層タグ** `mindInboxLayer=persistent` | `main-shared.bicep` が刻む。**型を問わず**持続層 (誤った RG へ shared を流した場合もここで捕まる) |
| 2   | **名指し** `PERSISTENT_RESOURCE_NAMES` | 層タグの無い移行前リソースを守る逃げ道 (空白区切り)                                               |
| 3   | **型** Cosmos DB / Cognitive Services  | 環境層に「使い捨ての同型」が無い型だけ                                                            |

Key Vault / Storage / Log Analytics は 1 か 2 でだけ持続層になる。どれにも当たらなかったものは**環境層として通すが黙っては通さない** — 「何を環境層と見なしたか」を削除前に `#` 行で出す。

```
[persistent-layer-guard] OK (ok): rg-dev-mind-inbox に持続層のリソースはありません。
  # Microsoft.KeyVault/vaults/kv-dev-mindbox-sql2 は層タグ (mindinboxlayer=persistent) も名指しも無いため**環境層**として扱った (…)
```

> ⚠️ **移行が済むまで、`rg-dev-mind-inbox` の撤収は既定で拒否されます。** 現在この RG には Cosmos (ユーザーデータ) と OpenAI / Speech が同居しているためで、これは意図した振る舞いです ([#302](https://github.com/yomote/mind-inbox/issues/302))。どうしても畳むなら `ALLOW_PERSISTENT_DELETE=true` を明示してください — **消えたユーザーデータは戻りません。**

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
| `PERSISTENT_RG`                    | `rg-shared-mindbox` | 持続層 RG。**この RG は撤収できない**                     |
| `PERSISTENT_RESOURCE_NAMES`        | (空)                | 層タグの無いリソースを名指しで持続層扱いする (空白区切り) |
| `ALLOW_PERSISTENT_DELETE`          | `false`             | 持続層のリソースが居ても撤収を続行する (不可逆)           |

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
