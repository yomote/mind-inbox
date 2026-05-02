# VOICEVOX Wrapper Service

VOICEVOX Engine を HTTP 経由で利用するための薄い API サービス。
このサービス自身は音声合成エンジンではなく、下流の VOICEVOX Engine を呼び出すラッパー。

## エンドポイント

| Method | Path           | 説明                                      |
| ------ | -------------- | ----------------------------------------- |
| GET    | `/health`      | ヘルスチェック（Engine の疎通確認を含む） |
| GET    | `/speakers`    | 話者一覧取得                              |
| POST   | `/audio-query` | 音声クエリ生成                            |
| POST   | `/synthesize`  | 音声合成（wav バイナリを返す）            |

---

## ローカル実行

### 前提

- Python 3.9+
- VOICEVOX Engine が別途起動していること

### VOICEVOX Engine の起動

```bash
docker run --rm -d -p 50021:50021 voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```

### セットアップ・起動

```bash
cd apps/services/voicevox
pip install -r requirements.txt

VOICEVOX_ENGINE_BASE_URL=http://localhost:50021 uvicorn app.main:app --reload --port 8080
```

### 動作確認

```bash
# ヘルスチェック（engine_reachable: true になることを確認）
curl http://localhost:8080/health

# 話者一覧
curl http://localhost:8080/speakers

# 音声クエリ
curl -X POST http://localhost:8080/audio-query \
  -H "Content-Type: application/json" \
  -d '{"text": "こんにちは", "speaker": 1}'

# 音声合成（wav ファイル出力）
curl -X POST http://localhost:8080/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "こんにちは", "speaker": 1, "speed_scale": 1.2}' \
  --output output.wav
```

---

## Azure Container Apps へのデプロイ

### 前提

- `az login` 済み
- VOICEVOX Engine の CA がデプロイ済みであること
- bootstrap で `enableAcr=true` / `enableVoicevoxWrapperAca=true` でデプロイ済み

```bash
# CAE と ACR を作成（未実施の場合）
az deployment group create \
  -g rg-dev-mind-inbox \
  -n main-bootstrap \
  -f cicd/modules/bootstrap-core.bicep \
  -p cicd/iac/main-bootstrap.parameters.json \
  -p enableAcr=true enableVoicevoxWrapperAca=true
```

### VOICEVOX Engine の URL を取得

```bash
# bootstrap output から取得できる場合
az deployment group show -g rg-dev-mind-inbox -n main-bootstrap \
  --query 'properties.outputs.voicevoxBaseUrl.value' -o tsv

# 取得できない場合（deploy-voicevox-aca-gpu.sh で直接デプロイした場合）は CA から直接取得
az containerapp list -g rg-dev-mind-inbox \
  --query '[].{name:name, fqdn:properties.configuration.ingress.fqdn}' -o table
```

### デプロイ

```bash
# Engine URL が bootstrap output に含まれている場合（自動取得）
RG=rg-dev-mind-inbox ./cicd/scripts/deploy/deploy-voicevox-wrapper.sh

# 手動で URL を渡す場合
VOICEVOX_ENGINE_BASE_URL=https://<engine-fqdn> \
RG=rg-dev-mind-inbox \
./cicd/scripts/deploy/deploy-voicevox-wrapper.sh
```

### 動作確認

```bash
# FQDN を取得
FQDN=$(az containerapp show -g rg-dev-mind-inbox \
  -n $(az deployment group show -g rg-dev-mind-inbox -n main-bootstrap \
       --query 'properties.outputs.voicevoxWrapperContainerAppName.value' -o tsv) \
  --query 'properties.configuration.ingress.fqdn' -o tsv)

# ヘルスチェック
curl https://${FQDN}/health

# 話者一覧
curl https://${FQDN}/speakers

# 音声合成
curl -X POST https://${FQDN}/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "こんにちは", "speaker": 1}' \
  --output output.wav
```

---

## 環境変数

| 変数名                     | 必須 | 説明                                 |
| -------------------------- | ---- | ------------------------------------ |
| `VOICEVOX_ENGINE_BASE_URL` | ✓    | VOICEVOX Engine のベース URL         |
| `PORT`                     | -    | リッスンポート（デフォルト: `8080`） |
