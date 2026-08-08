/**
 * warmup ping のトリガー (#120)。
 *
 * ホーム表示中に BFF /api/warmup を fire-and-forget で叩き、scale-to-zero
 * (ADR 0013) の ai-agent / vv-wrap をユーザーが対話を始める前に温めておく。
 *
 * - mock モードは BFF が無いので何もしない
 * - 連打・再訪問でのスパム防止に 60 秒スロットル (Container Apps のアイドル
 *   スケールダウンは分単位なので、これで十分温かさを維持できる)
 * - 認証 (ADR 0017): warmupFetch が Authorization を付ける。門の外から温める
 *   経路は作らない
 */

import { warmupFetch } from "./http";

const WARMUP_THROTTLE_MS = 60_000;

let lastRequestedAt = 0;

/** テスト用: スロットルをリセットする。 */
export function resetWarmupThrottle(): void {
  lastRequestedAt = 0;
}

/** ホーム表示時などに呼ぶ。多重・連続呼び出しはスロットルで無視される。 */
export function requestWarmup(): void {
  if (import.meta.env.VITE_USE_MOCK === "true") return;

  const now = Date.now();
  if (now - lastRequestedAt < WARMUP_THROTTLE_MS) return;
  lastRequestedAt = now;

  void warmupFetch()
    .then(async (res) => {
      if (!res.ok) return;
      const body: unknown = await res.json().catch(() => null);
      console.log("[warmup]", body);
    })
    .catch(() => {
      // 温め損ねても機能に影響はない (初回リクエストが遅いだけ)
    });
}
