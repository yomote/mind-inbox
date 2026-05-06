import { defineConfig } from "vitest/config";

/**
 * Frontend 用 vitest 設定。
 *
 * - 環境は `node` (現在の L1 対象は mockApi.ts と純粋関数のみで DOM 不要)
 *   → component render が必要になったら `environment: "jsdom"` に切り替える
 * - test ファイルは `src/**\/*.test.ts(x)` のみ拾う (e2e は別)
 */
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    // BFF と並列実行されても起動コストを最小化
    isolate: false,
  },
});
