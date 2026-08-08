#!/usr/bin/env bash
# Container App の image 差し替えにまつわる共通処理 (#107)。
# deploy-ai-agent.sh / deploy-voicevox-wrapper.sh から source して使う。
# 単体実行はしない（関数定義のみ）。

# 現在稼働している image と同一文字列で update しようとしていないかを検査して警告する。
#
# なぜ要るか: `az containerapp update --image <現在と同一の文字列>` は ARM 的にテンプレート
# 変更なし = 新しい revision を作らない no-op になる。:latest のような可変タグだと
# ghcr 側で実体が進んでいても Container Apps は据え置きのままで、しかもコマンドは成功する。
# 2026-08-07 に実際にこれで前日の image が動き続けた (#107)。
#
# 使い方: warn_if_noop_image_update "$RG" "$CA_NAME" "$IMAGE"
warn_if_noop_image_update() {
  local rg="$1" ca_name="$2" image="$3"
  local current_image
  current_image="$(az containerapp show -g "$rg" -n "$ca_name" \
    --query 'properties.template.containers[0].image' -o tsv 2>/dev/null || true)"
  [[ -z "$current_image" || "$current_image" != "$image" ]] && return 0

  echo "WARN: 現在の image と同一文字列 ($image) での update です。" >&2
  echo "      ARM 的に変更なし → 新 revision は作られません (no-op, #107)。" >&2
  echo "      ghcr 側でタグの実体が進んでいても反映されないため、IMAGE_TAG=sha-<full-sha> を指定してください。" >&2
  echo "      詳細: docs/runbooks/ghcr-images.md" >&2
}
