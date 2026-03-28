#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${VOICEVOX_CONTAINER_NAME:-voicevox-engine}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker が見つかりません。" >&2
  exit 1
fi

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  docker rm -f "${CONTAINER_NAME}" >/dev/null
  echo "VOICEVOXコンテナを停止しました: ${CONTAINER_NAME}"
else
  echo "対象コンテナは存在しません: ${CONTAINER_NAME}"
fi
