import type { inferRouterInputs } from "@trpc/server";
import { TRPCClientError } from "@trpc/client";
import * as mock from "../mockApi";
import type { ChatMessage, ExtractionResult, Problem, ProblemFilter, TriageInput } from "./types";
import { trpc } from "../trpc/client";
import type { AppRouter } from "../trpc/client";
import { useMock } from "./http";

/**
 * Problem / Mention の api 層（Phase C で mock→real を結線）。
 *
 * consultation.ts / history.ts と同じ `VITE_USE_MOCK` 分岐。real は BFF の
 * `consultation.extract` / `problem.*`（Phase B）を tRPC で叩く。
 */

/** BFF `problem.triage` の入力（discriminatedUnion）。UI のフラット TriageInput をこれに写す。 */
type BffTriageInput = inferRouterInputs<AppRouter>["problem"]["triage"];

/**
 * UI のフラット TriageInput → BFF の discriminated union へ変換する。
 * relink/merge のフィールド名が異なるためここで吸収する（それ以外は素通し）。
 */
function toBffTriageInput(input: TriageInput): BffTriageInput {
  switch (input.action) {
    case "relink":
      return {
        action: "relink",
        mentionId: input.mentionId ?? "",
        fromProblemId: input.problemId,
        toProblemId: input.targetProblemId ?? "",
      };
    case "merge":
      // 向きが逆: mock は problemId=生存 / targetProblemId=吸収され削除。
      // BFF は sourceProblemId=削除 / targetProblemId=生存。→ 入れ替える。
      return {
        action: "merge",
        sourceProblemId: input.targetProblemId ?? "",
        targetProblemId: input.problemId,
      };
    case "editTheme":
      return { action: "editTheme", problemId: input.problemId, theme: input.theme ?? "未分類" };
    case "editTitle":
      return { action: "editTitle", problemId: input.problemId, title: input.title ?? "" };
    default:
      // resolve / shelve / reopen / dismiss
      return { action: input.action, problemId: input.problemId };
  }
}

function isNotFound(err: unknown): boolean {
  return err instanceof TRPCClientError && err.data?.code === "NOT_FOUND";
}

/**
 * 抽出の失敗理由 (#183)。BFF は機械可読な token を message に載せて返す。
 * ユーザー向けの文面は UI 側 (extract-review.mdx の持ち場) で決める。
 */
export type ExtractFailureKind =
  | "session-missing"
  | "llm-parse-failed"
  | "upstream-failed"
  | "unknown";

export class ExtractFailed extends Error {
  // パラメータプロパティ短縮記法は erasableSyntaxOnly で使えないため明示代入する。
  readonly kind: ExtractFailureKind;

  constructor(kind: ExtractFailureKind) {
    super(`extract failed: ${kind}`);
    this.name = "ExtractFailed";
    this.kind = kind;
  }
}

const EXTRACT_FAILURE_KINDS: ExtractFailureKind[] = [
  "session-missing",
  "llm-parse-failed",
  "upstream-failed",
];

/**
 * 吐き出し全文を Mention[] に抽出する。
 *
 * **会話をこちらから渡す** (#183): ai-agent のセッション履歴はプロセスメモリなので、
 * scale-to-zero・スケールアウト・リビジョン差し替えのいずれでも消える。それに依存すると
 * 「対話はできたのに抽出だけ失敗する」が起きる。会話の真実は画面が持っているので送る。
 */
export async function extractMentions(
  sessionId: string,
  messages: ChatMessage[],
): Promise<ExtractionResult> {
  if (useMock) return mock.extractMentions(sessionId);
  try {
    return await trpc.consultation.extract.mutate({
      sessionId,
      messages: messages.map((m) => ({ role: m.role, text: m.text })),
    });
  } catch (err) {
    // 理由を失わない。呼び出し側が文面と復帰導線を出し分けられるようにする。
    const token = err instanceof TRPCClientError ? String(err.message) : "";
    const kind = EXTRACT_FAILURE_KINDS.find((k) => k === token) ?? "unknown";
    console.error(`[extractMentions] failed kind=${kind}`, err);
    throw new ExtractFailed(kind);
  }
}

export async function loadProblems(filter?: ProblemFilter): Promise<Problem[]> {
  if (useMock) return mock.loadProblems(filter);
  return await trpc.problem.list.query(filter);
}

export async function loadProblem(id: string): Promise<Problem | null> {
  if (useMock) return mock.loadProblem(id);
  try {
    return await trpc.problem.get.query({ id });
  } catch (err) {
    // BFF は未存在を NOT_FOUND で返す。UI 契約（Problem | null）に合わせて null に。
    if (isNotFound(err)) return null;
    throw err;
  }
}

export async function triageProblem(input: TriageInput): Promise<Problem | null> {
  if (useMock) return mock.triageProblem(input);
  // BFF は影響を受けた Problem[] を返す。UI 契約（Problem | null）に合わせて先頭 or null に。
  const { problems } = await trpc.problem.triage.mutate(toBffTriageInput(input));
  return problems[0] ?? null;
}

export async function createProblemPlan(problemId: string): Promise<Problem | null> {
  if (useMock) return mock.createProblemPlan(problemId);
  try {
    return await trpc.problem.createPlan.mutate({ problemId });
  } catch (err) {
    if (isNotFound(err)) return null;
    throw err;
  }
}

// テスト用に純粋変換だけ公開する（trpc を mock せず写像を検証するため）。
export { toBffTriageInput };
