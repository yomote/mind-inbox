/**
 * ai-agent サービスとの I/O 契約 (BFF 側の真実)。
 *
 * ここが zod なのは **機械比較できる形にしておくため**。素の TS `type` だと
 * L0 契約テスト (`npm run test:contract`) から JSON Schema を起こせず、ai-agent の
 * pydantic (`apps/services/ai-agent/app/schemas.py`) が片側だけ変わっても気づけない。
 *
 * 型は `z.infer` で導出する (二重定義にしない)。`aiAgentClient.ts` は従来どおり型名を
 * re-export しているので、`import type { ChatRequest } from "../clients/aiAgentClient"`
 * は今までどおり動く。
 *
 * 表記: BFF 側は camelCase。wire (ai-agent) 側は snake_case。この差は契約テストが
 * 機械的に正規化して吸収するので、ここでは BFF の流儀 (camelCase) で書く。
 * 実際の snake_case 変換は `aiAgentClient.ts` の各 fetch が行う。
 */
import { z } from "zod";
import {
  ActionPlanSchema,
  OrganizedResultSchema,
  ProblemStatusSchema,
  ThemeSchema,
} from "../trpc/domain";

// ── /chat ────────────────────────────────────────────────────────────────────

export const ChatRequestSchema = z.object({
  sessionId: z.string(),
  message: z.string(),
});
export type ChatRequest = z.infer<typeof ChatRequestSchema>;

export const ChatResponseSchema = z.object({
  reply: z.string(),
  requiresApproval: z.boolean(),
  approvalRequestId: z.string().nullable(),
  citations: z.array(z.string()),
});
export type ChatResponse = z.infer<typeof ChatResponseSchema>;

// ── /chat/stream (SSE イベント / ADR 0024) ───────────────────────────────────
//
// 契約の真実は ai-agent の pydantic (ChatStreamDelta / Done / Error)。ここはその鏡写しで、
// L0 契約テストが両者を突き合わせる。`ChatStreamDone.response` が ChatResponse を内包する
// ので、再帰比較が効く箇所でもある。

export const ChatStreamDeltaSchema = z.object({
  type: z.literal("delta"),
  text: z.string(),
});
export type ChatStreamDelta = z.infer<typeof ChatStreamDeltaSchema>;

export const ChatStreamDoneSchema = z.object({
  type: z.literal("done"),
  response: ChatResponseSchema,
});
export type ChatStreamDone = z.infer<typeof ChatStreamDoneSchema>;

export const ChatStreamErrorSchema = z.object({
  type: z.literal("error"),
  message: z.string(),
});
export type ChatStreamError = z.infer<typeof ChatStreamErrorSchema>;

// ── /organize ────────────────────────────────────────────────────────────────

export const OrganizeRequestSchema = z.object({
  sessionId: z.string(),
});
export type OrganizeRequest = z.infer<typeof OrganizeRequestSchema>;

/** ai-agent の OrganizeResponse は tRPC の整理結果と同一形 (domain.ts が真実) */
export const OrganizeResponseSchema = OrganizedResultSchema;
export type OrganizeResponse = z.infer<typeof OrganizeResponseSchema>;

// ── /plan ────────────────────────────────────────────────────────────────────

export const PlanRequestSchema = z.object({
  summary: z.string(),
  emotions: z.array(z.string()),
  priorities: z.array(z.string()),
});
export type PlanRequest = z.infer<typeof PlanRequestSchema>;

/** ai-agent の PlanResponse は ActionPlan と同一形 (domain.ts が真実) */
export const PlanResponseSchema = ActionPlanSchema;
export type PlanResponse = z.infer<typeof PlanResponseSchema>;

// ── /approve ─────────────────────────────────────────────────────────────────

export const ApproveRequestSchema = z.object({
  approvalRequestId: z.string(),
  approved: z.boolean(),
});
export type ApproveRequest = z.infer<typeof ApproveRequestSchema>;

export const ApproveResponseSchema = z.object({
  reply: z.string(),
});
export type ApproveResponse = z.infer<typeof ApproveResponseSchema>;

// ── /extract (Problem 中心 2層モデル / ADR 0007・0012) ────────────────────────

/** グルーピングの突き合わせ候補（BFF の Problem リポジトリから渡す / ADR 0012） */
export const ExistingProblemRefSchema = z.object({
  id: z.string(),
  title: z.string(),
  theme: ThemeSchema,
  summary: z.string(),
  mentionCount: z.number().int().nonnegative(),
  status: ProblemStatusSchema,
});
export type ExistingProblemRef = z.infer<typeof ExistingProblemRefSchema>;

export const ExtractRequestSchema = z.object({
  sessionId: z.string(),
  existingProblems: z.array(ExistingProblemRefSchema),
});
export type ExtractRequest = z.infer<typeof ExtractRequestSchema>;
