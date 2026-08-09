/**
 * [L2] TTS 分割合成サービスの service-level test (voicevoxClient は mock)。
 *
 * 無いと何が静かに通るか: 分割合成・文キャッシュ・フォールバックのどれが壊れても
 * /api/tts は「何かの WAV」を返し続けるため、レイテンシ退行 (全文一括に逆戻り) や
 * プリフェッチ無効化が緑のまま通る。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../clients/voicevoxClient", () => ({
  synthesize: vi.fn(),
  isConfigured: vi.fn(() => true),
}));

import { isConfigured, synthesize } from "../clients/voicevoxClient";
import { parseWav } from "../audio/wav";
import { planTts, prefetchTts, resetTtsCache, synthesizeTts } from "./ttsService";

/** テスト用の固定 fmt を持つ 16bit PCM WAV。data に marker を埋めて識別する。 */
function makeWav(marker: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + 2);
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);
  const writeAscii = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) bytes[offset + i] = s.charCodeAt(i);
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 38, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, 24000, true);
  view.setUint32(28, 48000, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, 2, true);
  view.setInt16(44, marker, true);
  return buffer;
}

const TWO_SENTENCES = "一つ目の文はこれです。二つ目の文はこちらです。";

beforeEach(() => {
  vi.clearAllMocks();
  resetTtsCache();
});

describe("[L2] synthesizeTts", () => {
  it("複数文は文単位に合成して 1 本の WAV に結合する", async () => {
    vi.mocked(synthesize).mockResolvedValueOnce(makeWav(1)).mockResolvedValueOnce(makeWav(2));

    const wav = await synthesizeTts({ text: TWO_SENTENCES, speakerId: 3 });

    expect(synthesize).toHaveBeenCalledTimes(2);
    expect(vi.mocked(synthesize).mock.calls.map(([req]) => req.text)).toEqual([
      "一つ目の文はこれです。",
      "二つ目の文はこちらです。",
    ]);
    const data = parseWav(wav as ArrayBuffer).data;
    expect(data.length).toBe(4); // 2 文分の data が結合されている
  });

  it("プリフェッチは確定文 (末尾以外) だけを合成する — 書きかけの末尾文は送らない", async () => {
    // 無いと: 書きかけの断片まで合成してしまい、最終合成でキャッシュに乗らない
    // (往復だけ増えて先行合成の意味が消える) 退行が静かに通る
    vi.mocked(synthesize).mockResolvedValue(makeWav(1));

    // ストリーミング途中の累積テキスト (末尾 "三つ目の文はまだ" は書きかけ)
    const result = await prefetchTts({
      text: "一つ目の文はこれです。二つ目の文はこちらです。三つ目の文はまだ",
      speakerId: 3,
    });

    expect(result).toEqual({ status: "cached", sentences: 2 });
    expect(vi.mocked(synthesize).mock.calls.map(([req]) => req.text)).toEqual([
      "一つ目の文はこれです。",
      "二つ目の文はこちらです。",
    ]);
  });

  it("確定文がまだ無い途中経過は合成しない (pending)", async () => {
    vi.mocked(synthesize).mockResolvedValue(makeWav(1));
    const result = await prefetchTts({ text: "まだ書きかけの", speakerId: 3 });
    expect(result).toEqual({ status: "pending", sentences: 0 });
    expect(synthesize).not.toHaveBeenCalled();
  });

  it("プリフェッチ済みの文は再合成しない (キャッシュヒット)", async () => {
    vi.mocked(synthesize).mockResolvedValue(makeWav(1));
    // 2 文を渡すと確定文は先頭 1 文 (末尾は書きかけ扱い) → 合成 1 回
    await prefetchTts({ text: TWO_SENTENCES, speakerId: 3 });
    expect(synthesize).toHaveBeenCalledTimes(1);
    expect(vi.mocked(synthesize).mock.calls[0][0].text).toBe("一つ目の文はこれです。");

    await synthesizeTts({ text: TWO_SENTENCES, speakerId: 3 });
    // 2 文中 1 文はキャッシュヒット → 追加の合成は 1 回だけ
    expect(synthesize).toHaveBeenCalledTimes(2);
    expect(vi.mocked(synthesize).mock.calls[1][0].text).toBe("二つ目の文はこちらです。");
  });

  it("話者が違えばキャッシュは別扱い", async () => {
    vi.mocked(synthesize).mockResolvedValue(makeWav(1));
    await prefetchTts({ text: TWO_SENTENCES, speakerId: 3 });
    await prefetchTts({ text: TWO_SENTENCES, speakerId: 8 });
    // 同じ先頭文でも話者が違えば合成し直す (話者混在の音声を返さない)
    expect(synthesize).toHaveBeenCalledTimes(2);
    expect(vi.mocked(synthesize).mock.calls.map(([req]) => req.speakerId)).toEqual([3, 8]);
  });

  it("VOICEVOX 未構成 (stub) は null を返す — /api/tts の 204 契約を保つ", async () => {
    vi.mocked(synthesize).mockResolvedValue(null);
    expect(await synthesizeTts({ text: TWO_SENTENCES, speakerId: 3 })).toBeNull();
  });

  it("分割合成の失敗は全文一括合成にフォールバックする", async () => {
    vi.mocked(synthesize)
      .mockRejectedValueOnce(new Error("engine busy"))
      .mockRejectedValueOnce(new Error("engine busy"))
      .mockResolvedValueOnce(makeWav(9));

    const wav = await synthesizeTts({ text: TWO_SENTENCES, speakerId: 3 });

    expect(wav).not.toBeNull();
    const lastCall = vi.mocked(synthesize).mock.calls.at(-1);
    expect(lastCall?.[0].text).toBe(TWO_SENTENCES); // 最後は全文で 1 回
  });

  it("1 文だけなら分割せずそのまま合成する", async () => {
    vi.mocked(synthesize).mockResolvedValue(makeWav(1));
    await synthesizeTts({ text: "短い一文です。", speakerId: 3 });
    expect(synthesize).toHaveBeenCalledTimes(1);
    expect(vi.mocked(synthesize).mock.calls[0][0].text).toBe("短い一文です。");
  });
});

describe("[L2] planTts (#185)", () => {
  it("合成せずに読み上げ単位の文だけを返す", async () => {
    // 無いと: フロントが「1 文目が焼けた時点で鳴らし始める」ための材料を失い、
    //         全文を結合してから返す旧経路 (= 最後の 1 文の合成完了まで無音) に戻る。
    vi.mocked(synthesize).mockResolvedValue(makeWav(1));

    const plan = planTts({ text: TWO_SENTENCES, speakerId: 3 });

    expect(plan).toEqual({
      status: "ok",
      sentences: ["一つ目の文はこれです。", "二つ目の文はこちらです。"],
    });
    // 分割を聞かれただけで合成まで走ると、プリフェッチと二重に VOICEVOX を叩く
    expect(synthesize).not.toHaveBeenCalled();
  });

  it("結合すると元のテキストに戻る (読み上げが欠けない)", () => {
    // 無いと: 分割で文字が落ちても「それらしく喋る」ので気づけない
    const plan = planTts({ text: TWO_SENTENCES });
    expect(plan.status === "ok" && plan.sentences.join("")).toBe(TWO_SENTENCES);
  });

  it("VOICEVOX 未構成なら stub を返す (フロントは合成を試さない)", () => {
    // 無いと: 未結線の環境で鳴らない合成を毎回叩き、その往復ぶん無言で待たされる
    vi.mocked(isConfigured).mockReturnValueOnce(false);
    expect(planTts({ text: TWO_SENTENCES })).toEqual({ status: "stub" });
  });
});
