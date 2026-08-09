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
    /**
     * ファイルごとに環境とモジュールレジストリを分ける (#189)。
     *
     * 以前は起動コストを惜しんで `isolate: false` にしていたが、これはテストファイル間で
     * **モジュールレジストリを共有する**ため、`vi.mock` が「先に実モジュールを import した
     * 別ファイル」に負けて無効化される。実際に 6 回中 2〜3 回、実行順によって
     * `useConsultation` / `useTextToSpeech` / `useVoiceInput` が落ちていた
     * (例: `useTextToSpeech.test.tsx` が mock ではなく本物の `api/http` を掴み、
     * jsdom で相対 URL の fetch に行って失敗する)。
     *
     * 再実行で通ってしまうため、**本物の回帰が flaky に紛れて見逃される**方が高くつく。
     * なお惜しんでいた起動コストは実測では出ていない (14 ファイル / 105 テストで
     * 前後とも約 5.8s、8 回連続実行で 8/8 green)。速度と信頼性のトレードオフですらなかった。
     */
    isolate: true,
  },
});
