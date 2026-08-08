/**
 * [L1] SessionComposer — 送信可否と音声入力の状態表示。
 *
 * 無いと何が静かに通るか (testing/strategy.md §1.2):
 * 空白だけの入力で送信ボタンが押せてしまい、ai-agent が 422 を返す往復が生まれる。
 * また `degraded` (高精度認識に繋がらずブラウザ認識へ落ちた状態) の表示が消えても、
 * 音声入力自体は動き続けるので**誰も気づかない** — 精度が落ちたことがユーザーに
 * 伝わらなくなる (#121 / ADR 0023 がわざわざ可視化した情報)。
 *
 * Phase 3 (#141 TTS 分離 / #142 useConsultation 抽出) がこのコンポーネント周辺を
 * 動かすため、着手前の振る舞いをここで固定しておく (epic #135)。
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SessionComposer } from "./SessionComposer";

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

vi.mock("../../voice/useVoiceInput", () => ({
  useVoiceInput: () => voiceState,
}));

function setVoice(patch: Partial<typeof voiceState>) {
  Object.assign(voiceState, patch);
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
    expect(screen.getByRole("button", { name: "送信" })).toBeDisabled();
  });

  it("空白だけの入力でも送信できない (ai-agent が 422 を返す往復を作らない)", () => {
    renderComposer({ value: "   \n  " });
    expect(screen.getByRole("button", { name: "送信" })).toBeDisabled();
  });

  it("本文があれば送信でき、onSend が呼ばれる", async () => {
    const { onSend } = renderComposer({ value: "最近ずっと気が重い" });
    const button = screen.getByRole("button", { name: "送信" });
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("送信中は本文があっても送信できない (二重送信を防ぐ)", () => {
    renderComposer({ value: "気が重い", loading: true });
    // loading 中はラベルがスピナーに変わるため name では引けない
    const buttons = screen.getAllByRole("button");
    expect(buttons.some((b) => b.hasAttribute("disabled"))).toBe(true);
  });

  it("ブラウザ認識へ落ちた状態 (degraded) をユーザーに見せる", () => {
    setVoice({ listening: true, engine: "browser", degraded: true });
    renderComposer({ value: "" });
    expect(screen.getByText("ブラウザ認識 (高精度認識に接続できず)")).toBeInTheDocument();
    setVoice({ listening: false, engine: null, degraded: false });
  });

  it("高精度認識で繋がっているときは degraded の警告を出さない", () => {
    setVoice({ listening: true, engine: "azure", degraded: false });
    renderComposer({ value: "" });
    expect(screen.getByText("高精度認識")).toBeInTheDocument();
    expect(screen.queryByText(/接続できず/)).not.toBeInTheDocument();
    setVoice({ listening: false, engine: null, degraded: false });
  });

  it("音声入力が使えない環境ではその旨を伝え、ボタンを押させない", () => {
    setVoice({ supported: false });
    renderComposer({ value: "" });
    expect(screen.getByText("このブラウザは音声入力に対応していません。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /音声入力開始/ })).toBeDisabled();
    setVoice({ supported: true });
  });
});
