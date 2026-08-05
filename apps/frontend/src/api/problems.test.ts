import { TRPCClientError } from "@trpc/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../trpc/client", () => ({
  trpc: {
    consultation: { extract: { mutate: vi.fn() } },
    problem: {
      list: { query: vi.fn() },
      get: { query: vi.fn() },
      triage: { mutate: vi.fn() },
      createPlan: { mutate: vi.fn() },
    },
  },
}));

import { trpc } from "../trpc/client";
import { loadProblem, toBffTriageInput, triageProblem } from "./problems";

beforeEach(() => {
  vi.clearAllMocks();
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

  it("merge: problemId を sourceProblemId に付け替える", () => {
    expect(
      toBffTriageInput({ action: "merge", problemId: "p1", targetProblemId: "p2" }),
    ).toEqual({ action: "merge", sourceProblemId: "p1", targetProblemId: "p2" });
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
