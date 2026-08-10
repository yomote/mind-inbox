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

// ExtractFailed は hook が instanceof で種別を見るので実物を残す
// (丸ごと差し替えるとクラスが undefined になり、文面の出し分けが黙って死ぬ)。
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  startNewConsultation: vi.fn(),
  sendMessage: vi.fn(),
  extractMentions: vi.fn(),
  loadProblems: vi.fn(async () => []),
  loadProblem: vi.fn(),
  triageProblem: vi.fn(),
  createProblemPlan: vi.fn(),
}));

import {
  ExtractFailed,
  createProblemPlan,
  extractMentions,
  loadProblem,
  loadProblems,
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

function setup() {
  const transition = vi.fn();
  const view = renderHook(() => useConsultation(transition));
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

describe("[L1] useConsultation — 無音失敗の禁止 (ADR 0018)", () => {
  /**
   * 無いと静かに通るもの: **「ボタンを押しても何も起きない」実環境の症状** (2026-08-09)。
   *
   * 元の実装は startConsultation だけが catch していて、抽出・一覧・送信・トリアージは
   * 例外がそのまま unhandled rejection になっていた。画面は描画され続けるので mock E2E も
   * 単体テストも緑のまま、実環境でだけ「反応がない UI」になる。
   * ここでは全ハンドラについて「失敗したら必ず言葉が出る / 遷移しない」を固定する。
   */
  const failure = new Error("network");

  /** ハンドラごとの [名前, 落とす api, 実行, 起きてはいけない遷移先]。 */
  const cases: Array<{
    name: string;
    arrange: () => void;
    act: (c: ReturnType<typeof setup>["result"]["current"]) => Promise<void>;
    forbiddenRoute?: string;
  }> = [
    {
      name: "困りごとを抽出",
      arrange: () => vi.mocked(extractMentions).mockRejectedValue(failure),
      act: (c) => c.extract(),
      forbiddenRoute: "extractReview",
    },
    {
      name: "困りごと一覧を開く",
      arrange: () => vi.mocked(loadProblems).mockRejectedValue(failure),
      act: (c) => c.openProblemList(),
      forbiddenRoute: "problemList",
    },
    {
      name: "困りごとを開く",
      arrange: () => vi.mocked(loadProblem).mockRejectedValue(failure),
      act: (c) => c.openProblem("p1"),
      forbiddenRoute: "problemDetail",
    },
    {
      name: "トリアージ",
      arrange: () => vi.mocked(triageProblem).mockRejectedValue(failure),
      act: (c) => c.triage({ action: "resolve", problemId: "p1" }),
    },
    {
      name: "抽出結果の却下",
      arrange: () => vi.mocked(triageProblem).mockRejectedValue(failure),
      act: (c) => c.dismissExtracted("p1"),
    },
    {
      name: "困りごとから次の一歩",
      arrange: () => vi.mocked(createProblemPlan).mockRejectedValue(failure),
      act: (c) => c.createPlanForProblem("p1"),
    },
  ];

  it.each(cases)(
    "$name が失敗したらエラーを表示し、遷移しない",
    async ({ arrange, act: run, forbiddenRoute }) => {
      vi.mocked(startNewConsultation).mockResolvedValue(session());
      arrange();
      const { result, transition } = setup();
      await act(async () => {
        await result.current.startConsultation();
      });

      await act(async () => {
        await run(result.current);
      });

      expect(result.current.actionError).not.toBeNull();
      if (forbiddenRoute) expect(transition).not.toHaveBeenCalledWith(forbiddenRoute);
      // 失敗しても操作不能にならない (loading が立ちっぱなしだと以降すべて無反応になる)
      expect(result.current.loading).toBe(false);
    },
  );

  it("送信に失敗したら下書きを戻し、宙に浮いた発話を残さない", async () => {
    // 無いと: 自分の発話だけが画面に残って返事が来ず、入力し直しも強いられる
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(sendMessage).mockRejectedValue(failure);
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    act(() => result.current.setDraftMessage("眠れない日が続いている"));
    await act(async () => {
      await result.current.sendDraftMessage();
    });

    expect(result.current.actionError).not.toBeNull();
    expect(result.current.draftMessage).toBe("眠れない日が続いている");
    expect(result.current.session?.messages.map((m) => m.role)).toEqual(["assistant"]);
  });

  it("開いた Problem が消えていたら、その旨を出す", async () => {
    // 無いと: 一覧のカードを押しても無反応 (古い一覧を掴んだときの実挙動)
    vi.mocked(loadProblem).mockResolvedValue(null);
    const { result, transition } = setup();

    await act(async () => {
      await result.current.openProblem("gone");
    });

    expect(result.current.actionError).not.toBeNull();
    expect(transition).not.toHaveBeenCalledWith("problemDetail");
  });
});

describe("[L1] useConsultation — マウント時に API を呼ばない (#112)", () => {
  it("hook を張っただけでは通信しない", async () => {
    // 無いと: 未認証で API を叩き getAccessToken() がログインリダイレクトを誘発して、
    //         オンボーディングを見せないまま Entra へ飛ばす (#112 の再発)。
    //         履歴の先読みを撤去した (ADR 0034) 今、mount 時の通信はゼロが仕様。
    setup();

    await waitFor(() => {
      expect(loadProblems).not.toHaveBeenCalled();
      expect(startNewConsultation).not.toHaveBeenCalled();
    });
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

describe("[L1] useConsultation — 抽出の失敗をユーザーに見せる (#183)", () => {
  it("会話全文を渡して抽出する", async () => {
    // 無いと: 会話を送らない実装に戻り、ai-agent のプロセスメモリ頼みになる。
    //         メモリが生きている間は動くので手元では気づけず、scale-to-zero した
    //         実環境でだけ抽出が落ちる (#183 の元症状)。
    const current = session();
    vi.mocked(startNewConsultation).mockResolvedValue(current);
    vi.mocked(extractMentions).mockResolvedValue({
      sessionId: "s1",
      items: [],
      newProblemCount: 0,
      updatedProblemCount: 0,
    });
    const transition = vi.fn();
    const { result } = renderHook(() => useConsultation(transition));

    await act(async () => await result.current.startConsultation());
    await act(async () => await result.current.extract());

    expect(extractMentions).toHaveBeenCalledWith("s1", current.messages);
  });

  it.each([
    ["session-missing", "取り出せませんでした"],
    ["llm-parse-failed", "整理に失敗しました"],
    ["upstream-failed", "通信状況"],
  ])("%s は理由に応じた案内を出し、画面を遷移させない", async (kind, expected) => {
    // 無いと: 押しても何も起きない状態に戻る。以前は catch が無く、遷移もせず
    //         スピナーが止まるだけで、押した本人には何が起きたか一切分からなかった。
    //         「壊れている」と「困りごとが 0 件だった」も区別できない (#183 / ADR 0018)。
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(extractMentions).mockRejectedValue(new ExtractFailed(kind as never));
    const transition = vi.fn();
    const { result } = renderHook(() => useConsultation(transition));

    await act(async () => await result.current.startConsultation());
    transition.mockClear();
    await act(async () => await result.current.extract());

    expect(result.current.actionError).toContain(expected);
    expect(transition).not.toHaveBeenCalled();
    // 失敗しても操作不能にならない (スピナーが回りっぱなしにならない)
    expect(result.current.loading).toBe(false);
  });

  it("エラーは閉じられる (次の操作を妨げない)", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(extractMentions).mockRejectedValue(new ExtractFailed("upstream-failed"));
    const { result } = renderHook(() => useConsultation(vi.fn()));

    await act(async () => await result.current.startConsultation());
    await act(async () => await result.current.extract());
    expect(result.current.actionError).not.toBeNull();

    act(() => result.current.clearActionError());
    expect(result.current.actionError).toBeNull();
  });
});
