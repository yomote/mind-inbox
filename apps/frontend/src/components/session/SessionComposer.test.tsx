// @vitest-environment jsdom
/**
 * [L1] SessionComposer — 送信可否と音声入力の状態表示。
 *
 * 無いと何が静かに通るか: 空白だけの入力で送信ボタンが押せてしまい、ai-agent が 422 を
 * 返す往復が生まれる。また `degraded` (高精度認識に繋がらずブラウザ認識へ落ちた状態) の
 * 表示が消えても、音声入力自体は動き続けるので**誰も気づかない** — 精度が落ちたことが
 * ユーザーに伝わらなくなる (#121 / ADR 0023 がわざわざ可視化した情報)。
 *
 * PR #152 で session/ 周辺が動いたが SessionComposer は props 不変のまま無試験だったので、
 * ここで振る舞いを固定する (epic #135)。
 */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionComposer } from "./SessionComposer";

// vitest は isolate:false で走るので、DOM は test ファイル間で共有される。
// 片付けないと前のテストのツリーが次のクエリに引っかかり順序依存 flaky になる。
afterEach(cleanup);

// useVoiceInput は Speech API と BFF を触るため、ここでは戻り値の形だけを与える。
// 実装の検証は voice/ 側の単体テスト (azureSpeech / stitcher) の担当。
const voiceState = {
  supported: true,
  listening: false,
  phase: "idle" as "idle" | "preparing" | "listening",
  engine: null as "azure" | "browser" | null,
  degraded: false,
  interimTranscript: "",
  elapsedSec: 0,
  error: null as string | null,
  toggle: vi.fn(),
  stop: vi.fn(),
};
const defaultVoiceState = { ...voiceState };

vi.mock("../../voice/useVoiceInput", () => ({
  useVoiceInput: () => voiceState,
}));

afterEach(() => {
  Object.assign(voiceState, defaultVoiceState);
});

function isDisabled(el: HTMLElement): boolean {
  return (el as HTMLButtonElement).disabled;
}

function renderComposer(props: Partial<React.ComponentProps<typeof SessionComposer>> = {}) {
  const onSend = vi.fn();
  const onChange = vi.fn();
  render(
    <SessionComposer
      value=""
      onChange={onChange}
      onSend={onSend}
      loading={false}
      speaking={false}
      ttsEnabled={false}
      voiceError={null}
      onToggleTtsEnabled={vi.fn()}
      onStopSpeaking={vi.fn()}
      {...props}
    />,
  );
  return { onSend, onChange };
}

describe("[L1] SessionComposer", () => {
  it("空文字では送信できない", () => {
    renderComposer({ value: "" });
    expect(isDisabled(screen.getByRole("button", { name: "送信" }))).toBe(true);
  });

  it("空白だけの入力でも送信できない (ai-agent が 422 を返す往復を作らない)", () => {
    renderComposer({ value: "   \n  " });
    expect(isDisabled(screen.getByRole("button", { name: "送信" }))).toBe(true);
  });

  it("本文があれば送信でき、onSend が呼ばれる", async () => {
    const { onSend } = renderComposer({ value: "最近ずっと気が重い" });
    const button = screen.getByRole("button", { name: "送信" });
    expect(isDisabled(button)).toBe(false);
    await userEvent.click(button);
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("送信中は本文があっても送信できない (二重送信を防ぐ)", () => {
    renderComposer({ value: "気が重い", loading: true });
    // loading 中はラベルがスピナーに変わるため name では引けない
    expect(screen.getAllByRole("button").some(isDisabled)).toBe(true);
  });

  it("ブラウザ認識へ落ちた状態 (degraded) をユーザーに見せる", () => {
    Object.assign(voiceState, { listening: true, engine: "browser", degraded: true });
    renderComposer({ value: "" });
    expect(screen.queryByText("ブラウザ認識 (高精度認識に接続できず)")).not.toBeNull();
  });

  it("高精度認識で繋がっているときは degraded の警告を出さない", () => {
    Object.assign(voiceState, { listening: true, engine: "azure", degraded: false });
    renderComposer({ value: "" });
    expect(screen.queryByText("高精度認識")).not.toBeNull();
    expect(screen.queryByText(/接続できず/)).toBeNull();
  });

  it("音声入力が使えない環境ではその旨を伝え、ボタンを押させない", () => {
    Object.assign(voiceState, { supported: false });
    renderComposer({ value: "" });
    expect(screen.queryByText("このブラウザは音声入力に対応していません。")).not.toBeNull();
    expect(isDisabled(screen.getByRole("button", { name: /音声入力開始/ }))).toBe(true);
  });
});

describe("[L1] SessionComposer — 音声入力の即時性 (#186)", () => {
  it("マイクを押したら入力欄にフォーカスが移る", async () => {
    // 無いと: 押した後にユーザーが入力欄をクリックし直さないと手直し・送信できない。
    //         「押したのに入力欄が反応しない」という PO 報告そのものに戻る。
    renderComposer({ value: "" });

    await userEvent.click(screen.getByRole("button", { name: /音声入力開始/ }));

    expect(document.activeElement).toBe(screen.getByPlaceholderText("ここに入力 / 話して入力"));
    expect(voiceState.toggle).toHaveBeenCalledTimes(1);
  });

  it("認識途中のテキストは入力欄の中に出る", () => {
    // 無いと: 喋っている最中は入力欄が空のままに見え、「入っていない」と受け取られる
    //         (旧実装は入力欄の外の小さなキャプションに出していた)。
    Object.assign(voiceState, {
      listening: true,
      phase: "listening",
      interimTranscript: "さいきん ねむれなくて",
    });
    renderComposer({ value: "仕事のことなんだけど" });

    const field = screen.getByPlaceholderText("ここに入力 / 話して入力") as HTMLTextAreaElement;
    expect(field.value).toBe("仕事のことなんだけど\nさいきん ねむれなくて");
  });

  it("マイク準備中と、聞いている状態を別々に出す", () => {
    // 無いと: 押してからマイクが開くまでの区間 (iOS では特に長い) が「聞いている」と
    //         同じ見た目になり、その間に喋った内容が黙って落ちる。
    Object.assign(voiceState, { listening: true, phase: "preparing" });
    renderComposer({ value: "" });
    expect(screen.queryByText("マイクを準備中…")).not.toBeNull();
    expect(screen.queryByText(/聞いています/)).toBeNull();

    cleanup();
    Object.assign(voiceState, { listening: true, phase: "listening", elapsedSec: 65 });
    renderComposer({ value: "" });
    expect(screen.queryByText("聞いています 1:05")).not.toBeNull();
    expect(screen.queryByText("マイクを準備中…")).toBeNull();
  });
});

describe("[L1] SessionComposer — 読み上げの待ち時間 (#185)", () => {
  it("合成中は「準備中」、再生中は「読み上げ中」を出す", () => {
    // 無いと: 合成にかかる数秒〜十数秒のあいだ画面が何も変わらず、待てば鳴るのか
    //         もう終わったのかが判別できない状態に戻る。
    renderComposer({ value: "", speaking: true, ttsStatus: "synthesizing" });
    expect(screen.getByTestId("tts-status").textContent).toContain("ずんだもんが準備中");

    cleanup();
    renderComposer({ value: "", speaking: true, ttsStatus: "playing" });
    expect(screen.getByTestId("tts-status").textContent).toContain("読み上げ中");
  });

  it("読み上げていないときは状態表示を出さない", () => {
    renderComposer({ value: "", speaking: false, ttsStatus: "idle" });
    expect(screen.queryByTestId("tts-status")).toBeNull();
  });
});
