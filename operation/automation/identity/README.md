# Entra ユーザー作成スクリプト

CSV から Entra ID のユーザー（`member` / `guest`）を作成し、必要ならグループに追加します。

## ファイル

- `create-users-from-csv.sh`
- `users.sample.csv`

## 前提

- Azure CLI ログイン済み
- 実行主体に必要な権限があること
  - `member` 作成: ユーザー作成権限
  - `guest` 招待: 外部招待権限
  - グループ追加: グループ管理権限

## 使い方

```bash
cd /path/to/repo
chmod +x ./operation/automation/identity/create-users-from-csv.sh
./operation/automation/identity/create-users-from-csv.sh --csv ./operation/automation/identity/users.sample.csv
```

### 便利オプション

```bash
./operation/automation/identity/create-users-from-csv.sh \
  --csv ./operation/automation/identity/users.sample.csv \
  --default-domain contoso.onmicrosoft.com \
  --invite-redirect-url https://myapplications.microsoft.com \
  --welcome-message false
```

## CSV 仕様

ヘッダー（固定）:

```csv
mode,displayName,userPrincipalName,email,mailNickname,password,groups
```

- `mode`: `member` または `guest`
- `displayName`: 表示名（必須）
- `userPrincipalName`: `member` の場合に必須（`--default-domain` 指定時は空可）
- `email`: `guest` の場合に必須
- `mailNickname`: 任意（空なら自動生成）
- `password`: `member` のみ任意（空なら自動生成）
- `groups`: 任意。`;` 区切りで複数指定。`displayName` または `objectId`

## 注意

- 既存ユーザーは再利用し、重複作成はしません（簡易な冪等動作）。
- CSV パーサーは簡易実装です。**フィールド内カンマは非対応**です。
- 失敗行がある場合、終了コードは `2` になります。
