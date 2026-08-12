import { randomUUID } from "node:crypto";
import { initTRPC, TRPCError } from "@trpc/server";
import { ConversationMessageSchema } from "../clients/aiAgentContracts";
import { z } from "zod";
import type { TrpcContext } from "./context";
import {
  approve as approveAiAgent,
  createPlan as createPlanAiAgent,
  ExtractError,
  extract as extractAiAgent,
  sendChatMessage,
} from "../clients/aiAgentClient";
import { issueSpeechAuthToken } from "../clients/speechTokenClient";
import {
  MAX_CONVERSATION_MESSAGES,
  MAX_CONVERSATION_TOTAL_CHARS,
  MAX_ID_LENGTH,
  MAX_MESSAGE_LENGTH,
  MAX_TITLE_LENGTH,
} from "../limits";
import { deriveTitle } from "../domain/title";
import {
  appendMention,
  dedupe,
  mergeProblems,
  problemFromMention,
  withDerived,
} from "../domain/problem";
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

/**
 * クライアントから来る識別子。**入力側にだけ上限を置く** (`../limits.ts` / #313 C-1)。
 * 出力・永続化側 (`domain.ts`) には足さない — 既存ドキュメントを読めなくしないため。
 */
const IdInputSchema = z.string().min(1).max(MAX_ID_LENGTH);

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
  /**
   * stub フォールバック応答の機械判別フラグ (#146 / ADR 0039 D6)。
   * AI_AGENT_BASE_URL 未設定で stub に落ちたときだけ true。実応答では付かない
   * (optional なので後方互換)。フロントはこれで警告バナーを出す。
   */
  stubbed: z.boolean().optional(),
});

/** consultation.extract の出力 = ExtractionResult + stub 判別フラグ (#146)。 */
const ExtractionReplySchema = ExtractionResultSchema.extend({
  stubbed: z.boolean().optional(),
});

const ApproveResultSchema = z.object({
  reply: z.string(),
});

type ChatMessage = z.infer<typeof ChatMessageSchema>;

function nowIso(): string {
  return new Date().toISOString();
}

/**
 * ai-agent の抽出結果を Problem リポジトリに反映する。
 * new は新規 Problem を起こし、existing は既存に Mention を追記して mentionCount / lastMentionedAt を更新。
 *
 * 棚卸し済み（resolved / shelved）の Problem に再言及があったら **`open` に戻す**
 * （UC-03 の事後条件）。ADR 0007 の方針は「自動で寄せて気づきを返し、違えば事後トリアージで直す」で、
 * 状態も同じ扱いにする。ここを手動にしていた頃は、抽出結果レビューが「🔁2回目 / 再燃」と
 * 表示するのに一覧の既定（open のみ）には出てこない、という食い違いが起きていた
 * （＝「また話しているのに気づかせてくれない」= プロダクトの芯が通らない）。
 * 誤検知なら詳細画面から「解決した」で戻せる。
 *
 * ルールの本体（追記 / 新規起こし / 再燃）は `../domain/problem.ts` の純粋関数。
 * ここはリポジトリとの往復だけを持つ。
 */
async function materializeExtraction(
  result: ExtractionResult,
  repo: ProblemRepository,
): Promise<void> {
  for (const { mention, grouping } of result.items) {
    if (grouping.kind === "existing") {
      const existing = await repo.get(grouping.problemId);
      if (existing) {
        await repo.upsert(appendMention(existing, mention));
        continue;
      }
      // 既存が見つからない（候補集合との齟齬）→ 取りこぼさず新規として作る
    }
    await repo.upsert(problemFromMention(mention, grouping));
  }
}

// ---- triage ----------------------------------------------------------------
// domain_model.md §4.2 / TriageActionSchema。分割は v1 では後回し。
// action ごとに必要な引数が違うため discriminatedUnion で受ける。

const TriageInputSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("resolve"), problemId: IdInputSchema }),
  z.object({ action: z.literal("shelve"), problemId: IdInputSchema }),
  z.object({ action: z.literal("reopen"), problemId: IdInputSchema }),
  z.object({ action: z.literal("dismiss"), problemId: IdInputSchema }),
  z.object({
    action: z.literal("editTheme"),
    problemId: IdInputSchema,
    theme: ThemeSchema,
  }),
  z.object({
    action: z.literal("editTitle"),
    problemId: IdInputSchema,
    title: z.string().min(1).max(MAX_TITLE_LENGTH),
  }),
  z.object({
    action: z.literal("relink"),
    mentionId: IdInputSchema,
    fromProblemId: IdInputSchema,
    toProblemId: IdInputSchema,
  }),
  z.object({
    action: z.literal("merge"),
    sourceProblemId: IdInputSchema,
    targetProblemId: IdInputSchema,
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
      const merged = mergeProblems(target, source);
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

// ---- speech ----------------------------------------------------------------
// Azure Speech (ADR 0023) の一時 authorization token 発行。
// SPA にキーは渡さない — MI の Entra トークン由来の短寿命トークンのみ。
// available: false はフロントに「Web Speech へフォールバックせよ」を伝えるシグナル。

const SpeechTokenSchema = z.discriminatedUnion("available", [
  z.object({
    available: z.literal(false),
    authToken: z.null(),
    region: z.null(),
  }),
  z.object({
    available: z.literal(true),
    // Speech SDK にそのまま渡す "aad#{resourceId}#{entraToken}" 形式
    authToken: z.string().min(1),
    region: z.string().min(1),
  }),
]);

const speechRouter = router({
  issueToken: publicProcedure.output(SpeechTokenSchema).query(async () => {
    return await issueSpeechAuthToken();
  }),
});

// ---- consultation ----------------------------------------------------------

const consultationRouter = router({
  start: publicProcedure
    .input(z.object({ concern: z.string().max(MAX_MESSAGE_LENGTH) }))
    .output(z.object({ session: SessionSchema, stubbed: z.boolean().optional() }))
    .mutation(async ({ input }) => {
      const sessionId = randomUUID();
      console.log(`[consultation.start] sessionId=${sessionId}`);

      // 空 concern で開始するのが既定 (home.mdx: テーマ入力画面なしで直接対話開始)。
      // AI の挨拶 (初手) は出さない (#241 / dialogue-session.mdx §3.1) — 会話は空のまま
      // 返し、ユーザーの発話から始める。AI も呼ばない (空メッセージは ai-agent で 422)。
      const concern = input.concern.trim();
      if (concern.length === 0) {
        return {
          session: {
            id: sessionId,
            title: deriveTitle(""),
            messages: [],
          },
        };
      }

      const chatRes = await sendChatMessage({
        sessionId,
        message: concern,
      });

      const messages: ChatMessage[] = [
        {
          id: randomUUID(),
          role: "user",
          text: concern,
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
          title: deriveTitle(concern),
          messages,
        },
        stubbed: chatRes.stubbed,
      };
    }),

  sendMessage: publicProcedure
    .input(
      z.object({
        sessionId: IdInputSchema,
        message: z.string().min(1).max(MAX_MESSAGE_LENGTH),
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
        stubbed: chatRes.stubbed,
      };
    }),

  // 吐き出し全文 → Mention 抽出 + 自動グルーピング（ADR 0007）。セッションからの唯一の出口。
  // BFF が既存 Problem 候補を渡し（ADR 0012）、結果を Problem リポジトリに反映する。
  extract: publicProcedure
    .input(
      z.object({
        sessionId: IdInputSchema,
        // 会話全文はクライアントが渡す (#183)。省略時は ai-agent 側の履歴に賭ける
        // (旧クライアント互換) が、その経路は 404 になりうる。
        //
        // **件数と合計文字数の両方で締める** (#313 C-1)。ここが 1 リクエストで
        // LLM に流し込める入力量の天井 = 1 リクエストあたりの課金の天井になる。
        // 件数だけだと 1 通が長い場合を、文字数だけだと短文の大量送信を止められない。
        messages: z
          .array(ConversationMessageSchema)
          .max(MAX_CONVERSATION_MESSAGES)
          .refine(
            (messages) =>
              messages.reduce((total, m) => total + m.text.length, 0) <=
              MAX_CONVERSATION_TOTAL_CHARS,
            { message: `会話全文が長すぎます (最大 ${MAX_CONVERSATION_TOTAL_CHARS} 文字)` },
          )
          .default([]),
      }),
    )
    .output(ExtractionReplySchema)
    .mutation(async ({ input, ctx }) => {
      console.log(
        `[consultation.extract] sessionId=${input.sessionId} messages=${input.messages.length}`,
      );
      const existing = await ctx.problemRepo.list();
      try {
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
          messages: input.messages,
        });
        await materializeExtraction(result, ctx.problemRepo);
        return result;
      } catch (err) {
        if (err instanceof ExtractError) {
          // **失敗の種別をクライアントまで運ぶ** (#183)。message は機械可読な token に
          // 留め、ユーザー向けの文面はフロント (UI 仕様の持ち場) で決める。
          console.error(`[consultation.extract] failed kind=${err.kind}: ${err.message}`);
          throw new TRPCError({
            code:
              err.kind === "session-missing"
                ? "NOT_FOUND"
                : err.kind === "llm-parse-failed"
                  ? "BAD_GATEWAY"
                  : "INTERNAL_SERVER_ERROR",
            message: err.kind,
          });
        }
        throw err;
      }
    }),

  approve: publicProcedure
    .input(
      z.object({
        approvalRequestId: IdInputSchema,
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
    .input(z.object({ id: IdInputSchema }))
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
    .input(z.object({ problemId: IdInputSchema }))
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
  speech: speechRouter,
  consultation: consultationRouter,
  problem: problemRouter,
});

export type AppRouter = typeof appRouter;
