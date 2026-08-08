import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vitest/config";

/**
 * Frontend 用 vitest 設定。**環境をファイル拡張子で分ける。**
 *
 * - `*.test.ts`  → `node`  : 純粋ロジック (mockApi / stitcher / azureSpeech) と、
 *   ソースを `readFileSync` で読む静的検査 (auth/msal.test.ts)。
 *   **jsdom にすると `import.meta.url` が file: でなくなり後者が壊れる** — 全体を
 *   jsdom へ切り替えて実際に 2 ファイル落とした (2026-08-08)。だから一律にしない
 * - `*.test.tsx` → `jsdom` : component render を含む L1。
 *   briefing #2 の PO 決定「Phase 3 はテストファースト」の前提 (epic #135)
 *
 * どちらも `isolate: false` (BFF と並列実行されても起動コストを最小化)。
 * その代償として module registry と DOM が test ファイル間で共有されるので、
 * DOM 側は `setupFiles` の `cleanup()` で毎テスト後に片付ける。
 */
export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: "node",
          environment: "node",
          include: ["src/**/*.test.ts"],
          isolate: false,
        },
      },
      {
        // JSX を含む .tsx を変換するために必要 (アプリ本体と同じ SWC プラグイン)
        plugins: [react()],
        test: {
          name: "dom",
          environment: "jsdom",
          include: ["src/**/*.test.tsx"],
          setupFiles: ["./src/test/setup.ts"],
          isolate: false,
        },
      },
    ],
  },
});
