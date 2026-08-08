import { seedProblems } from "../../mockApi";
import type { Problem } from "../../api";

/**
 * spec プレビュー用の固定 Problem 群。
 * mockApi の seed をそのまま流用して fixture の二重管理を避ける（PR #44 レビュー指摘）。
 * seedProblems() は毎回新しい配列を返し store を共有しないので、プレビューは決め打ちのまま。
 * 先頭は再出現(3 mention)の p-career で、ProblemDetailSpecPreview の主役になる。
 */
export const sampleProblems: Problem[] = seedProblems();
