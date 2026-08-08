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

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
 */
function speechSynthesisSpy() {
  const spoken: { text: string; volume: number }[] = [];
  vi.stubGlobal("speechSynthesis", {
    speak: (u: { text: string; volume: number; onend?: () => void }) => {
      spoken.push({ text: u.text, volume: u.volume });
      u.onend?.();
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
