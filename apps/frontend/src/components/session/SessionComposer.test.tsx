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
