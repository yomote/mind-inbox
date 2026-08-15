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
// 下書きプレビュー (#187): テストビルドは mock モードではないため、素の previewSupported は
// false になり自動更新・確定の分岐が一切通らない。getter 経由で test ごとに切り替える
// (true = プレビュー有効環境 / false = BFF 未結線の従来経路)。
const apiFlags = vi.hoisted(() => ({ previewSupported: true }));

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  startNewConsultation: vi.fn(),
  sendMessage: vi.fn(),
  respondToApproval: vi.fn(),
  extractMentions: vi.fn(),
  loadProblems: vi.fn(async () => []),
  loadProblem: vi.fn(),
  triageProblem: vi.fn(),
  createProblemPlan: vi.fn(),
  get previewSupported() {
    return apiFlags.previewSupported;
  },
  previewExtraction: vi.fn(async (sessionId: string) => ({
    sessionId,
    items: [],
    newProblemCount: 0,
    updatedProblemCount: 0,
  })),
  commitPreview: vi.fn(),
}));

import {
  ApprovalAlreadyProcessed,
  ApprovalExpired,
  ApprovalRequestUnusable,
  ExtractFailed,
  commitPreview,
  createProblemPlan,
  extractMentions,
  loadProblem,
  loadProblems,
  previewExtraction,
  respondToApproval,
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
  apiFlags.previewSupported = true;
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
      message: {
        id: "a-2",
        role: "assistant",
        text: "詳しく教えてください",
        createdAt: "2026-01-01",
      },
      approval: null,
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

  // 「困りごとを抽出」は従来経路 (プレビュー無効環境) の挙動。プレビュー有効環境の
  // 確定 (commitPreview) の無音失敗禁止は下の [単体] プレビュー describe が固定する。
  beforeEach(() => {
    apiFlags.previewSupported = false;
  });

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

describe("[単体] useConsultation — 下書きプレビュー (#187 / ADR 0039)", () => {
  const reply = (id: string) => ({
    message: {
      id,
      role: "assistant" as const,
      text: "受け止めました",
      createdAt: "2026-01-01",
    },
    approval: null,
  });

  async function sendTimes(result: ReturnType<typeof setup>["result"], texts: string[]) {
    for (const [i, text] of texts.entries()) {
      vi.mocked(sendMessage).mockResolvedValue(reply(`a-r${i}`));
      await act(async () => {
        result.current.setDraftMessage(text);
      });
      await act(async () => {
        await result.current.sendDraftMessage();
      });
    }
  }

  it("ユーザー発話 2 回ごとに自動更新され、毎ターンは走らない", async () => {
    // 無いと: 「2 往復ごと」の間引き (LLM 呼び出しコストの上限 — ADR 0039 D2) が
    //         毎ターン実行に退行しても、画面は普通に動き続けて気づけない。逆に
    //         一度も走らない退行でも右ペインが空のままなだけで例外は出ない。
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const draft = {
      sessionId: "s1",
      items: [],
      newProblemCount: 0,
      updatedProblemCount: 0,
    };
    vi.mocked(previewExtraction).mockResolvedValue(draft);
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    await sendTimes(result, ["1 回目の発話"]);
    expect(previewExtraction).not.toHaveBeenCalled();

    await sendTimes(result, ["2 回目の発話"]);
    await waitFor(() => expect(result.current.preview).toEqual(draft));
    expect(previewExtraction).toHaveBeenCalledTimes(1);
    // 会話全文を渡す (#183 と同じ理由: 真実は画面が持っている)。
    const [sessionId, messages] = vi.mocked(previewExtraction).mock.calls[0];
    expect(sessionId).toBe("s1");
    expect(messages.filter((m: { role: string }) => m.role === "user")).toHaveLength(2);
  });

  it("整理中に会話が進んだら、完了後に最新の会話で自動更新する (PR #282 再レビュー P2)", async () => {
    // 無いと: 実行中に届いた更新要求が捨てられ、先行リクエストは送信前の会話から作った
    //         古い下書きを返して止まる。以降は手動で押すか更に 2 往復するまで更新されず、
    //         §5.8 の「2 往復ごとに更新」が静かに満たされなくなる (画面は普通に動く)。
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const calls: number[] = [];
    let resolveFirst: ((value: never) => void) | undefined;
    const latest = {
      sessionId: "s1",
      items: [draftItem],
      newProblemCount: 0,
      updatedProblemCount: 1,
    };
    vi.mocked(previewExtraction).mockImplementation(async (_sessionId, messages) => {
      calls.push(messages.length);
      if (calls.length === 1) return await new Promise((resolve) => (resolveFirst = resolve));
      return latest as never;
    });
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    // 「今すぐ整理」を開始 (完了させない)。
    act(() => {
      result.current.refreshPreview();
    });
    expect(previewExtraction).toHaveBeenCalledTimes(1);
    const messagesAtStart = calls[0];

    // 実行中に 2 往復ぶん送信 = 自動更新の契機。重ねて実行はしない。
    await sendTimes(result, ["1 回目", "2 回目"]);
    expect(previewExtraction).toHaveBeenCalledTimes(1);

    // 先行処理が完了したら、持ち越した最新の会話で自動的に走る。
    await act(async () => {
      resolveFirst?.({
        sessionId: "s1",
        items: [],
        newProblemCount: 0,
        updatedProblemCount: 0,
      } as never);
    });

    await waitFor(() => expect(previewExtraction).toHaveBeenCalledTimes(2));
    expect(calls[1]).toBeGreaterThan(messagesAtStart);
    await waitFor(() => expect(result.current.preview).toEqual(latest));
    expect(result.current.previewStatus).toBe("idle");
  });

  it("プレビューの失敗は会話を止めない (Snackbar に出さず、ペイン内の状態にする)", async () => {
    // 無いと: 背景処理の失敗が actionError (Snackbar) や unhandled rejection に化けて
    //         会話そのものを邪魔する。あるいは完全に無音で「右ペインが更新されない」
    //         だけになり、壊れていることに誰も気づけない (ADR 0018)。
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(previewExtraction).mockRejectedValue(new Error("preview down"));
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    await sendTimes(result, ["1 回目", "2 回目"]);

    await waitFor(() => expect(result.current.previewStatus).toBe("error"));
    expect(result.current.actionError).toBeNull();

    // 会話は続けられる (loading が preview に巻き込まれない)。
    expect(result.current.loading).toBe(false);
    await sendTimes(result, ["3 回目"]);
    expect(result.current.session?.messages.length).toBeGreaterThan(5);
  });

  const emptyDraft = { sessionId: "s1", items: [], newProblemCount: 0, updatedProblemCount: 0 };

  it("手動「今すぐ整理」は往復数と無関係に更新する", async () => {
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(previewExtraction).mockResolvedValue(emptyDraft);
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    await act(async () => {
      result.current.refreshPreview();
    });

    await waitFor(() => expect(previewExtraction).toHaveBeenCalledTimes(1));
  });

  const draftItem = {
    mention: { id: "m-d1", excerpt: "眠れない" },
    grouping: { kind: "existing", problemId: "p-career" },
  } as never;

  it("確定は表示中の下書きをそのまま渡し、再抽出しない (PR #282 P1)", async () => {
    // 無いと: 確定が extractMentions (再抽出) に退行しても例外は出ず、右ペインで
    //         確認した内容と違う Problem が静かに保存される (ADR 0039 D1/D3 違反)。
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const draft = {
      sessionId: "s1",
      items: [draftItem],
      newProblemCount: 0,
      updatedProblemCount: 1,
    };
    vi.mocked(previewExtraction).mockResolvedValue(draft);
    const committed = { ...draft, sessionId: "s1" };
    vi.mocked(commitPreview).mockResolvedValue(committed);
    const { result, transition } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });
    await sendTimes(result, ["1 回目", "2 回目"]);
    await waitFor(() => expect(result.current.preview).toEqual(draft));

    await act(async () => {
      await result.current.extract();
    });

    expect(commitPreview).toHaveBeenCalledWith("s1", draft.items);
    expect(extractMentions).not.toHaveBeenCalled();
    expect(result.current.extraction).toEqual(committed);
    expect(transition).toHaveBeenCalledWith("extractReview");
    // 確定済みの下書きを「未確定」として残さない (揮発)。
    expect(result.current.preview).toBeNull();
  });

  it("更新中に確定しても、保存されるのは押した時点の表示内容 (PR #282 再レビュー P1)", async () => {
    // 無いと: 確定 (400ms) の最中に飛行中の更新 (600ms) が返ると、保存したのは古い内容
    //         なのに画面だけ新しい内容に差し替わる。レビュー画面には保存された古い内容が
    //         出るので、ユーザーが最後に見た右ペインと保存結果が食い違う (D1/D3 が再び破れる)。
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const shown = {
      sessionId: "s1",
      items: [draftItem],
      newProblemCount: 0,
      updatedProblemCount: 1,
    };
    vi.mocked(previewExtraction).mockResolvedValue(shown);
    vi.mocked(commitPreview).mockImplementation(async (_id, drafts) => ({
      sessionId: "s1",
      items: drafts,
      newProblemCount: 0,
      updatedProblemCount: 1,
    }));
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });
    await sendTimes(result, ["1 回目", "2 回目"]);
    await waitFor(() => expect(result.current.preview).toEqual(shown));

    // 「今すぐ整理」を走らせ、応答が返る前に確定する。
    let resolveLate: ((value: never) => void) | undefined;
    // 更新後は 2 件 (押した時点は 1 件) — 保存内容と表示が食い違えば検出できる差分。
    const late = {
      sessionId: "s1",
      items: [draftItem, { mention: { id: "m-late" }, grouping: { kind: "new", problemId: "p2" } }],
      newProblemCount: 1,
      updatedProblemCount: 1,
    } as never;
    vi.mocked(previewExtraction).mockImplementation(
      () => new Promise((resolve) => (resolveLate = resolve)),
    );
    act(() => {
      result.current.refreshPreview();
    });
    await act(async () => {
      await result.current.extract();
    });

    // 確定に渡ったのは押した時点のスナップショット。
    expect(commitPreview).toHaveBeenCalledWith("s1", shown.items);
    expect(result.current.extraction?.items).toEqual(shown.items);

    // 遅れて返った更新は表示にも反映しない (確定済みの下書きは揮発したまま)。
    await act(async () => {
      resolveLate?.(late);
    });
    expect(result.current.preview).toBeNull();
  });

  it("下書きが無い間は確定できない (ボタン disabled の二重ガード)", async () => {
    // 無いと: 「この内容」が存在しないのに確定が走り、空の確定 or 再抽出相当の
    //         別内容が保存されうる。
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const { result, transition } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });
    transition.mockClear();

    await act(async () => {
      await result.current.extract();
    });

    expect(commitPreview).not.toHaveBeenCalled();
    expect(extractMentions).not.toHaveBeenCalled();
    expect(transition).not.toHaveBeenCalled();
  });

  it("確定の失敗はエラーを表示し、下書きを残したまま遷移しない", async () => {
    // 無いと: 確定の無音失敗 —「押したのに何も起きず、下書きも消えた」(ADR 0018)。
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    const draft = {
      sessionId: "s1",
      items: [draftItem],
      newProblemCount: 0,
      updatedProblemCount: 1,
    };
    vi.mocked(previewExtraction).mockResolvedValue(draft);
    vi.mocked(commitPreview).mockRejectedValue(new Error("commit down"));
    const { result, transition } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });
    await sendTimes(result, ["1 回目", "2 回目"]);
    await waitFor(() => expect(result.current.preview).toEqual(draft));
    transition.mockClear();

    await act(async () => {
      await result.current.extract();
    });

    expect(result.current.actionError).toContain("確定できませんでした");
    expect(transition).not.toHaveBeenCalled();
    expect(result.current.preview).toEqual(draft);
    expect(result.current.loading).toBe(false);
  });

  it("古いセッションの preview 応答は破棄される (PR #282 P2)", async () => {
    // 無いと: 「今すぐ整理」直後に中断 → 新規相談を開始すると、旧セッションの応答が
    //         新セッションの右ペインに現れる (preview 600ms > 開始 350ms で実在する競合)。
    //         そのまま確定すると関係ない困りごとが保存される。
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    let resolveStale: ((value: typeof staleDraft) => void) | undefined;
    const staleDraft = {
      sessionId: "s1",
      items: [draftItem],
      newProblemCount: 0,
      updatedProblemCount: 1,
    };
    vi.mocked(previewExtraction).mockImplementation(
      () => new Promise((resolve) => (resolveStale = resolve)),
    );
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });

    // 旧セッションで「今すぐ整理」— 応答は返さないまま新セッションを開始する。
    act(() => {
      result.current.refreshPreview();
    });
    vi.mocked(startNewConsultation).mockResolvedValue(session({ id: "s2", messages: [] }));
    await act(async () => {
      await result.current.startConsultation();
    });

    // 旧セッションの応答が今さら届く → 捨てる。
    await act(async () => {
      resolveStale?.(staleDraft);
    });

    expect(result.current.preview).toBeNull();
    expect(result.current.previewStatus).toBe("idle");
  });

  it("新しい相談を開始すると前セッションの下書きは消える (揮発)", async () => {
    // 無いと: 前セッションの下書きが新セッションの右ペインに残り、「確定すると
    //         関係ない困りごとが保存される」誤操作の入口になる。
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(previewExtraction).mockResolvedValue(emptyDraft);
    const { result } = setup();
    await act(async () => {
      await result.current.startConsultation();
    });
    await sendTimes(result, ["1 回目", "2 回目"]);
    await waitFor(() => expect(result.current.preview).not.toBeNull());

    vi.mocked(startNewConsultation).mockResolvedValue(session({ id: "s2", messages: [] }));
    await act(async () => {
      await result.current.startConsultation();
    });

    expect(result.current.preview).toBeNull();
    expect(result.current.previewStatus).toBe("idle");
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

    await act(async () => {
      await result.current.reset();
    });

    await waitFor(() => expect(result.current.session).toBeNull());
    expect(result.current.problems).toEqual([]);
    expect(result.current.draftMessage).toBe("");
  });
});

describe("[L1] useConsultation — 抽出の失敗をユーザーに見せる (#183)", () => {
  // ここは従来経路 (プレビュー無効環境 = BFF 未結線の real) の仕様。
  beforeEach(() => {
    apiFlags.previewSupported = false;
  });

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

describe("[単体] useConsultation — 副作用ツールの承認 (#82 / G1 / dialogue-session.mdx §5.9)", () => {
  const approvalReply = {
    message: {
      id: "a-appr",
      role: "assistant" as const,
      text: "「send_reply」を実行するには承認が必要です。実行してよろしいですか？",
      createdAt: "2026-01-01",
    },
    approval: { id: "appr-1", description: "「send_reply」を実行するには承認が必要です。" },
  };

  async function startAndAskForApproval() {
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(sendMessage).mockResolvedValue(approvalReply);
    const { result } = setup();
    await act(async () => await result.current.startConsultation());
    act(() => result.current.setDraftMessage("この件、返信しておいて"));
    await act(async () => await result.current.sendDraftMessage());
    return result;
  }

  it("応答に載ってきた承認要求を保持し、承認すると要求を閉じて結果を会話に残す", async () => {
    // 無いと: 承認要求を受け取っても状態に入らず (= カードが出ず)、あるいは押しても
    //         要求が残り続ける退行が通る。どちらも「返事は出ている」ので画面は
    //         正常に見え、サーバだけが承認待ちで止まる。
    const result = await startAndAskForApproval();
    expect(result.current.pendingApproval?.id).toBe("appr-1");

    vi.mocked(respondToApproval).mockResolvedValue({
      id: "a-done",
      role: "assistant",
      text: "[stub] Reply sent to team@example.com.",
      createdAt: "2026-01-01",
    });
    await act(async () => await result.current.respondToPendingApproval(true));

    expect(respondToApproval).toHaveBeenCalledWith("appr-1", true);
    expect(result.current.pendingApproval).toBeNull();
    expect(result.current.session?.messages.at(-1)?.text).toContain("Reply sent");
  });

  it("却下もサーバへ送る (画面から消すだけにしない)", async () => {
    // 無いと: 却下を送らずカードだけ閉じる実装でもテストが通り、サーバ側の承認待ち
    //         (checkpoint) が宙に浮いたまま残る。
    const result = await startAndAskForApproval();

    vi.mocked(respondToApproval).mockResolvedValue({
      id: "a-cancel",
      role: "assistant",
      text: "操作はキャンセルされました。他にご用件はありますか？",
      createdAt: "2026-01-01",
    });
    await act(async () => await result.current.respondToPendingApproval(false));

    expect(respondToApproval).toHaveBeenCalledWith("appr-1", false);
    expect(result.current.pendingApproval).toBeNull();
    expect(result.current.session?.messages.at(-1)?.text).toContain("キャンセル");
  });

  it("送信に失敗したら要求を消さない (もう一度押せる)", async () => {
    // 無いと: 通信失敗でカードだけ消え、「却下したつもりでサーバには届いていない」
    //         状態が画面から見えなくなる (ADR 0018 の無音失敗)。
    vi.spyOn(console, "error").mockImplementation(() => {});
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockRejectedValue(new Error("boom"));

    await act(async () => await result.current.respondToPendingApproval(false));

    expect(result.current.pendingApproval?.id).toBe("appr-1");
    expect(result.current.actionError).toContain("実行されていません");
  });

  it("承認の ack を失ったときは「実行されていない」と断定しない", async () => {
    // 無いと: 承認 (approved=true) の応答が返らなかっただけで「操作はまだ実行されて
    //         いません」と断定する文面が出る。`/approve` は冪等ではないので、サーバが
    //         実行してから ack が落ちた場合と区別できない — 断定すると、実際には送信
    //         済みのメールをユーザーがもう一度送る判断をしうる (PR #416 judge major-2)。
    vi.spyOn(console, "error").mockImplementation(() => {});
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockRejectedValue(new Error("boom"));

    await act(async () => await result.current.respondToPendingApproval(true));

    expect(result.current.pendingApproval?.id).toBe("appr-1"); // 押し直せる
    expect(result.current.actionError).toContain("実行されたか確認できませんでした");
    expect(result.current.actionError).not.toContain("まだ実行されていません");
  });

  it("受け付けられない承認 (ApprovalExpired) はカードを閉じ、実行の有無は断定せずに伝える", async () => {
    // 無いと (1): 失効した承認 (ai-agent の TTL 1h / 再起動 / checkpoint 消失) を押しても
    //         「通信状況を確認して再試行」が出続け、再試行は決して成功しないので
    //         カードが閉じられない。承認も却下もできず、次の発話も送れない
    //         行き止まりになる (judge major-1)。
    // 無いと (2): 「期限切れです。操作は実行されていません」と断定する文面が通る。
    //         レコードが消えた理由は 404 からは分からず、「承認が実行されたあとに
    //         レコードだけ消えた」可能性が残る。断定すると、送信済みのメールを
    //         ユーザーがもう一度送る判断をしうる (judge / Codex P1)。
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockRejectedValue(new ApprovalExpired());

    await act(async () => await result.current.respondToPendingApproval(true));

    expect(result.current.pendingApproval).toBeNull();
    expect(result.current.actionError).toContain("もう受け付けられません");
    // **処理済み (409) の文面と混同しない** — こちらは実行の有無を言えない側 (#82)
    expect(result.current.actionError).not.toContain("すでに処理済み");
    expect(result.current.actionError).not.toContain("実行されていません");
    expect(result.current.actionError).toContain("会話を続けて");
  });

  it.each([
    { status: "approved" as const, expected: "すでに承認済み" },
    { status: "rejected" as const, expected: "すでに却下済み" },
  ])(
    "すでに処理済みの承認 (status=$status) はカードを閉じ、受け付けられた決定を伝える",
    async ({ status, expected }) => {
      // 無いと: 二重送信が期限切れ (ApprovalExpired) と同じ文面に落ち、UI は
      //         「もう受け付けられません (期限切れか、記録が失われています)」しか
      //         言えない。契約を 409 + 現在状態に分けた意味 (#82 / PO 裁定
      //         2026-08-15 B 案) がユーザーに届かず、**却下済み (確実に未実行) まで
      //         「実行されたか確認してください」**に落ちる。
      const result = await startAndAskForApproval();
      vi.mocked(respondToApproval).mockRejectedValue(new ApprovalAlreadyProcessed(status));

      await act(async () => await result.current.respondToPendingApproval(true));

      // 再試行しても永久に 409 なのでカードは閉じる (期限切れと同じ扱い)
      expect(result.current.pendingApproval).toBeNull();
      expect(result.current.actionError).toContain(expected);
      // 曖昧な期限切れ文面に混ざっていないこと
      expect(result.current.actionError).not.toContain("もう受け付けられません");
    },
  );

  it("承認済みの 409 は「実行されました」と断定しない", async () => {
    // 無いと (PR #430 Codex P1): ai-agent は承認の記録を**実行の前**に書くので、
    //         記録直後にプロセスが落ちると「approved なのに未実行」のレコードが残る。
    //         その ID を再送した人に「操作は実行されました」と言うと、**実際には
    //         送られていないメールを送ったと信じさせる**。断定してよいのは却下側だけ。
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockRejectedValue(new ApprovalAlreadyProcessed("approved"));

    await act(async () => await result.current.respondToPendingApproval(true));

    expect(result.current.actionError).not.toContain("実行されました");
    // 断定しない代わりに、確かめる導線は必ず出す (黙って閉じない)
    expect(result.current.actionError).toContain("会話履歴");
  });

  it("却下済みの 409 は「実行されていません」と言い切る", async () => {
    // 無いと: 却下は「実行しない」を受け付けた状態で、この経路でツールが呼ばれることは
    //         無い。ここまで曖昧にすると、ユーザーは実行されていない操作のために
    //         会話を遡って確認させられる (409 に分けた価値が消える)。
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockRejectedValue(new ApprovalAlreadyProcessed("rejected"));

    await act(async () => await result.current.respondToPendingApproval(false));

    expect(result.current.actionError).toContain("実行されていません");
  });

  it("すでに処理済みの承認を抱えたまま次の発話を送ると、結果を伝えつつ発話が送信される", async () => {
    // 無いと: 破棄のための却下が 409 で失敗する経路が期限切れと同じ文面に落ち、
    //         「もう受け付けられません (記録が失われています)」だけが出る (#82)。
    //         直前に自分が承認した操作の行方すら案内されない。発話自体を止めないのは
    //         期限切れと同じ理由 (この ID への再試行は永久に成功しない)。
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockRejectedValue(new ApprovalAlreadyProcessed("approved"));
    vi.mocked(sendMessage).mockResolvedValue({
      message: { id: "a-3", role: "assistant", text: "受け止めました", createdAt: "2026-01-01" },
      approval: null,
    });

    act(() => result.current.setDraftMessage("やっぱり別の話をしたい"));
    await act(async () => await result.current.sendDraftMessage());

    expect(result.current.pendingApproval).toBeNull();
    expect(sendMessage).toHaveBeenLastCalledWith("s1", "やっぱり別の話をしたい");
    expect(result.current.actionError).toContain("すでに承認済み");
    expect(result.current.actionError).not.toContain("実行されました");
  });

  it("受け付けられない承認を抱えたまま次の発話を送ると、カードが閉じて発話が送信される", async () => {
    // 無いと: 却下が 404 で失敗するたびに「発話を送らずカードを残す」経路に落ち、その
    //         会話は二度と先へ進めなくなる。この ID へ却下を送り直しても永久に 404 なので、
    //         止める理由が無い (judge major-1 の機械化文)。
    //         なお「何も実行せずに解放された」とは限らない (処理済みでも 404 / Codex P1)。
    //         止めない理由は「実行されていないから」ではなく「再試行が成功しないから」。
    const result = await startAndAskForApproval();
    expect(result.current.pendingApproval).not.toBeNull();

    vi.mocked(respondToApproval).mockRejectedValue(new ApprovalExpired());
    vi.mocked(sendMessage).mockResolvedValue({
      message: { id: "a-3", role: "assistant", text: "受け止めました", createdAt: "2026-01-01" },
      approval: null,
    });
    act(() => result.current.setDraftMessage("やっぱり別の話をしたい"));
    await act(async () => await result.current.sendDraftMessage());

    expect(result.current.pendingApproval).toBeNull();
    expect(sendMessage).toHaveBeenLastCalledWith("s1", "やっぱり別の話をしたい");
    expect(result.current.actionError).toContain("もう受け付けられません");
    expect(result.current.actionError).not.toContain("実行されていません");
    expect(result.current.session?.messages.map((m) => m.text)).toEqual(
      expect.arrayContaining(["やっぱり別の話をしたい", "受け止めました"]),
    );
    expect(result.current.draftMessage).toBe("");
  });

  it("新規相談で承認要求を捨てるときも、サーバへ却下を送る", async () => {
    // 無いと: 画面から消すだけになり、ai-agent 側の ApprovalRecord / checkpoint が
    //         pending のまま残る (in-memory 構成には TTL が無い / judge minor-3)。
    //         画面上は新しい相談が始まるので、無いと気づけない。
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockResolvedValue({
      id: "a-cancel",
      role: "assistant",
      text: "操作はキャンセルされました。他にご用件はありますか？",
      createdAt: "2026-01-01",
    });
    vi.mocked(startNewConsultation).mockResolvedValue(session({ id: "s2" }));

    await act(async () => await result.current.startConsultation());

    expect(respondToApproval).toHaveBeenCalledWith("appr-1", false);
    expect(result.current.pendingApproval).toBeNull();
    // 捨てた却下の応答は**新しい相談には出さない** (別の会話の結果を持ち込まない)
    expect(result.current.session?.id).toBe("s2");
    expect(result.current.session?.messages.map((m) => m.text)).not.toContain(
      "操作はキャンセルされました。他にご用件はありますか？",
    );
  });

  it("却下が届かなくても新規相談は始まる (fire-and-forget)", async () => {
    // 無いと: 却下の通信失敗が新規相談を巻き込んで止める。捨てる側の通信は
    //         ユーザーの操作 (新しい相談を始める) を人質に取ってはいけない。
    vi.spyOn(console, "error").mockImplementation(() => {});
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockRejectedValue(new Error("boom"));
    vi.mocked(startNewConsultation).mockResolvedValue(session({ id: "s2" }));

    await act(async () => await result.current.startConsultation());

    expect(result.current.session?.id).toBe("s2");
    expect(result.current.pendingApproval).toBeNull();
  });

  it("reset (ログアウト) で承認要求を捨てるときも、サーバへ却下を送る", async () => {
    // 無いと: ログアウト経路だけ却下が漏れ、承認待ちがサーバに残り続ける (judge minor-3)。
    const result = await startAndAskForApproval();
    vi.mocked(respondToApproval).mockResolvedValue({
      id: "a-cancel",
      role: "assistant",
      text: "操作はキャンセルされました。",
      createdAt: "2026-01-01",
    });

    // reset は却下の完了を返す (ログアウトはこれを待ってからリダイレクトする)。
    await act(async () => {
      await result.current.reset();
    });

    expect(respondToApproval).toHaveBeenCalledWith("appr-1", false);
    expect(result.current.pendingApproval).toBeNull();
  });

  it("承認せずに次の発話を送ると、サーバへも却下を送ってから送信する", async () => {
    // 無いと: ローカルの承認カードを消すだけの実装が通り、ai-agent 側の承認待ち
    //         (ApprovalRecord / checkpoint) が pending のまま残る。in-memory 構成には
    //         TTL が無いので、繰り返すほど解放されない保持領域が増える (PR #416 Codex P2)。
    //         画面上は「カードが消えて会話が進む」ので、無いと気づけない。
    const result = await startAndAskForApproval();
    expect(result.current.pendingApproval).not.toBeNull();

    vi.mocked(respondToApproval).mockResolvedValue({
      id: "a-cancel",
      role: "assistant",
      text: "操作はキャンセルされました。他にご用件はありますか？",
      createdAt: "2026-01-01",
    });
    vi.mocked(sendMessage).mockResolvedValue({
      message: { id: "a-3", role: "assistant", text: "受け止めました", createdAt: "2026-01-01" },
      approval: null,
    });
    act(() => result.current.setDraftMessage("やっぱりやめておく"));
    await act(async () => await result.current.sendDraftMessage());

    expect(respondToApproval).toHaveBeenCalledWith("appr-1", false);
    expect(result.current.pendingApproval).toBeNull();
    // 却下の結果も新しい発話も両方残る (却下の応答を後続の楽観更新で踏み潰さない)
    expect(result.current.session?.messages.map((m) => m.text)).toEqual(
      expect.arrayContaining([
        "操作はキャンセルされました。他にご用件はありますか？",
        "やっぱりやめておく",
        "受け止めました",
      ]),
    );
  });

  it("却下が届かなければ発話を送らず、要求も入力も残す", async () => {
    // 無いと: 却下がサーバに届いていないのにカードだけ消えて会話が進む。ユーザーは
    //         承認待ちが残っていることも、却下が失敗したことも知らないまま先へ行く。
    vi.spyOn(console, "error").mockImplementation(() => {});
    const result = await startAndAskForApproval();

    vi.mocked(respondToApproval).mockRejectedValue(new Error("boom"));
    act(() => result.current.setDraftMessage("もう一言"));
    await act(async () => await result.current.sendDraftMessage());

    expect(sendMessage).toHaveBeenCalledTimes(1); // 承認要求を出した最初の 1 回だけ
    expect(result.current.actionError).toContain("実行されていません");
    expect(result.current.pendingApproval?.id).toBe("appr-1");
    expect(result.current.draftMessage).toBe("もう一言");
  });

  it("承認できない応答 (承認 ID 欠落) は通信失敗と別の案内を出す", async () => {
    // 無いと: 「もう一度お試しください」だけが出て、何度やっても同じところで止まる
    //         (通信の問題ではないため)。承認不要の応答との区別も画面から消える。
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(startNewConsultation).mockResolvedValue(session());
    vi.mocked(sendMessage).mockRejectedValue(new ApprovalRequestUnusable());
    const { result } = setup();
    await act(async () => await result.current.startConsultation());

    act(() => result.current.setDraftMessage("この件、返信しておいて"));
    await act(async () => await result.current.sendDraftMessage());

    expect(result.current.actionError).toContain("承認できない応答");
    expect(result.current.pendingApproval).toBeNull();
  });
});
