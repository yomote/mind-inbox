// @vitest-environment jsdom
/**
 * [L1] useVoiceInput — マイクを**ユーザージェスチャの同期処理の中で開く** (#186)。
 *
 * 無いと何が静かに通るか: start() に await を 1 つ挟むだけで iOS Safari は
 * (許可済みでも) マイクを実質開かなくなり、「押しても無反応」に戻る。しかし
 * デスクトップ Chromium ではジェスチャ要件が緩いため**普通に動いてしまい**、
 * 手元の確認でも E2E でも緑のまま通る。この非対称性こそが #186 を生んだので、
 * 「同期で開いたか」をここで機械的に固定する。
 *
 * SessionComposer.test.tsx は useVoiceInput をまるごとモックしているため、
 * この分岐の実ロジックはそちらでは 1 行も通らない (PR #190 レビュー指摘)。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./azureSpeech", () => ({
  peekAzurePreparation: vi.fn(() => null),
  prepareAzureRecognition: vi.fn(async () => ({ kind: "unavailable", expiresAt: Date.now() })),
  startPreparedAzureRecognition: vi.fn(() => ({ stop: vi.fn() })),
  tryStartAzureRecognition: vi.fn(async () => ({ kind: "unavailable" })),
}));

import {
  peekAzurePreparation,
  prepareAzureRecognition,
  startPreparedAzureRecognition,
} from "./azureSpeech";
import { useVoiceInput } from "./useVoiceInput";

/** Web Speech API の最小の偽物 (jsdom には無い)。 */
class FakeRecognition {
  static instances: FakeRecognition[] = [];
  lang = "";
  continuous = false;
  interimResults = false;
  maxAlternatives = 0;
  onstart: (() => void) | null = null;
  onresult: ((e: unknown) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn(() => this.onstart?.());
  stop = vi.fn();
  constructor() {
    FakeRecognition.instances.push(this);
  }
}

function installSpeechRecognition() {
  FakeRecognition.instances = [];
  vi.stubGlobal("webkitSpeechRecognition", FakeRecognition);
}

const readyPreparation = { kind: "ready", expiresAt: Date.now() + 60_000 };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(peekAzurePreparation).mockReturnValue(null);
  vi.mocked(prepareAzureRecognition).mockResolvedValue({
    kind: "unavailable",
    expiresAt: Date.now(),
  } as never);
  vi.mocked(startPreparedAzureRecognition).mockReturnValue({ stop: vi.fn() });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("[L1] useVoiceInput — タップの同期処理でマイクを開く (#186)", () => {
  it("準備済みの高精度認識は、toggle の同期処理の中で開始される", () => {
    // 無いと: トークン取得や SDK ロードを await してから開く実装に戻り、iOS で無反応になる
    installSpeechRecognition();
    vi.mocked(peekAzurePreparation).mockReturnValue(readyPreparation as never);

    const { result } = renderHook(() => useVoiceInput(vi.fn()));

    let openedSynchronously = false;
    act(() => {
      result.current.toggle();
      // ここは toggle と同じ同期処理の中。await を挟む実装なら未呼び出しになる。
      openedSynchronously = vi.mocked(startPreparedAzureRecognition).mock.calls.length === 1;
    });

    expect(openedSynchronously).toBe(true);
    expect(result.current.phase).toBe("listening");
    expect(result.current.engine).toBe("azure");
  });

  it("準備が間に合っていなければ、待たずにブラウザ認識を同期で開始する", () => {
    // 無いと: 「準備できるまで待つ」実装になり、初回タップが必ず無反応になる
    installSpeechRecognition();
    vi.mocked(peekAzurePreparation).mockReturnValue(null);

    const { result } = renderHook(() => useVoiceInput(vi.fn()));

    let startedSynchronously = false;
    act(() => {
      result.current.toggle();
      startedSynchronously = FakeRecognition.instances[0]?.start.mock.calls.length === 1;
    });

    expect(startedSynchronously).toBe(true);
    expect(result.current.engine).toBe("browser");
    expect(result.current.phase).toBe("listening");
  });

  it("ブラウザ認識で開始したら、次のタップに備えて裏で準備を進める", () => {
    // 無いと: 一度ブラウザ認識に落ちるとセッション中ずっと高精度認識に上がれない
    installSpeechRecognition();
    vi.mocked(peekAzurePreparation).mockReturnValue(null);

    const { result } = renderHook(() => useVoiceInput(vi.fn()));
    vi.mocked(prepareAzureRecognition).mockClear();
    act(() => result.current.toggle());

    expect(prepareAzureRecognition).toHaveBeenCalled();
  });

  it("準備済みでも開始に失敗したら、劣化を見せたうえでブラウザ認識で続行する", () => {
    // 無いと: 高精度認識が使えなくなっても「動いてはいる」ので品質劣化に誰も気づけない
    installSpeechRecognition();
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(peekAzurePreparation).mockReturnValue(readyPreparation as never);
    vi.mocked(startPreparedAzureRecognition).mockImplementation(() => {
      throw new Error("mic busy");
    });

    const { result } = renderHook(() => useVoiceInput(vi.fn()));
    act(() => result.current.toggle());

    expect(result.current.engine).toBe("browser");
    expect(result.current.degraded).toBe(true);
    expect(result.current.listening).toBe(true);
  });
});

describe("[L1] useVoiceInput — 使えるエンジンの有無と停止", () => {
  it("開始できるエンジンが 1 つも無ければ supported=false (押させない)", async () => {
    // 無いと: 押して初めて「対応していません」と出る作りに戻る (#186)。
    //         旧判定は `|| !useMock` で、BFF さえあれば有効にしてしまっていた。
    const { result } = renderHook(() => useVoiceInput(vi.fn()));
    await waitFor(() => expect(prepareAzureRecognition).toHaveBeenCalled());

    expect(result.current.supported).toBe(false);
  });

  it("高精度認識が使えるなら、Web Speech 非対応ブラウザでも supported=true", async () => {
    // 無いと: iOS の一部構成でボタンを不必要に殺す
    vi.mocked(prepareAzureRecognition).mockResolvedValue(readyPreparation as never);

    const { result } = renderHook(() => useVoiceInput(vi.fn()));

    await waitFor(() => expect(result.current.supported).toBe(true));
  });

  it("停止すると認識を止めて idle に戻る", () => {
    // 無いと: 「音声入力停止」を押しても裏で聞き続ける (最も不信を招く壊れ方)
    installSpeechRecognition();
    const { result } = renderHook(() => useVoiceInput(vi.fn()));

    act(() => result.current.toggle());
    expect(result.current.listening).toBe(true);

    act(() => result.current.stop());

    expect(FakeRecognition.instances[0].stop).toHaveBeenCalled();
    expect(result.current.listening).toBe(false);
    expect(result.current.phase).toBe("idle");
    expect(result.current.engine).toBeNull();
  });

  it("確定した認識テキストを呼び出し元へ渡す", () => {
    // 無いと: 認識はできているのに入力欄に何も入らない、という壊れ方を拾えない
    installSpeechRecognition();
    const append = vi.fn();
    const { result } = renderHook(() => useVoiceInput(append));

    act(() => result.current.toggle());
    act(() => {
      FakeRecognition.instances[0].onresult?.({
        resultIndex: 0,
        results: [{ 0: { transcript: "ねむれない" }, isFinal: true }],
      });
    });

    expect(append).toHaveBeenCalledWith("ねむれない");
  });
});

/**
 * TTS 再生中のエコー破棄 (#228)。純ロジック (echoGate) は echoGate.test.ts が担当。
 * ここでは **hook が門を実際に配線しているか** (ttsPlaying prop → 結果破棄) を固定する。
 */
describe("[L2] useVoiceInput — TTS 再生中は認識結果を入力欄へ流さない (#228)", () => {
  /** ttsPlaying を後から切り替えられる形で hook を張る。 */
  function renderVoice(append: (text: string) => void) {
    return renderHook(({ ttsPlaying }) => useVoiceInput(append, { ttsPlaying }), {
      initialProps: { ttsPlaying: false },
    });
  }

  function fireResult(text: string, isFinal: boolean) {
    FakeRecognition.instances[0].onresult?.({
      resultIndex: 0,
      results: [{ 0: { transcript: text }, isFinal }],
    });
  }

  it("再生中に確定した認識結果は入力欄へ流れず、muted が立つ", () => {
    // 無いと: スピーカーから出たずんだもんの声がユーザー発話として入力欄に入る
    //         (#228 の実事象)。認識も再生も個々のテストは緑のまま通る。
    installSpeechRecognition();
    const append = vi.fn();
    const { result, rerender } = renderVoice(append);

    act(() => result.current.toggle());
    rerender({ ttsPlaying: true });
    expect(result.current.muted).toBe(true);

    act(() => fireResult("ぼくはずんだもんなのだ", true));

    expect(append).not.toHaveBeenCalled();
    expect(result.current.interimTranscript).toBe("");
  });

  it("再生が終わると猶予明けから自動で認識結果が流れる (ユーザーの再操作は不要)", () => {
    // 無いと: 一度読み上げが走ると音声入力が二度と効かず、手で入れ直す羽目になる。
    // 猶予 (POST_PLAYBACK_GRACE_MS) 内はエコーの尻尾でありうるため final-only でも
    // 破棄される — 通るのは猶予明けから (echoGate.test.ts が境界を固定)
    installSpeechRecognition();
    const nowSpy = vi.spyOn(Date, "now");
    try {
      nowSpy.mockReturnValue(0);
      const append = vi.fn();
      const { result, rerender } = renderVoice(append);

      act(() => result.current.toggle());
      rerender({ ttsPlaying: true });
      act(() => fireResult("ぼくはずんだもんなのだ", true));
      nowSpy.mockReturnValue(1000);
      rerender({ ttsPlaying: false });
      expect(result.current.muted).toBe(false);

      nowSpy.mockReturnValue(10_000); // 猶予明け
      act(() => fireResult("ユーザーの発話", true));

      expect(append).toHaveBeenCalledTimes(1);
      expect(append).toHaveBeenCalledWith("ユーザーの発話");
    } finally {
      nowSpy.mockRestore();
    }
  });

  it("再生中に聞こえた発話が再生終了直後に確定しても破棄される (尻尾の防御)", () => {
    // 無いと: 認識エンジンの final 化は再生停止より遅れて届くため、
    //         ずんだもんの最後の 1 文だけが毎回入力欄に入る
    installSpeechRecognition();
    const nowSpy = vi.spyOn(Date, "now");
    try {
      nowSpy.mockReturnValue(0);
      const append = vi.fn();
      const { result, rerender } = renderVoice(append);

      act(() => result.current.toggle());
      rerender({ ttsPlaying: true });
      act(() => fireResult("ぼくはずんだもん", false)); // 再生中にエンジンが何か聞いた

      nowSpy.mockReturnValue(1000);
      rerender({ ttsPlaying: false });
      act(() => fireResult("ぼくはずんだもんなのだ", true)); // 停止直後に final 化

      expect(append).not.toHaveBeenCalled();

      nowSpy.mockReturnValue(10_000); // 猶予を過ぎた後のユーザー発話は通る
      act(() => fireResult("こんどはユーザー", true));
      expect(append).toHaveBeenCalledWith("こんどはユーザー");
    } finally {
      nowSpy.mockRestore();
    }
  });

  it("再生開始時点で未確定だった interim (ユーザー発話) は捨てずに確定する", () => {
    // 無いと: 送信直前まで喋っていた内容の末尾が、読み上げ開始と同時に黙って消える
    installSpeechRecognition();
    const append = vi.fn();
    const { result, rerender } = renderVoice(append);

    act(() => result.current.toggle());
    act(() => fireResult("さいごのひとこと", false));
    expect(result.current.interimTranscript).toBe("さいごのひとこと");

    rerender({ ttsPlaying: true });

    expect(append).toHaveBeenCalledWith("さいごのひとこと");
  });
});
