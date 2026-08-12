import { TRPCClientError } from "@trpc/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../trpc/client", () => ({
  trpc: {
    consultation: { extract: { mutate: vi.fn() }, preview: { mutate: vi.fn() } },
    problem: {
      list: { query: vi.fn() },
      get: { query: vi.fn() },
      triage: { mutate: vi.fn() },
      createPlan: { mutate: vi.fn() },
    },
  },
}));

import { trpc } from "../trpc/client";
import {
  ExtractFailed,
  commitPreview,
  loadProblem,
  previewExtraction,
  toBffTriageInput,
  triageProblem,
} from "./problems";
import { getStubbedResponse, reportStubbedResponse } from "./stubStatus";

beforeEach(() => {
  vi.clearAllMocks();
  reportStubbedResponse(undefined);
});

// ---- toBffTriageInput（UI フラット → BFF discriminated union の写像）--------

describe("[L1] toBffTriageInput", () => {
  it("relink: problemId/targetProblemId を from/toProblemId に付け替える", () => {
    // 無いと: フロントのフラット名のまま BFF に渡り relink が silently 失敗する退行が静かに通る
    expect(
      toBffTriageInput({
        action: "relink",
        problemId: "p1",
        targetProblemId: "p2",
        mentionId: "m1",
      }),
    ).toEqual({ action: "relink", mentionId: "m1", fromProblemId: "p1", toProblemId: "p2" });
  });

  it("merge: 生存/削除の向きを反転する（mock survivor=problemId → BFF targetProblemId）", () => {
    // 無いと: mock(problemId=生存)と BFF(targetProblemId=生存)の向きの取り違えが静かに通り、
    //         real 分岐で「残るはずの Problem」が削除される
    expect(
      toBffTriageInput({ action: "merge", problemId: "survivor", targetProblemId: "absorbed" }),
    ).toEqual({ action: "merge", sourceProblemId: "absorbed", targetProblemId: "survivor" });
  });

  it("editTheme / 状態遷移は素通し", () => {
    expect(toBffTriageInput({ action: "editTheme", problemId: "p1", theme: "心と体" })).toEqual({
      action: "editTheme",
      problemId: "p1",
      theme: "心と体",
    });
    expect(toBffTriageInput({ action: "resolve", problemId: "p1" })).toEqual({
      action: "resolve",
      problemId: "p1",
    });
  });
});

// ---- real 分岐の戻り値マッピング -------------------------------------------

describe("[L2] problems api — real 分岐", () => {
  it("triageProblem: BFF の {problems:[p]} を先頭 Problem に写す", async () => {
    // 無いと: BFF の {problems:[]} 配列返しと UI の Problem|null 契約の齟齬が静かに通る
    const p = { id: "p1" } as never;
    vi.mocked(trpc.problem.triage.mutate).mockResolvedValue({ problems: [p] } as never);

    const res = await triageProblem({ action: "resolve", problemId: "p1" });
    expect(res).toBe(p);
    expect(trpc.problem.triage.mutate).toHaveBeenCalledWith({
      action: "resolve",
      problemId: "p1",
    });
  });

  it("triageProblem: dismiss の {problems:[]} を null に写す", async () => {
    vi.mocked(trpc.problem.triage.mutate).mockResolvedValue({ problems: [] } as never);
    expect(await triageProblem({ action: "dismiss", problemId: "p1" })).toBeNull();
  });

  it("loadProblem: NOT_FOUND を null に写す（詳細画面を壊さない）", async () => {
    const err = new TRPCClientError("Problem not found");
    Object.defineProperty(err, "data", { value: { code: "NOT_FOUND" } });
    vi.mocked(trpc.problem.get.query).mockRejectedValue(err);

    expect(await loadProblem("nope")).toBeNull();
  });

  it("loadProblem: NOT_FOUND 以外のエラーは再送出する", async () => {
    const err = new TRPCClientError("boom");
    Object.defineProperty(err, "data", { value: { code: "INTERNAL_SERVER_ERROR" } });
    vi.mocked(trpc.problem.get.query).mockRejectedValue(err);

    await expect(loadProblem("x")).rejects.toBe(err);
  });
});

// ---- 下書きプレビューの real 結線 (#187 / #283 / ADR 0039 D1・D3) ------------

const DRAFT_ITEM = {
  mention: { id: "men-1", statement: "転職しようか迷っている" },
  grouping: { kind: "existing", problemId: "prob-1" },
} as never;

describe("[単体] 下書きプレビューの real 結線", () => {
  it("previewExtraction は consultation.preview を叩く — 書き込み側 (extract) を呼ばない", async () => {
    // 無いと: プレビューが確定用の extract を叩き、まだ確認していない下書きが
    //         会話の途中で Problem として保存される (ADR 0039 D1「preview は書かない」の破壊)
    vi.mocked(trpc.consultation.preview.mutate).mockResolvedValue({ items: [] } as never);

    await previewExtraction("s1", [
      { id: "m1", role: "user", text: "転職しようかな" },
      { id: "m2", role: "assistant", text: "どのあたりが気になりますか" },
    ] as never);

    expect(trpc.consultation.preview.mutate).toHaveBeenCalledWith({
      sessionId: "s1",
      messages: [
        { role: "user", text: "転職しようかな" },
        { role: "assistant", text: "どのあたりが気になりますか" },
      ],
    });
    expect(trpc.consultation.extract.mutate).not.toHaveBeenCalled();
  });

  it("commitPreview は表示中の下書きを draft として送る — 会話を送って再抽出させない", async () => {
    // 無いと: 確定時に会話から抽出し直す経路に戻り、画面で確認した内容と違うものが
    //         保存されうる (抽出は非決定的 / ADR 0039 D3)
    vi.mocked(trpc.consultation.extract.mutate).mockResolvedValue({ items: [] } as never);

    await commitPreview("s1", [DRAFT_ITEM]);

    expect(trpc.consultation.extract.mutate).toHaveBeenCalledWith({
      sessionId: "s1",
      messages: [],
      draft: { items: [DRAFT_ITEM] },
    });
  });

  it("preview の stub フラグを警告ストアへ伝える (#146)", async () => {
    // 無いと: stub の下書きが本物の顔で右ペインに並び、確定して初めて [stub] に気づく
    vi.mocked(trpc.consultation.preview.mutate).mockResolvedValue({
      items: [],
      stubbed: true,
    } as never);

    await previewExtraction("s1", []);
    expect(getStubbedResponse()).toBe(true);
  });

  it("preview / commit の失敗理由を ExtractFailed の kind に翻訳する (#183)", async () => {
    // 無いと: 失敗の種別が message 文字列のまま画面へ流れ、復帰導線を出し分けられない
    vi.mocked(trpc.consultation.preview.mutate).mockRejectedValue(
      new TRPCClientError("llm-parse-failed"),
    );
    await expect(previewExtraction("s1", [])).rejects.toMatchObject({
      name: "ExtractFailed",
      kind: "llm-parse-failed",
    });

    vi.mocked(trpc.consultation.extract.mutate).mockRejectedValue(new TRPCClientError("boom"));
    const failure = await commitPreview("s1", [DRAFT_ITEM]).catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ExtractFailed);
    expect((failure as ExtractFailed).kind).toBe("unknown");
  });
});
