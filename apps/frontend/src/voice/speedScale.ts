/**
 * 読み上げ速度 (VOICEVOX の `speedScale` / #242) の設定ストア。
 *
 * **なぜ React state ではなくモジュールのストアなのか**: 速度を必要とするのは
 * 読み上げ hook (`useTextToSpeech`) だけではない。ストリーミング中の先行合成
 * (`api/consultation.ts` の prefetch) は React の外から `/api/tts` を叩くため、
 * props で配れない。ここで**速度を 1 箇所に持ち**、両方が同じ値を読むようにする。
 *
 * 値がズレると何が起きるか: BFF 側の合成キャッシュは速度込みのキーで引く。
 * prefetch が 1.0 で焼き、本合成が 1.4 で要求すると**全ミスして先行合成が無効化**される
 * (音は鳴るのでテストも E2E も緑のまま、体感だけ #185 以前に戻る)。
 *
 * 永続化はテーマ (`App.tsx`) と同じ localStorage。BFF には保存しない。
 */

import * as React from "react";

/** UI の下限。これ未満は聞き取りづらく、VOICEVOX engine 側の下限 (0.5) にも近い。 */
export const SPEED_SCALE_MIN = 0.75;
/** UI の上限。voicevox-wrapper の `speed_scale` が受け付ける上限 (2.0) と揃える。 */
export const SPEED_SCALE_MAX = 2.0;
/** スライダーの刻み。 */
export const SPEED_SCALE_STEP = 0.05;
/**
 * 既定値。等倍 = これまでと同じ音 (設定を触らないユーザーの体験を変えない)。
 * 「ちょっと遅い」(PO 2026-08-11) に既定で寄せるかは別途 PO 判断。
 */
export const SPEED_SCALE_DEFAULT = 1.0;

const STORAGE_KEY = "mind-inbox-tts-speed-scale";

/**
 * 保存値・入力値を安全な速度に正規化する。
 *
 * **範囲外を既定へ倒さずクランプする**のは、localStorage を手で書き換えた / 将来
 * 範囲を狭めた場合に「設定したのに黙って等倍に戻る」を作らないため。数値として
 * 読めないものだけ既定に倒す。
 */
export function normalizeSpeedScale(raw: unknown): number {
  const value = typeof raw === "string" ? Number(raw) : raw;
  if (typeof value !== "number" || !Number.isFinite(value)) return SPEED_SCALE_DEFAULT;
  const clamped = Math.min(SPEED_SCALE_MAX, Math.max(SPEED_SCALE_MIN, value));
  const snapped = Math.round(clamped / SPEED_SCALE_STEP) * SPEED_SCALE_STEP;
  // 小数第 2 位まで落とす。刻みの掛け算は 2 進小数の誤差を残す (0.05 * 28 = 1.4000000000000001)
  // ため、そのまま保存・送信すると localStorage と BFF に無意味な桁が乗り、
  // 速度ごとのキャッシュキーが同じ速度で 2 種類に割れる。
  return Math.round(snapped * 100) / 100;
}

/** 「1.20×」の表示。刻みが 0.05 なので小数 2 桁で足りる。 */
export function formatSpeedScale(value: number): string {
  return `${value.toFixed(2)}×`;
}

function readStored(): number {
  if (typeof localStorage === "undefined") return SPEED_SCALE_DEFAULT;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === null) return SPEED_SCALE_DEFAULT;
    return normalizeSpeedScale(saved);
  } catch {
    // Safari のプライベートモード等で localStorage が投げることがある。
    // **見えなくなるもの**: 「保存された速度」だけ。既定速度で読み上げは続く。
    return SPEED_SCALE_DEFAULT;
  }
}

let current = readStored();
const listeners = new Set<() => void>();

/** 現在の読み上げ速度。React の外 (api 層の先行合成) からも読む。 */
export function getSpeedScale(): number {
  return current;
}

/** 読み上げ速度を変更して永続化する。範囲外はクランプされる。 */
export function setSpeedScale(next: number): void {
  const normalized = normalizeSpeedScale(next);
  if (normalized === current) return;
  current = normalized;
  try {
    localStorage.setItem(STORAGE_KEY, String(normalized));
  } catch {
    // 保存できなくてもセッション中は効かせる (**見えなくなるもの**: 再訪時の保持のみ)。
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** テスト用: ストアと保存値を初期状態に戻す。 */
export function resetSpeedScaleForTest(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // テスト用途。localStorage が無い環境ではメモリ上の値だけ戻せばよい。
  }
  current = SPEED_SCALE_DEFAULT;
  listeners.forEach((listener) => listener());
}

/** 現在の読み上げ速度を購読する (設定画面での変更が読み上げ側へ即座に伝わる)。 */
export function useSpeedScale(): number {
  return React.useSyncExternalStore(subscribe, getSpeedScale, getSpeedScale);
}
