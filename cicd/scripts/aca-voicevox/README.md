# VOICEVOX on Azure Container Apps (Serverless GPU)

ローカル Docker ではなく、Azure Container Apps の **Serverless GPU (T4)** で
VOICEVOX Engine を動かすための手順です。

> 補足:
> `cicd/iac/main-bootstrap.bicep` からも `enableVoicevoxAca=true` で同構成を作成できます。
> このスクリプトは、VOICEVOX 部分を単体で作成/更新したいとき用です。

## 前提

- `az` (Azure CLI) が利用可能
- Azure にログイン済み
- 対象サブスクリプションで **Container Apps GPU クォータ**が有効
  - 参考: [Container Apps serverless GPU 概要](https://learn.microsoft.com/azure/container-apps/gpu-serverless-overview)

## デプロイ

```bash
cd cicd
./scripts/aca-voicevox/deploy-voicevox-aca-gpu.sh
```

成功すると `VITE_VOICEVOX_BASE_URL` に設定すべき URL が表示されます。

## 主な環境変数

- `RG` (default: `rg-dev-mind-inbox`)
- `LOCATION` (default: `japaneast`)
- `CONTAINERAPPS_ENV` (default: `cae-dev-mindbox-voicevox`)
- `APP_NAME` (default: `ca-dev-mindbox-voicevox`)
- `VOICEVOX_IMAGE` (default: `voicevox/voicevox_engine:nvidia-latest`)
- `WORKLOAD_PROFILE_NAME` (default: `voicevox-gpu-t4`)
- `WORKLOAD_PROFILE_TYPE` (default: `Consumption-GPU-NC8as-T4`)
- `CPU` (default: `8.0`)
- `MEMORY` (default: `56.0Gi`)
- `MIN_REPLICAS` (default: `0`)
- `MAX_REPLICAS` (default: `1`)

### 例

```bash
cd cicd
RG=rg-dev-mind-inbox \
LOCATION=japaneast \
APP_NAME=ca-dev-mindbox-voicevox \
./scripts/aca-voicevox/deploy-voicevox-aca-gpu.sh
```

## フロントエンド接続

`frontend/.env.local` などに設定:

```bash
VITE_VOICEVOX_BASE_URL=https://<your-container-app-fqdn>
VITE_VOICEVOX_SPEAKER=3
```

> 注意:
>
> - 初回リクエストはコールドスタートで時間がかかる場合があります。
> - `MIN_REPLICAS=1` にすると待ち時間は減る一方、常時コストが発生します。

