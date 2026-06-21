# Claude Code (web) 開発セッションから Azure を操作する

## Trigger

Claude Code on the web の開発セッション内から、実 Azure リソースの状況確認や操作を
`az` で直接行いたいとき。無人の定期チェックではなく、**開発者が居るセッションでの対話的な利用**が対象。

> 無人・自動の運用保守エージェント (GitHub Actions + OIDC) を組む場合は別方針。
> 経緯は ADR [0006](../adr/0006-autonomous-ops-agent-via-github-oidc.md) と PR #29 (クローズ済) を参照。

## なぜこの方式か

- セッション内では Claude 自身が動いている = 頭脳は既に居るため、CI に別エージェントを立てる必要がない
  → `ANTHROPIC_API_KEY` も GitHub Actions も不要
- 認証は **device-code ログイン**: 対話型・静的シークレットなし・トークンはセッション限り (コンテナ破棄で消える)
- `az` は**自分の Azure 権限**でログインするので、専用サービスプリンシパルやロール設計が不要

## Prerequisites

- Claude Code on the web の**環境を編集できる**こと (Network access / Setup script を変更する)
- 操作対象サブスクリプションへの自分自身の Azure 権限 (見るだけなら Reader 相当)
- ブラウザ (device-code の承認に使う)

## Steps

### 1. ネットワーク egress を開ける (環境編集ダイアログ → Network access)

`az` のリソース操作はデフォルト Trusted では届かない (`management.azure.com` 等が allowlist 外)。

1. 環境を編集モードで開く (クラウドアイコンから。専用の Environments ページは無い)
2. **Network access** を **Custom** にする
3. **「Also include default list of common package managers」を ON** (npm/pip や packages.microsoft.com を残す)
4. **Allowed domains** に追記:

   ```text
   management.azure.com
   graph.microsoft.com
   ```

   - `login.microsoftonline.com` はデフォルトの `*.microsoftonline.com` でカバー済み → 追加不要
   - 横着するなら `*.azure.com` 1 行でも可 (広めなので非推奨)

### 2. Setup script に az インストールを仕込む (同じダイアログ → Setup script)

正本: [`cicd/scripts/cloud-env/setup-azure-cli.sh`](../../cicd/scripts/cloud-env/setup-azure-cli.sh)。
このファイルの中身を **Setup script 欄に貼り付ける**。

> Network access か Setup script を変更すると、次セッションで setup script が再実行され
> キャッシュが作り直される (docs の Environment caching)。1 回 az が入れば以降は高速。

### 3. 新しいセッションを開始

egress / setup script の変更は**起動時に効く**ため、現セッションには反映されない。新規セッションを開く。

### 4. ログインする (セッション内でオンデマンド)

Claude に依頼するか、自分で:

```bash
az login --use-device-code
# → 表示された URL を開き、コードを入力してブラウザで承認
```

### 5. 対象サブスクリプションを選ぶ (複数ある場合)

```bash
az account list -o table
az account set --subscription "<subscription-name-or-id>"
```

## Verification

実行後、次が通れば成功:

- [ ] `az version` が出る (setup script でのインストール成功)
- [ ] `az account show` で自分のアカウント/サブスクリプションが出る (ログイン成功)
- [ ] `az group show -n rg-dev-mind-inbox` または `az group list -o table` が返る (= egress と権限が両方OK)
- [ ] `az resource list -g rg-dev-mind-inbox -o table` でリソース一覧が見える

## Rollback

- 一時的に Azure 接続を切りたい: `az logout`
- 環境設定を戻す: 編集ダイアログで Network access を **Trusted** に戻す / Setup script 欄を空にする

## Common Issues

### `Host not in allowlist: management.azure.com ...`

- 原因: egress 許可リストに ARM ホストが無い (Step 1 未実施 or 旧セッションのまま)。
- 対処: Network access = Custom に `management.azure.com` を追加し、**新セッションを開き直す**。

### `az: command not found`

- 原因: setup script 未設定、または egress/script 変更後にセッションを開き直していない。
- 対処: Setup script 欄に `setup-azure-cli.sh` の内容を貼り、新セッションで再実行させる。

### device-code のコードを承認しても進まない

- 原因: `login.microsoftonline.com` への到達不可、または別アカウントでブラウザにログイン済み。
- 対処: 既定 Trusted で `*.microsoftonline.com` は通るはず。ブラウザのアカウントを確認。

## Related

- スクリプト正本: [`cicd/scripts/cloud-env/setup-azure-cli.sh`](../../cicd/scripts/cloud-env/setup-azure-cli.sh)
- ADR: [0006 運用保守エージェント (無人方式・今回は不採用)](../adr/0006-autonomous-ops-agent-via-github-oidc.md)
- docs: https://code.claude.com/docs/en/claude-code-on-the-web#network-access
