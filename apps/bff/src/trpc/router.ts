import { randomUUID } from "node:crypto";
import { initTRPC, TRPCError } from "@trpc/server";
import { z } from "zod";
import type { TrpcContext } from "./context";
import {
  approve as approveAiAgent,
  createPlan as createPlanAiAgent,
  extract as extractAiAgent,
  organize as organizeAiAgent,
  sendChatMessage,
} from "../clients/aiAgentClient";
import {
  ActionPlanSchema,
  HistoryItemSchema,
  OrganizedResultSchema,
} from "../repositories/historyRepository";
import type { ProblemRepository } from "../repositories/problemRepository";
import {
  ExtractionResultSchema,
  ProblemSchema,
  ProblemStatusSchema,
  ThemeSchema,
  type ExtractionResult,
  type Problem,
  type TriageAction,
} from "./domain";

const t = initTRPC.context<TrpcContext>().create();

const router = t.router;
const publicProcedure = t.procedure;

// ---- shared schemas --------------------------------------------------------

const ChatMessageSchema = z.object({
  id: z.string(),
  role: z.enum(["user", "assistant"]),
  text: z.string(),
  createdAt: z.string(),
});

const SessionSchema = z.object({
  id: z.string(),
  title: z.string(),
  messages: z.array(ChatMessageSchema),
});

const ChatReplySchema = z.object({
  reply: z.string(),
  requiresApproval: z.boolean(),
  approvalRequestId: z.string().nullable(),
  citations: z.array(z.string()),
});

const ApproveResultSchema = z.object({
  reply: z.string(),
});

type ChatMessage = z.infer<typeof ChatMessageSchema>;

function nowIso(): string {
  return new Date().toISOString();
}

function deriveTitle(concern: string): string {
  const trimmed = concern.trim();
  if (trimmed.length === 0) return "相談セッション";
  return trimmed.length > 26 ? `${trimmed.slice(0, 26)}…` : trimmed;
}

// 空入力で開始した場合の AI からの初手 (new-consultation.mdx「空入力でも開始可能」/
// dialogue session.mdx「開始直後の挙動」)。受け止めトーンを崩さないこと。
const EMPTY_START_OPENER = "今日はどんなことが気になっていますか?思いつくままで大丈夫です。";

/**
 * ai-agent の抽出結果を Problem リポジトリに反映する。
 * new は新規 Problem を起こし、existing は既存に Mention を追記して mentionCount / lastMentionedAt を更新。
 * 状態遷移（reignited の reopen 等）は自動では行わず、事後トリアージに委ねる（ADR 0007）。
 */
async function materializeExtraction(
  result: ExtractionResult,
  repo: ProblemRepository,
): Promise<void> {
  for (const { mention, grouping } of result.items) {
    if (grouping.kind === "existing") {
      const existing = await repo.get(grouping.problemId);
      if (existing) {
        await repo.upsert({
          ...existing,
          mentions: [...existing.mentions, mention],
          mentionCount: grouping.mentionCount,
          lastMentionedAt: mention.createdAt,
        });
        continue;
      }
      // 既存が見つからない（候補集合との齟齬）→ 取りこぼさず新規として作る
    }
    // 新規 Problem（new、または existing だが候補が見つからないフォールバック）。
    // Mention は1件なので mentionCount は必ず 1（mentions.length と一致させる。
    // grouping.mentionCount は既存追記前提の値なのでここでは使わない）。
    const problem: Problem = {
      id: grouping.problemId,
      title: grouping.problemTitle,
      summary: mention.statement,
      theme: grouping.problemTheme,
      tags: mention.proposedTags,
      status: "open",
      mentions: [mention],
      mentionCount: 1,
      plans: [],
      createdAt: mention.createdAt,
      lastMentionedAt: mention.createdAt,
      resolvedAt: null,
      shelvedAt: null,
    };
    await repo.upsert(problem);
  }
}

function dedupe(values: string[]): string[] {
  return [...new Set(values)];
}

// ---- triage ----------------------------------------------------------------
// domain_model.md §4.2 / TriageActionSchema。分割は v1 では後回し。
// action ごとに必要な引数が違うため discriminatedUnion で受ける。

const TriageInputSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("resolve"), problemId: z.string().min(1) }),
  z.object({ action: z.literal("shelve"), problemId: z.string().min(1) }),
  z.object({ action: z.literal("reopen"), problemId: z.string().min(1) }),
  z.object({ action: z.literal("dismiss"), problemId: z.string().min(1) }),
  z.object({
    action: z.literal("editTheme"),
    problemId: z.string().min(1),
    theme: ThemeSchema,
  }),
  z.object({
    action: z.literal("editTitle"),
    problemId: z.string().min(1),
    title: z.string().min(1),
  }),
  z.object({
    action: z.literal("relink"),
    mentionId: z.string().min(1),
    fromProblemId: z.string().min(1),
    toProblemId: z.string().min(1),
  }),
  z.object({
    action: z.literal("merge"),
    sourceProblemId: z.string().min(1),
    targetProblemId: z.string().min(1),
  }),
]);
type TriageInput = z.infer<typeof TriageInputSchema>;

// drift ガード: TriageInput の action 集合が domain.ts の TriageActionSchema と一致することを
// コンパイル時に強制する（どちらか一方に action を足し忘れると型エラーになる）。
type AssertEqual<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
const _triageActionsInSync: AssertEqual<TriageInput["action"], TriageAction> = true;
void _triageActionsInSync;

async function requireProblem(repo: ProblemRepository, id: string): Promise<Problem> {
  const problem = await repo.get(id);
  if (!problem) {
    throw new TRPCError({ code: "NOT_FOUND", message: `Problem not found: ${id}` });
  }
  return problem;
}

/** mentions 変更後に派生フィールド（mentionCount / lastMentionedAt）を再計算する。 */
function withDerived(problem: Problem): Problem {
  const mentionCount = problem.mentions.length;
  const lastMentionedAt = problem.mentions.reduce(
    (max, m) => (m.createdAt > max ? m.createdAt : max),
    problem.mentions[0]?.createdAt ?? problem.lastMentionedAt,
  );
  return { ...problem, mentionCount, lastMentionedAt };
}

/** トリアージ操作を適用し、影響を受けた Problem を返す（dismiss / merge で消えたものは含めない）。 */
async function applyTriage(input: TriageInput, repo: ProblemRepository): Promise<Problem[]> {
  switch (input.action) {
    case "resolve": {
      const p = await requireProblem(repo, input.problemId);
      return [await repo.upsert({ ...p, status: "resolved", resolvedAt: nowIso() })];
    }
    case "shelve": {
      const p = await requireProblem(repo, input.problemId);
      return [await repo.upsert({ ...p, status: "shelved", shelvedAt: nowIso() })];
    }
    case "reopen": {
      const p = await requireProblem(repo, input.problemId);
      return [await repo.upsert({ ...p, status: "open", resolvedAt: null, shelvedAt: null })];
    }
    case "editTheme": {
      const p = await requireProblem(repo, input.problemId);
      return [await repo.upsert({ ...p, theme: input.theme })];
    }
    case "editTitle": {
      const p = await requireProblem(repo, input.problemId);
      return [await repo.upsert({ ...p, title: input.title })];
    }
    case "dismiss": {
      await requireProblem(repo, input.problemId);
      await repo.remove(input.problemId);
      return [];
    }
    case "relink": {
      if (input.fromProblemId === input.toProblemId) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "fromProblemId と toProblemId が同一です",
        });
      }
      const from = await requireProblem(repo, input.fromProblemId);
      const to = await requireProblem(repo, input.toProblemId);
      const mention = from.mentions.find((m) => m.id === input.mentionId);
      if (!mention) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: `Mention not found in ${input.fromProblemId}: ${input.mentionId}`,
        });
      }
      const affected: Problem[] = [];
      const remaining = from.mentions.filter((m) => m.id !== input.mentionId);
      if (remaining.length === 0) {
        // 種の Mention が抜けて空になった Problem は削除する（mentions.min(1) を保つ）
        await repo.remove(from.id);
      } else {
        affected.push(await repo.upsert(withDerived({ ...from, mentions: remaining })));
      }
      const moved = { ...mention, problemId: to.id };
      affected.push(await repo.upsert(withDerived({ ...to, mentions: [...to.mentions, moved] })));
      return affected;
    }
    case "merge": {
      if (input.sourceProblemId === input.targetProblemId) {
        throw new TRPCError({ code: "BAD_REQUEST", message: "source と target が同一です" });
      }
      const source = await requireProblem(repo, input.sourceProblemId);
      const target = await requireProblem(repo, input.targetProblemId);
      const movedMentions = source.mentions.map((m) => ({ ...m, problemId: target.id }));
      const merged = withDerived({
        ...target,
        mentions: [...target.mentions, ...movedMentions],
        tags: dedupe([...target.tags, ...source.tags]),
        plans: [...target.plans, ...source.plans],
      });
      await repo.remove(source.id);
      return [await repo.upsert(merged)];
    }
  }
}

// ---- health ----------------------------------------------------------------

const healthRouter = router({
  ping: publicProcedure.output(z.object({ ok: z.literal(true) })).query(() => {
    return { ok: true as const };
  }),
});

// ---- consultation ----------------------------------------------------------

const consultationRouter = router({
  start: publicProcedure
    .input(z.object({ concern: z.string() }))
    .output(z.object({ session: SessionSchema }))
    .mutation(async ({ input }) => {
      const sessionId = randomUUID();
      console.log(`[consultation.start] sessionId=${sessionId}`);

      // 空入力開始: ユーザー発話を作らず、AI の問いかけから始める。
      // 空文字を ai-agent に流さない (会話履歴にも空メッセージを残さない)。
      if (input.concern.trim().length === 0) {
        return {
          session: {
            id: sessionId,
            title: deriveTitle(input.concern),
            messages: [
              {
                id: randomUUID(),
                role: "assistant" as const,
                text: EMPTY_START_OPENER,
                createdAt: nowIso(),
              },
            ],
          },
        };
      }

      const chatRes = await sendChatMessage({
        sessionId,
        message: input.concern,
      });

      const messages: ChatMessage[] = [
        {
          id: randomUUID(),
          role: "user",
          text: input.concern,
          createdAt: nowIso(),
        },
        {
          id: randomUUID(),
          role: "assistant",
          text: chatRes.reply,
          createdAt: nowIso(),
        },
      ];

      return {
        session: {
          id: sessionId,
          title: deriveTitle(input.concern),
          messages,
        },
      };
    }),

  sendMessage: publicProcedure
    .input(
      z.object({
        sessionId: z.string().min(1),
        message: z.string().min(1),
      }),
    )
    .output(ChatReplySchema)
    .mutation(async ({ input }) => {
      console.log(`[consultation.sendMessage] sessionId=${input.sessionId}`);

      const chatRes = await sendChatMessage({
        sessionId: input.sessionId,
        message: input.message,
      });

      return {
        reply: chatRes.reply,
        requiresApproval: chatRes.requiresApproval,
        approvalRequestId: chatRes.approvalRequestId,
        citations: chatRes.citations,
      };
    }),

  organize: publicProcedure
    .input(z.object({ sessionId: z.string().min(1) }))
    .output(OrganizedResultSchema)
    .mutation(async ({ input }) => {
      console.log(`[consultation.organize] sessionId=${input.sessionId}`);
      return await organizeAiAgent({ sessionId: input.sessionId });
    }),

  // 吐き出し全文 → Mention 抽出 + 自動グルーピング（ADR 0007）。organize を置換する新導線。
  // BFF が既存 Problem 候補を渡し（ADR 0012）、結果を Problem リポジトリに反映する。
  extract: publicProcedure
    .input(z.object({ sessionId: z.string().min(1) }))
    .output(ExtractionResultSchema)
    .mutation(async ({ input, ctx }) => {
      console.log(`[consultation.extract] sessionId=${input.sessionId}`);
      const existing = await ctx.problemRepo.list();
      const result = await extractAiAgent({
        sessionId: input.sessionId,
        existingProblems: existing.map((p) => ({
          id: p.id,
          title: p.title,
          theme: p.theme,
          summary: p.summary,
          mentionCount: p.mentionCount,
          status: p.status,
        })),
      });
      await materializeExtraction(result, ctx.problemRepo);
      return result;
    }),

  createPlan: publicProcedure
    .input(z.object({ result: OrganizedResultSchema }))
    .output(ActionPlanSchema)
    .mutation(async ({ input }) => {
      console.log(`[consultation.createPlan]`);
      return await createPlanAiAgent({
        summary: input.result.summary,
        emotions: input.result.emotions,
        priorities: input.result.priorities,
      });
    }),

  approve: publicProcedure
    .input(
      z.object({
        approvalRequestId: z.string().min(1),
        approved: z.boolean(),
      }),
    )
    .output(ApproveResultSchema)
    .mutation(async ({ input }) => {
      console.log(
        `[consultation.approve] approvalRequestId=${input.approvalRequestId} approved=${input.approved}`,
      );
      return await approveAiAgent({
        approvalRequestId: input.approvalRequestId,
        approved: input.approved,
      });
    }),
});

// ---- history ---------------------------------------------------------------

const historyRouter = router({
  list: publicProcedure
    .output(z.array(HistoryItemSchema))
    .query(async ({ ctx }) => {
      return await ctx.historyRepo.list();
    }),

  save: publicProcedure
    .input(
      z.object({
        sessionId: z.string().min(1),
        title: z.string().min(1),
        result: OrganizedResultSchema,
        plan: ActionPlanSchema,
      }),
    )
    .output(HistoryItemSchema)
    .mutation(async ({ input, ctx }) => {
      const item = HistoryItemSchema.parse({
        id: randomUUID(),
        title: input.title,
        createdAt: nowIso(),
        result: input.result,
        plan: input.plan,
      });
      console.log(`[history.save] id=${item.id} title=${item.title}`);
      return await ctx.historyRepo.save(item);
    }),
});

// ---- problem ---------------------------------------------------------------

const problemRouter = router({
  list: publicProcedure
    .input(
      z
        .object({
          theme: ThemeSchema.optional(),
          status: ProblemStatusSchema.optional(),
        })
        .optional(),
    )
    .output(z.array(ProblemSchema))
    .query(async ({ input, ctx }) => {
      return await ctx.problemRepo.list(input ?? undefined);
    }),

  get: publicProcedure
    .input(z.object({ id: z.string().min(1) }))
    .output(ProblemSchema)
    .query(async ({ input, ctx }) => {
      return await requireProblem(ctx.problemRepo, input.id);
    }),

  // 事後トリアージ（統合 / 再リンク / 却下 / テーマ・タイトル編集 / 状態遷移）。ADR 0007。
  triage: publicProcedure
    .input(TriageInputSchema)
    .output(z.object({ problems: z.array(ProblemSchema) }))
    .mutation(async ({ input, ctx }) => {
      console.log(`[problem.triage] action=${input.action}`);
      return { problems: await applyTriage(input, ctx.problemRepo) };
    }),

  // 既存 /plan を再利用して Problem にプランを付ける（派生物。status は変えない / ADR 0007）。
  createPlan: publicProcedure
    .input(z.object({ problemId: z.string().min(1) }))
    .output(ProblemSchema)
    .mutation(async ({ input, ctx }) => {
      const problem = await requireProblem(ctx.problemRepo, input.problemId);
      const plan = await createPlanAiAgent({
        summary: problem.summary,
        emotions: dedupe(problem.mentions.map((m) => m.affect.label)),
        priorities: problem.tags,
      });
      const updated: Problem = { ...problem, plans: [...problem.plans, plan] };
      return await ctx.problemRepo.upsert(updated);
    }),
});

// ---- app router ------------------------------------------------------------

export const appRouter = router({
  health: healthRouter,
  consultation: consultationRouter,
  history: historyRouter,
  problem: problemRouter,
});

export type AppRouter = typeof appRouter;
