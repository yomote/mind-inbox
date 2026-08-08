// @vitest-environment jsdom
/**
 * [L1] 相談フロー hook の状態遷移。
 *
 * この hook は「api を呼ぶ → 状態を差し替える → 画面遷移する」の結節点で、
 * 壊れても画面自体は描画されるため mock E2E のゴールデンパス以外では気づけない。
 * ここでは**画面が持つべき状態の更新と遷移先**を固定する (通信そのものは api 層の担当)。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api", () => ({
  startNewConsultation: vi.fn(),
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

import {
  createActionPlan,
  loadHistories,
  loadProblem,
  loadProblems,
  organizeResult,
  saveHistory,
  sendMessage,
  startNewConsultation,
  triageProblem,
} from "../api";
import { useConsultation } from "./useConsultation";

const session = (overrides = {}) => ({
  id: "s1",
  title: "相談セッション",
  messages: [
    { id: "a-1", role: "assistant" as const, text: "どうしましたか", createdAt: "2026-01-01" },
  ],
  ...overrides,
});

const problem = (id: string) => ({ id, title: `問題 ${id}` }) as never;

function setup(options: { ready?: boolean } = {}) {
  const transition = vi.fn();
  const view = renderHook(
    ({ ready }: { ready: boolean }) => useConsultation(transition, { ready }),
    { initialProps: { ready: options.ready ?? true } },
  );
  return { transition, ...view };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("[L1] useConsultation — 相談の開始と発話", () => {
  it("開始に成功したらセッションを持ち、対話画面へ遷移する", async () => {
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const { result, transition } = setup();

    await act(async () => {
      await result.current.startConsultation();
    });

    expect(startNewConsultation).toHaveBeenCalledWith("");
    expect(result.current.session?.id).toBe("s1");
    expect(transition).toHaveBeenCalledWith("session");
  });

  it("開始に失敗したらエラーを表示し、遷移しない", async () => {
    // 無いと: 無音で失敗して「押しても何も起きない」画面になる (ADR 0018 の無音失敗禁止)
    vi.mocked(startNewConsultation).mockRejectedValue(new Error("network"));
    const { result, transition } = setup();

    await act(async () => {
      await result.current.startConsultation();
    });

    expect(result.current.actionError).not.toBeNull();
    expect(transition).not.toHaveBeenCalledWith("session");
  });

  it("最初のユーザー発話でタイトルを自動生成し、返事を追記する", async () => {
    // 無いと: 履歴の見出しが「相談セッション」のまま並び、何の相談か分からなくなる
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(sendMessage).mockResolvedValue({
      id: "a-2",
      role: "assistant",
      text: "詳しく教えてください",
      createdAt: "2026-01-01",
    });
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    act(() => result.current.setDraftMessage("眠れない日が続いている"));
    await act(async () => {
      await result.current.sendDraftMessage();
    });

    expect(result.current.session?.title).toBe("眠れない日が続いている");
    expect(result.current.session?.messages.map((m) => m.role)).toEqual([
      "assistant",
      "user",
      "assistant",
    ]);
    expect(result.current.draftMessage).toBe("");
  });

  it("空の下書きは送信しない", async () => {
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    act(() => result.current.setDraftMessage("   "));
    await act(async () => {
      await result.current.sendDraftMessage();
    });

    expect(sendMessage).not.toHaveBeenCalled();
  });
});

describe("[L1] useConsultation — 整理・プラン・保存", () => {
  it("整理 → プラン → 保存で状態が積み上がり、履歴に載る", async () => {
    // 無いと: 途中の状態を落として保存し、履歴から結果やプランが欠ける
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(organizeResult).mockResolvedValue({
      summary: "疲労",
      emotions: ["不安"],
      priorities: ["休む"],
    });
    vi.mocked(createActionPlan).mockResolvedValue({ title: "48h", steps: ["休む"] });
    vi.mocked(saveHistory).mockResolvedValue({
      id: "h1",
      title: "相談セッション",
      createdAt: "2026-01-01",
      result: { summary: "疲労", emotions: ["不安"], priorities: ["休む"] },
      plan: { title: "48h", steps: ["休む"] },
    });
    const { result, transition } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    await act(async () => {
      await result.current.organize();
    });
    expect(result.current.result?.summary).toBe("疲労");
    expect(transition).toHaveBeenCalledWith("result");

    await act(async () => {
      await result.current.createPlan();
    });
    expect(result.current.plan?.title).toBe("48h");
    expect(transition).toHaveBeenCalledWith("actionPlan");

    await act(async () => {
      await result.current.saveAndGoHistory();
    });
    expect(result.current.histories.map((h) => h.id)).toEqual(["h1"]);
    expect(transition).toHaveBeenCalledWith("history");
  });

  it("整理結果が無いままプランを作ろうとしても呼ばない", () => {
    const { result } = setup();
    act(() => {
      void result.current.createPlan();
    });
    expect(createActionPlan).not.toHaveBeenCalled();
  });
});

describe("[L1] useConsultation — 困りごとのトリアージ", () => {
  it("却下・統合は一覧へ戻る (対象が消えるため)", async () => {
    // 無いと: 消えた Problem の詳細画面に留まり、空の画面をユーザーに見せる
    vi.mocked(triageProblem).mockResolvedValue(null);
    vi.mocked(loadProblems).mockResolvedValue([]);
    const { result, transition } = setup();

    await act(async () => {
      await result.current.triage({ action: "dismiss", problemId: "p1" });
    });

    expect(transition).toHaveBeenCalledWith("problemList");
  });

  it("状態変更 (解決) は詳細に留まり、一覧キャッシュを更新する", async () => {
    // 無いと: 一覧に戻ったとき古い状態が表示される (棚卸しが反映されない)
    vi.mocked(triageProblem).mockResolvedValue(problem("p1"));
    vi.mocked(loadProblems).mockResolvedValue([problem("p1")]);
    const { result, transition } = setup();

    await act(async () => {
      await result.current.triage({ action: "resolve", problemId: "p1" });
    });

    expect(loadProblems).toHaveBeenCalled();
    expect(transition).not.toHaveBeenCalledWith("problemList");
  });

  it("存在しない Problem を開いても遷移しない", async () => {
    vi.mocked(loadProblem).mockResolvedValue(null);
    const { result, transition } = setup();

    await act(async () => {
      await result.current.openProblem("missing");
    });

    expect(transition).not.toHaveBeenCalledWith("problemDetail");
  });
});

describe("[L1] useConsultation — 履歴の初期読み込み (#112)", () => {
  it("認証が確定するまで履歴 API を呼ばない", async () => {
    // 無いと: 未認証で API を叩き getAccessToken() がログインリダイレクトを誘発して、
    //         オンボーディングを見せないまま Entra へ飛ばす (#112 の再発)
    const { rerender } = setup({ ready: false });
    await waitFor(() => expect(loadHistories).not.toHaveBeenCalled());

    rerender({ ready: true });
    await waitFor(() => expect(loadHistories).toHaveBeenCalledTimes(1));
  });
});

describe("[L1] useConsultation — reset", () => {
  it("reset で相談の状態を捨てる (ログアウト)", async () => {
    // 無いと: ログアウト後に前ユーザーのセッション・困りごとが画面に残る
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });
    expect(result.current.session).not.toBeNull();

    act(() => result.current.reset());

    await waitFor(() => expect(result.current.session).toBeNull());
    expect(result.current.problems).toEqual([]);
    expect(result.current.draftMessage).toBe("");
  });
});
