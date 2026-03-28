# Environment lifecycle scripts

このフォルダは、環境そのものの後片付け・クリーンアップ系スクリプトを配置します。

## Cleanup Environment

```bash
cd cicd
RG=<your-rg> ./scripts/env/cleanup-env.sh
```

- `main-config` / `main-bootstrap` の outputs から、自動作成した Entra アプリ登録を検出できた場合は先に削除します。
- 既存の手動管理 Entra アプリを残したい場合は `DELETE_ENTRA_APP=false` を付けてください。
- Key Vault は soft-delete のため、既定で purge まで実行します（`PURGE_DELETED_KEYVAULTS=true`）。
- purge を無効化したい場合は `PURGE_DELETED_KEYVAULTS=false` を付けてください。
