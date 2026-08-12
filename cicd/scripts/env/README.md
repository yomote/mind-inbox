# Environment lifecycle scripts

このフォルダは、環境そのものの後片付け・クリーンアップ系スクリプトを配置します。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

リソースグループの削除に加えて、再 deploy 時に同名衝突を起こす **soft-delete 残骸** までまとめて完全クリーンアップします。

### 削除対象の流れ

1. 自動作成された Entra アプリ登録（`main-config` / `main-bootstrap` の outputs から検出した場合のみ）
2. Log Analytics workspace を `--force` で permanent delete（14 日間の soft-delete を回避）
3. リソースグループ本体
4. Soft-deleted Key Vault の purge
5. Soft-deleted Cognitive Services / Azure OpenAI account の purge

Key Vault と Cognitive Services は、RG が既に削除済みでも `list-deleted` をフォールバックとしてスキャンし、過去にこの RG に存在したものを拾って purge します。

### 主な環境変数

| 変数                               | 既定値              | 役割                                                |
| ---------------------------------- | ------------------- | --------------------------------------------------- |
| `RG`                               | `rg-dev-mind-inbox` | 対象リソースグループ                                |
| `DELETE_ENTRA_APP`                 | `true`              | 自動作成された Entra アプリを削除                   |
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

# 同じ名前で作り直したいので soft-delete の残骸を退ける
# （再 provision が「already exists in soft-deleted state」で失敗したときの手当て）
RG=rg-dev-mind-inbox \
  PURGE_DELETED_KEYVAULTS=true PURGE_DELETED_COGNITIVE_SERVICES=true \
  ./scripts/env/cleanup-env.sh

# ヘルプ
./scripts/env/cleanup-env.sh --help
```

### 注意

- `purge` 系は permanent delete です。誤って実行しないよう RG 名を必ず確認してください。
- Cognitive Services / OpenAI の purge にはサブスクリプションで `Microsoft.CognitiveServices/locations/deletedAccounts/delete` 権限が必要です（通常 Owner / Contributor で OK）。
- LA workspace の `--force` 削除は、再 deploy で同名 workspace を作る際の「soft-deleted state から復元するか？」プロンプトを回避するためのものです。
