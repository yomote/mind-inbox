// @vitest-environment jsdom
/**
 * [L1] 読み上げ (TTS) hook の振る舞い。
 *
 * この hook はブラウザ副作用の塊 (fetch / Audio 再生 / speechSynthesis / objectURL) で、
 * 壊れても「音が鳴らない」だけなので CI も E2E も緑のまま通る。実際 2026-08-08 に
 * 「VOICEVOX へ届かずブラウザ読み上げへ静かにフォールバックしていた」事故が出ている (#118)。
 * ここでは**フォールバックの分岐と後始末**を固定する。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/http", () => ({
  ttsFetch: vi.fn(),
}));

import { ttsFetch } from "../api/http";
import { useTextToSpeech } from "./useTextToSpeech";
import { resetUnlockedAudioForTest } from "./unlockedAudio";

/** speechSynthesis (ブラウザ内蔵読み上げ) は jsdom に無いので最小の偽物を置く。 */
function stubSpeechSynthesis() {
  const speak = vi.fn((utterance: { onend?: () => void }) => {
    utterance.onend?.();
  });
  vi.stubGlobal("speechSynthesis", { speak, cancel: vi.fn(), getVoices: () => [] });
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      text: string;
      lang = "";
      rate = 1;
      volume = 1;
      voice: unknown = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(text: string) {
        this.text = text;
      }
    },
  );
  return speak;
}

// jsdom の Blob は undici の Response と互換が無い (`stream()` を持たない) ため、
// ttsFetch の戻りは最小のスタブで表す (hook が見るのは status / ok / blob() だけ)。
const wavResponse = () =>
  ({
    status: 200,
    ok: true,
    blob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: "audio/wav" }),
  }) as unknown as Response;

const stubResponse = () => ({ status: 204, ok: true }) as unknown as Response;

let playSpy: ReturnType<typeof vi.spyOn>;
let revokeSpy: ReturnType<typeof vi.fn<(url: string) => void>>;

beforeEach(() => {
  vi.clearAllMocks();
  // 解錠済み要素はモジュールに残るので、テスト間で持ち越さない
  resetUnlockedAudioForTest();
  playSpy = vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  revokeSpy = vi.fn<(url: string) => void>();
  URL.createObjectURL = vi.fn(() => "blob:audio");
  URL.revokeObjectURL = revokeSpy;
});

afterEach(() => {
  vi.unstubAllGlobals();
  playSpy.mockRestore();
});

describe("[L1] useTextToSpeech — 合成経路の選択", () => {
  it("VOICEVOX が返れば音声を再生し、ブラウザ読み上げにフォールバックしない", async () => {
    // 無いと: BFF への結線が壊れてブラウザ読み上げに落ちても「喋ってはいる」ので
    //         VOICEVOX が死んだことに誰も気づかない (2026-08-08 の実事故と同型)
    const speak = stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      await result.current.speak("読み上げます");
    });

    expect(ttsFetch).toHaveBeenCalledWith("読み上げます", 3);
    expect(playSpy).toHaveBeenCalledTimes(1);
    expect(speak).not.toHaveBeenCalled();
  });

  it("stub (204) はブラウザ読み上げにフォールバックする", async () => {
    // 無いと: VOICEVOX 未設定の環境 (BFF が 204 を返す) で無音になり、
    //         「喋らないアプリ」として出荷される
    const speak = stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(stubResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      await result.current.speak("こんにちは");
    });

    expect(speak).toHaveBeenCalledTimes(1);
    expect(playSpy).not.toHaveBeenCalled();
  });

  it("standalone (mock デモ) は BFF を叩かずブラウザ読み上げに直行する", async () => {
    // 無いと: BFF の無い mock ビルドで /api/tts の失敗を待ってから喋る (体感が死ぬ)
    const speak = stubSpeechSynthesis();

    const { result } = renderHook(() => useTextToSpeech({ standalone: true, speaker: 3 }));
    await act(async () => {
      await result.current.speak("デモです");
    });

    expect(ttsFetch).not.toHaveBeenCalled();
    expect(speak).toHaveBeenCalledTimes(1);
  });

  it("同じ文言の 2 回目は合成し直さない (キャッシュ)", async () => {
    // 無いと: 同一文の再読み上げのたびに VOICEVOX を叩き、遅延と課金が増える
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      await result.current.speak("同じ文");
    });
    await act(async () => {
      await result.current.speak("同じ文");
    });

    expect(ttsFetch).toHaveBeenCalledTimes(1);
    expect(playSpy).toHaveBeenCalledTimes(2);
  });
});

describe("[L1] useTextToSpeech — ON/OFF と後始末", () => {
  it("読み上げ OFF では何も鳴らない", async () => {
    // 無いと: ユーザーが OFF にしたのに喋る (最も苦情になる壊れ方)
    const speak = stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    act(() => result.current.toggleEnabled());
    expect(result.current.enabled).toBe(false);

    await act(async () => {
      await result.current.speak("鳴ってはいけない");
    });

    expect(ttsFetch).not.toHaveBeenCalled();
    expect(speak).not.toHaveBeenCalled();
  });

  it("stop は再生を止めて objectURL を解放する", async () => {
    // 無いと: セッションを跨ぐたびに Blob URL が積もり、長時間利用でメモリを食う
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      await result.current.speak("再生中");
    });

    act(() => result.current.stop());

    expect(revokeSpy).toHaveBeenCalledWith("blob:audio");
    await waitFor(() => expect(result.current.speaking).toBe(false));
  });
});

describe("[L1] useTextToSpeech — 自動読み上げ (speakOnce)", () => {
  it("同じメッセージ id は二度読み上げない", async () => {
    // 無いと: 画面の再描画やストリーミング更新のたびに同じ返事を読み直す
    //         (伸びるバブルの度に読み上げが重なる)
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      result.current.speakOnce("m-1", "はじめまして");
      result.current.speakOnce("m-1", "はじめまして");
    });

    await waitFor(() => expect(vi.mocked(ttsFetch)).toHaveBeenCalledTimes(1));
  });

  it("OFF の間に届いたメッセージは既読にせず、ON に戻したら読む", async () => {
    // 無いと: 読み上げ OFF 中に届いた 1 件が「読み上げ済み」として消費され、
    //         ON に戻してもその返事だけ永久に読まれない (PR #152 レビュー minor)
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    act(() => result.current.toggleEnabled()); // OFF

    await act(async () => {
      result.current.speakOnce("m-1", "OFF 中に届いた返事");
    });
    expect(ttsFetch).not.toHaveBeenCalled();

    act(() => result.current.toggleEnabled()); // ON に戻す
    await act(async () => {
      result.current.speakOnce("m-1", "OFF 中に届いた返事");
    });

    await waitFor(() => expect(vi.mocked(ttsFetch)).toHaveBeenCalledTimes(1));
  });

  it("reset 後は同じ id をもう一度読み上げられる (ログアウト → 再ログイン)", async () => {
    // 無いと: ログアウトしても読み上げ済み id が残り、次のユーザーの最初の返事が読まれない
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      result.current.speakOnce("m-1", "一度目");
    });
    await waitFor(() => expect(vi.mocked(ttsFetch)).toHaveBeenCalledTimes(1));

    act(() => result.current.reset());
    await act(async () => {
      result.current.speakOnce("m-1", "一度目");
    });

    await waitFor(() => expect(vi.mocked(ttsFetch)).toHaveBeenCalledTimes(2));
  });
});

describe("[L1] useTextToSpeech — 劣化の可視化と再生解錠 (#150)", () => {
  it("VOICEVOX で鳴ったら outputMode は voicevox になる", async () => {
    // 無いと: 実際にどの声で鳴ったかを誰も観測できず、L4 の data-voice-output 検証が
    //         「常に緑」になって「WAV は 200 なのに別の声」を取り逃す
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      await result.current.speak("こんにちは");
    });

    await waitFor(() => expect(result.current.outputMode).toBe("voicevox"));
    expect(result.current.error).toBeNull();
  });

  it("再生をブラウザにブロックされたら、読み上げは続けたうえで劣化を画面に出す", async () => {
    // 無いと: 2026-08-08 の実事象そのもの —「無言で別の声に置き換わる」ため、
    //         ユーザーには「ずんだもんが黙った」としか見えず原因も追えない
    const speak = stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());
    playSpy.mockRejectedValue(new DOMException("blocked", "NotAllowedError"));

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      await result.current.speak("こんにちは");
    });

    // 音は出し続ける (無音にはしない) が、別の声になったことは必ず見せる
    await waitFor(() => expect(result.current.outputMode).toBe("browser-fallback"));
    expect(speak).toHaveBeenCalled();
    expect(result.current.error).toContain("ブロック");
  });

  it("合成に失敗したときは、再生ブロックとは別のメッセージを出す", async () => {
    // 無いと: 「合成が届いていない」のか「再生できていない」のか切り分けられず、
    //         再発時にどのホップを見ればよいか分からない
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue({ status: 502, ok: false } as unknown as Response);

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      await result.current.speak("こんにちは");
    });

    await waitFor(() => expect(result.current.outputMode).toBe("browser-fallback"));
    expect(result.current.error).toContain("合成できませんでした");
  });

  it("unlock は speechSynthesis だけでなく HTMLAudioElement も解錠する", async () => {
    // 無いと: ずんだもんの再生 (Audio 要素) が未解錠のまま残り、合成待ちの数秒で
    //         ジェスチャ文脈が切れるブラウザでは弾かれる (#150 の解錠の穴)
    const speak = stubSpeechSynthesis();

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      result.current.unlock();
    });

    expect(playSpy).toHaveBeenCalled(); // 無音 WAV を実際に再生して解錠している
    expect(speak).toHaveBeenCalled(); // ブラウザ読み上げ側の解錠も従来どおり行う
  });

  it("再生がブロックされた後は、次のタップで解錠をやり直せる", async () => {
    // 無いと: 「画面をタップすると、ずんだもんの声に戻ります」という案内が嘘になる
    //         (解錠済みフラグが立ったままで unlock が二度と実行されない)
    stubSpeechSynthesis();
    vi.mocked(ttsFetch).mockResolvedValue(wavResponse());
    playSpy.mockRejectedValue(new DOMException("blocked", "NotAllowedError"));

    const { result } = renderHook(() => useTextToSpeech({ standalone: false, speaker: 3 }));
    await act(async () => {
      result.current.unlock();
    });
    await act(async () => {
      await result.current.speak("こんにちは");
    });
    await waitFor(() => expect(result.current.outputMode).toBe("browser-fallback"));

    const callsBefore = playSpy.mock.calls.length;
    await act(async () => {
      result.current.unlock();
    });

    expect(playSpy.mock.calls.length).toBeGreaterThan(callsBefore);
  });
});
