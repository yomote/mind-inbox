/**
 * Frontend の component テスト (L1) 共通セットアップ。
 *
 * vitest は `isolate: false` で走る (BFF と並列実行されても起動コストを最小化するため)。
 * つまり**同一ワーカー内では module registry と DOM が test ファイル間で共有される**。
 * PR #145 のレビューで order-dependent flaky を踏んだのと同じ土俵なので、
 * DOM 側は毎テスト後に必ず片付ける。
 *
 * - `cleanup()` — RTL がマウントしたツリーを unmount する。これが無いと前のテストの
 *   DOM が次のテストのクエリに引っかかり、「単体では通るがまとめて走ると落ちる」になる
 * - module state に依存するテストは各ファイル側で `vi.resetModules()` を呼ぶこと
 *   (ここでは呼ばない — 全ファイル一律の resetModules は import コストを毎回払うため)
 */

import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom が実装していない Web API のうち、コンポーネントが存在を前提に触るもの。
// **挙動は与えない** — 「無いとクラッシュする」を防ぐだけで、振る舞いのテストは
// 各テストが明示的に mock する (ここで賢くすると、テストが何を仮定しているか見えなくなる)。
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}
