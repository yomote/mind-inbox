# ローカル VOICEVOX サーバ起動

フロントエンドの音声合成（ずんだもん）用に、VOICEVOX Engine をローカルで起動します。

## 前提

- Docker が利用可能
- `curl` が利用可能

## 起動

```bash
./start-voicevox.sh
```

起動後の既定エンドポイント:

- [http://127.0.0.1:50021](http://127.0.0.1:50021)

このスクリプトは起動後に `audio_query` + `synthesis` を1回実行し、初回応答遅延を緩和します。

## 停止

```bash
./stop-voicevox.sh
```

## カスタム設定

環境変数で上書きできます。

- `VOICEVOX_CONTAINER_NAME`（既定: `voicevox-engine`）
- `VOICEVOX_PORT`（既定: `50021`）
- `VOICEVOX_IMAGE`（既定: `voicevox/voicevox_engine:cpu-latest`）
- `VOICEVOX_SPEAKER`（既定: `3` / ずんだもん）

例:

```bash
VOICEVOX_PORT=50025 ./start-voicevox.sh
```
