# Environment lifecycle scripts

このフォルダは、環境そのものの後片付け・クリーンアップ系スクリプトを配置します。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

**既定ではリソースグループを削除するだけ**で、soft-delete による救済は残します ([ADR 0046](../../../docs/adr/0046-environment-rebuildable-from-declaration.md) D5/D6)。再 deploy 時の同名衝突を退けたいときだけ、**衝突した種類の purge を明示的に有効化**します。

### 削除対象の流れ

| # | 対象 | 既定 | 有効化する変数 |
| --- | --- | --- | --- |
| 1 | 自動作成された Entra アプリ登録 | **しない** | `DELETE_ENTRA_APP=true` |
| 2 | Log Analytics workspace の permanent delete (14 日の soft-delete を回避) | **しない** | `FORCE_DELETE_LOG_ANALYTICS=true` |
| 3 | **リソースグループ本体** | **する** | — (常に実行) |
| 4 | Soft-deleted Key Vault の purge | **しない** | `PURGE_DELETED_KEYVAULTS=true` |
| 5 | Soft-deleted Cognitive Services / Azure OpenAI account の purge | **しない** | `PURGE_DELETED_COGNITIVE_SERVICES=true` |

purge を有効化した場合、Key Vault と Cognitive Services は RG が既に削除済みでも `list-deleted` をフォールバックとしてスキャンし、過去にこの RG に存在したものを拾って purge します。**つまり有効化した種類は soft-delete からも救えなくなる**ので、必要な種類だけを立ててください。

### 主な環境変数

| 変数                               | 既定値              | 役割                                                |
| ---------------------------------- | ------------------- | --------------------------------------------------- |
| `RG`                               | `rg-dev-mind-inbox` | 対象リソースグループ                                |
| `DELETE_ENTRA_APP`                 | `false`             | 自動作成された Entra アプリを削除                   |
| `FORCE_DELETE_LOG_ANALYTICS`       | `false`             | LA workspace を `--force` で即時削除                |
| `PURGE_DELETED_KEYVAULTS`          | `false`             | Key Vault の soft-delete を purge                   |
| `PURGE_DELETED_COGNITIVE_SERVICES` | `false`             | Cognitive Services / OpenAI の soft-delete を purge |
| `NO_WAIT`                          | `true`              | `az group delete --no-wait` で非同期削除            |
| `PURGE_WAIT_SECONDS`               | `1800`              | RG 削除や soft-delete 状態の最大待機秒              |

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
