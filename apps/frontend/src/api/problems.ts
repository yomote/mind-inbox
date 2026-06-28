import * as mock from "../mockApi";
import type { ExtractionResult, Problem, ProblemFilter, TriageInput } from "../mockApi";

/**
 * Problem / Mention の api 層。
 *
 * Phase D は mock 先行。BFF の `consultation.extract` / `problem.*` はまだ無い（Phase B）ため、
 * ここは常に mockApi へ委譲する。Phase C で `VITE_USE_MOCK=false` 分岐と tRPC 呼び出しを足す。
 */

export async function extractMentions(sessionId: string): Promise<ExtractionResult> {
  return mock.extractMentions(sessionId);
}

export async function loadProblems(filter?: ProblemFilter): Promise<Problem[]> {
  return mock.loadProblems(filter);
}

export async function loadProblem(id: string): Promise<Problem | null> {
  return mock.loadProblem(id);
}

export async function triageProblem(input: TriageInput): Promise<Problem | null> {
  return mock.triageProblem(input);
}

export async function createProblemPlan(problemId: string): Promise<Problem | null> {
  return mock.createProblemPlan(problemId);
}
