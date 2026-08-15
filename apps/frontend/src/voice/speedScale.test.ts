// @vitest-environment jsdom
/**
 * [単体] 読み上げ速度の設定ストア (#242)。
 *
 * 無いと何が静かに通るか: 速度の設定は**壊れても音は鳴る**。保存が効かない (再訪で
 * 等倍に戻る) / 範囲外を黙って既定に倒す / 丸めをしないまま浮動小数を保存する、の
 * どれが起きても画面上はスライダーが動くだけで、誰も気づかないまま「設定しても
 * 変わらない」体験になる。ここで保存・正規化の 1 行を固定する。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  SPEED_SCALE_DEFAULT,
  SPEED_SCALE_MAX,
  SPEED_SCALE_MIN,
  getSpeedScale,
  normalizeSpeedScale,
  resetSpeedScaleForTest,
  setSpeedScale,
} from "./speedScale";

const STORAGE_KEY = "mind-inbox-tts-speed-scale";

beforeEach(() => {
  localStorage.clear();
  resetSpeedScaleForTest();
});

describe("[単体] normalizeSpeedScale", () => {
  it("範囲外はクランプする (既定に倒さない)", () => {
    // 無いと: 上限を下げた / 保存値を手で書き換えたときに「速くしたのに等倍」になり、
    //         設定が効いていないのか値が壊れているのか区別できない
    expect(normalizeSpeedScale(5)).toBe(SPEED_SCALE_MAX);
    expect(normalizeSpeedScale(0.1)).toBe(SPEED_SCALE_MIN);
  });

  it("数値として読めないものだけ既定に倒す", () => {
    expect(normalizeSpeedScale("なにこれ")).toBe(SPEED_SCALE_DEFAULT);
    expect(normalizeSpeedScale(undefined)).toBe(SPEED_SCALE_DEFAULT);
    expect(normalizeSpeedScale(Number.NaN)).toBe(SPEED_SCALE_DEFAULT);
  });

  it("保存された文字列を数値として読む", () => {
    expect(normalizeSpeedScale("1.25")).toBeCloseTo(1.25);
  });

  it("刻みに丸める (浮動小数の端数を保存しない)", () => {
    expect(normalizeSpeedScale(1.2345)).toBeCloseTo(1.25, 10);
  });
});

describe("[単体] 読み上げ速度の永続化", () => {
  it("設定した速度は localStorage に残り、次の読み込みで復元される", async () => {
    // 無いと: **Issue #242 の受け入れ条件そのもの** (再訪で保持される) が壊れても、
    //         その場ではスライダーが動くので気づけない
    setSpeedScale(1.4);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("1.4");

    // 「次の読み込み」= モジュールを読み直したときに保存値から始まること
    vi.resetModules();
    const reloaded = await import("./speedScale");
    expect(reloaded.getSpeedScale()).toBeCloseTo(1.4);
  });

  it("保存が無ければ等倍から始まる", () => {
    expect(getSpeedScale()).toBe(SPEED_SCALE_DEFAULT);
  });

  it("範囲外の値を設定してもクランプされた値が保存される", () => {
    // 無いと: 壊れた値が保存され、BFF が 400 を返して読み上げが丸ごと死ぬ
    //         (zod の範囲は 0.5〜2.0)
    setSpeedScale(99);
    expect(getSpeedScale()).toBe(SPEED_SCALE_MAX);
    expect(Number(localStorage.getItem(STORAGE_KEY))).toBe(SPEED_SCALE_MAX);
  });
});
