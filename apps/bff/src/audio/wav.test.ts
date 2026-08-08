/**
 * [L1] WAV パース / 結合の単体テスト。
 *
 * 無いと何が静かに通るか: ヘッダのサイズ欄やチャンク走査を壊しても、結合 WAV は
 * 「バイナリが返る」ため L2 以上では検知できず、ブラウザで無音・ノイズ・途中切れ
 * として初めて発覚する。
 */

import { describe, expect, it } from "vitest";
import { concatWavs, parseWav } from "./wav";

/** 16bit PCM mono の最小 WAV を組み立てる。 */
function makeWav(samples: number[], sampleRate = 24000): ArrayBuffer {
  const dataLength = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);
  const writeAscii = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) bytes[offset + i] = s.charCodeAt(i);
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataLength, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt チャンクサイズ
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, dataLength, true);
  samples.forEach((s, i) => view.setInt16(44 + i * 2, s, true));
  return buffer;
}

describe("[L1] parseWav", () => {
  it("fmt / data チャンクを取り出す", () => {
    const wav = makeWav([1, 2, 3]);
    const { fmt, data } = parseWav(wav);
    expect(fmt.length).toBe(16);
    expect(data.length).toBe(6);
  });

  it("RIFF でないバイナリは例外", () => {
    expect(() => parseWav(new ArrayBuffer(64))).toThrow(/RIFF/);
  });
});

describe("[L1] concatWavs", () => {
  it("data が入力順に連結され、サイズ欄が更新される", () => {
    const a = makeWav([1, 2]);
    const b = makeWav([3, 4, 5]);
    const merged = concatWavs([a, b]);

    const parsed = parseWav(merged);
    expect(parsed.data.length).toBe(10);
    const view = new DataView(merged);
    // RIFF サイズ = 全体 - 8
    expect(view.getUint32(4, true)).toBe(merged.byteLength - 8);
    // data の中身が a → b の順で並ぶ
    const samples: number[] = [];
    const dv = new DataView(
      parsed.data.buffer,
      parsed.data.byteOffset,
      parsed.data.byteLength,
    );
    for (let i = 0; i < parsed.data.length; i += 2) samples.push(dv.getInt16(i, true));
    expect(samples).toEqual([1, 2, 3, 4, 5]);
  });

  it("1 本だけならそのまま返す", () => {
    const a = makeWav([7]);
    expect(concatWavs([a])).toBe(a);
  });

  it("fmt (サンプルレート等) が違う WAV の結合は例外 — 無音/早回りとして静かに壊さない", () => {
    const a = makeWav([1], 24000);
    const b = makeWav([2], 44100);
    expect(() => concatWavs([a, b])).toThrow(/fmt/);
  });
});
