import { randomUUID } from "node:crypto";
import { initTRPC } from "@trpc/server";
import { z } from "zod";
import type { TrpcContext } from "./context";
import {
  approve as approveAiAgent,
  createPlan as createPlanAiAgent,
  organize as organizeAiAgent,
  sendChatMessage,
} from "../clients/aiAgentClient";
import {
  ActionPlanSchema,
  HistoryItemSchema,
  OrganizedResultSchema,
} from "../repositories/historyRepository";

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

// ---- health ----------------------------------------------------------------

const healthRouter = router({
  ping: publicProcedure.output(z.object({ ok: z.literal(true) })).query(() => {
    return { ok: true as const };
  }),
});

// ---- consultation ----------------------------------------------------------

const consultationRouter = router({
  start: publicProcedure
    .input(z.object({ concern: z.string().min(1) }))
    .output(z.object({ session: SessionSchema }))
    .mutation(async ({ input }) => {
      const sessionId = randomUUID();
      console.log(`[consultation.start] sessionId=${sessionId}`);

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

// ---- app router ------------------------------------------------------------

export const appRouter = router({
  health: healthRouter,
  consultation: consultationRouter,
  history: historyRouter,
});

export type AppRouter = typeof appRouter;
