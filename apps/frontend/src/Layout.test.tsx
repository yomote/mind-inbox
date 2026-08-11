// @vitest-environment jsdom
/**
 * [L2] Layout の「配線」テスト。
 *
 * 無いと何が静かに通るか: hook (voice / consultation) を切り出した後、**呼び出し側の
 * 配線だけが失われても** hook 単体テストは緑のまま通る。実際 PR #152 のレビューで、
 * iOS 音声解錠 `unlock()` が分離の過程で呼び出し元を失いデッドコード化していたのを
 * 指摘された (hook のテスト 8 本はすべて緑だった)。
 * ここでは「ユーザー操作 → hook のどの入口が叩かれるか」だけを固定する。
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./auth/msal", () => ({
  authEnabled: false,
  getAccount: () => null,
  initAuth: vi.fn(async () => undefined),
  login: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("./api", () => ({
  startNewConsultation: vi.fn(async () => ({
    id: "s1",
    title: "相談セッション",
    messages: [{ id: "a-1", role: "assistant", text: "どうしましたか", createdAt: "2026-01-01" }],
  })),
  sendMessage: vi.fn(),
  organizeResult: vi.fn(),
  createActionPlan: vi.fn(),
  extractMentions: vi.fn(),
  loadHistories: vi.fn(async () => []),
  saveHistory: vi.fn(),
  loadProblems: vi.fn(async () => []),
  loadProblem: vi.fn(),
  triageProblem: vi.fn(),
  createProblemPlan: vi.fn(),
}));

import { Layout } from "./Layout";

/**
 * iOS の解錠は「音量 0 の空発話を speechSynthesis に流す」で表現されるので、
 * speak に渡された utterance の volume で判別する。
 *
 * `autoEnd: false` を渡すと onend を呼ばない = 発話が「再生中のまま」になり、
 * 再生中の画面遷移・停止の配線を検証できる。
 */
function speechSynthesisSpy({ autoEnd = true }: { autoEnd?: boolean } = {}) {
  const spoken: { text: string; volume: number }[] = [];
  vi.stubGlobal("speechSynthesis", {
    speak: (u: { text: string; volume: number; onend?: (() => void) | null }) => {
      spoken.push({ text: u.text, volume: u.volume });
      if (autoEnd) u.onend?.();
    },
    cancel: vi.fn(),
    getVoices: () => [],
  });
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
  return spoken;
}

const renderLayout = () =>
  render(
    <MemoryRouter initialEntries={["/home"]}>
      <Layout themeMode="light" onToggleTheme={() => {}} />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
});

// vitest は isolate:false で走るので、DOM は test 間で共有される。
// 片付けないと前のテストのツリー (別画面のヘッダー等) が次のクエリに引っかかる。
afterEach(cleanup);

describe("[L2] Layout — 音声解錠 (iOS) の配線", () => {
  it("相談開始のタップで音声を解錠する", async () => {
    // 無いと: iOS Safari で以降の自動読み上げが無音になる。ブラウザ (headless) では
    //         再生されないので E2E でも気づけない — 配線の有無をここで固定する。
    //         解錠は冪等 (最初のジェスチャ 1 回) なので、3 つの呼び出し口
    //         (相談開始 / 送信 / 読み上げトグル) は同じガードを共有する
    const spoken = speechSynthesisSpy();
    renderLayout();

    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));

    await waitFor(() => expect(spoken.some((u) => u.volume === 0)).toBe(true));
  });

  it("解錠は 1 度きり (2 回目以降のタップで無駄な発話をしない)", async () => {
    // 無いと: タップのたびに空の utterance が積まれ、読み上げの頭が欠ける
    const spoken = speechSynthesisSpy();
    renderLayout();

    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    await waitFor(() => expect(spoken.some((u) => u.volume === 0)).toBe(true));

    spoken.length = 0;
    await userEvent.click(await screen.findByRole("button", { name: /読み上げ(ON|OFF)$/ }));

    expect(spoken.filter((u) => u.volume === 0)).toHaveLength(0);
  });
});

describe("[L2] Layout — 新しい相談の開始で読み上げを止める (#146 レビュー)", () => {
  it("再生中に新しい相談を始めると読み上げが止まる (旧セッションの音声と speaking を持ち込まない)", async () => {
    // 無いと: 前の応答の再生中にホームへ戻って新しい相談を始めると、旧セッションの音声が
    // 鳴り続け、メッセージ 0 件の新セッションのマスコットが speaking のまま表示される退行が
    // 静かに通る (tts と consultation は互いを知らないので、止める配線は Layout にしか無い)。
    speechSynthesisSpy({ autoEnd: false }); // onend が来ない = 再生しっぱなし
    renderLayout();

    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));
    // AI の返事が自動読み上げされ、再生中の表示になる (standalone = ブラウザ読み上げ)
    const chip = await screen.findByTestId("tts-status");
    expect(chip.textContent).toContain("読み上げ中");

    // ホームへ戻り、再生が続いたまま新しい相談を開始する
    await userEvent.click(screen.getByRole("button", { name: "Mind Inbox" }));
    await userEvent.click(await screen.findByRole("button", { name: "新しい相談を始める" }));

    // 開始タップで読み上げが止まり、「読み上げ中」表示 (= ttsStatus playing) が消える
    await waitFor(() => expect(screen.queryByTestId("tts-status")).toBeNull());
  });
});
