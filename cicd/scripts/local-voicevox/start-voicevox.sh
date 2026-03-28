#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${VOICEVOX_CONTAINER_NAME:-voicevox-engine}"
HOST_PORT="${VOICEVOX_PORT:-50021}"
IMAGE="${VOICEVOX_IMAGE:-voicevox/voicevox_engine:cpu-latest}"
SPEAKER_ID="${VOICEVOX_SPEAKER:-3}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker が見つかりません。Dockerをインストールしてください。" >&2
  exit 1
fi

if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "VOICEVOXコンテナは既に起動中です: ${CONTAINER_NAME}"
else
  if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null
  fi

  echo "VOICEVOXコンテナを起動します..."
  docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${HOST_PORT}:50021" \
    "${IMAGE}" >/dev/null
fi

echo "VOICEVOXの起動待ち..."
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/version" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:${HOST_PORT}/version" >/dev/null 2>&1; then
  echo "VOICEVOXが起動しませんでした。コンテナログを確認してください。" >&2
  docker logs --tail 50 "${CONTAINER_NAME}" || true
  exit 1
fi

TMP_JSON="$(mktemp)"
trap 'rm -f "$TMP_JSON"' EXIT

# 初回合成を先に1回叩いてウォームアップ（初回レイテンシ短縮）
warmup_text="準備完了"
warmup_ok=0

for _ in $(seq 1 5); do
  if curl -fsS -X POST \
    --get \
    --data-urlencode "text=${warmup_text}" \
    --data-urlencode "speaker=${SPEAKER_ID}" \
    "http://127.0.0.1:${HOST_PORT}/audio_query" \
    -o "$TMP_JSON"; then
    if curl -fsS -X POST \
      -H 'Content-Type: application/json' \
      --data-binary @"$TMP_JSON" \
      "http://127.0.0.1:${HOST_PORT}/synthesis?speaker=${SPEAKER_ID}" \
      -o /dev/null; then
      warmup_ok=1
      break
    fi
  fi
  sleep 1
done

if [[ "$warmup_ok" -ne 1 ]]; then
  echo "WARN: ウォームアップ合成に失敗しました（実行は継続）。" >&2
fi

echo "VOICEVOX ready: http://127.0.0.1:${HOST_PORT}"
