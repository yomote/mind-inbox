import { randomUUID } from "node:crypto";
import { initTRPC, TRPCError } from "@trpc/server";
import { ConversationMessageSchema } from "../clients/aiAgentContracts";
import type { ConversationMessage } from "../clients/aiAgentContracts";
import { z } from "zod";
import type { TrpcContext } from "./context";
import {
  approve as approveAiAgent,
  createPlan as createPlanAiAgent,
  ExtractError,
  extract as extractAiAgent,
  isStubMode,
  sendChatMessage,
  type StubMarked,
} from "../clients/aiAgentClient";
import { issueSpeechAuthToken } from "../clients/speechTokenClient";
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
  ExtractedItemSchema,
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
 *
 * 戻り値は **実際に行った書き込みの種別** (#283)。grouping の申告ではなくこれを数える —
 * 「既存に追加」のつもりでも対象が存在しなければ新規作成になるため (下記フォールバック)。
 */
async function materializeExtraction(
  result: ExtractionResult,
  repo: ProblemRepository,
): Promise<MaterializedOutcome[]> {
  // このバッチで新しく起こした Problem。あとから同じ Problem へ寄る Mention が来ても
  // 「既存に追加」ではなく新規の一部として数えるために覚えておく
  // (でないと 1 つの Problem が「新規 1 件」かつ「既存に追加 1 件」に二重計上される)。
  const createdHere = new Set<string>();
  const outcomes: MaterializedOutcome[] = [];
  /** このバッチで作った Problem への追記は「新規」の一部として数える。 */
  const outcomeFor = (problemId: string): MaterializedOutcome => ({
    kind: createdHere.has(problemId) ? "new" : "existing",
    problemId,
  });

  for (const { mention, grouping } of result.items) {
    const target = await repo.get(grouping.problemId);

    // **冪等**: 同じ draft を再送しても蓄積データを変えない (#283)。確定応答が失われて
    // ユーザーが「この内容で確定」を押し直すのは普通に起きるが、Mention は不変・追記専用
    // (domain_model.md §2.1) なので、同じ Mention ID が既に入っていれば書くことは何も無い。
    // 無いと同じ Mention が二重に入り mentionCount まで増える (静かなデータ破損)。
    if (target?.mentions.some((m) => m.id === mention.id)) {
      outcomes.push(outcomeFor(grouping.problemId));
      continue;
    }

    if (grouping.kind === "existing" && target) {
      await repo.upsert(appendMention(target, mention));
      outcomes.push(outcomeFor(grouping.problemId));
      continue;
    }

    // new、または existing だが対象が見つからない（候補集合との齟齬 / preview 後・
    // 確定前に dismiss 等で消された）→ 取りこぼさず新規として作る。**実績は "new"**
    const created = problemFromMention(mention, grouping);
    await repo.upsert(created);
    createdHere.add(created.id);
    outcomes.push({ kind: "new", problemId: created.id });
  }
  return outcomes;
}

/**
 * ai-agent `/extract` を呼び、失敗種別を tRPC エラーへ翻訳する (preview / extract 共通)。
 *
 * **ここでは書かない** — 永続化するかは呼び出し側の責務 (preview は書かない / extract は
 * materializeExtraction で書く)。失敗の翻訳は #183 の「理由を失わない」規律をそのまま使う:
 * message は機械可読な token に留め、ユーザー向けの文面はフロント (UI 仕様の持ち場) で決める。
 */
async function runExtraction(
  label: string,
  sessionId: string,
  messages: ConversationMessage[],
  repo: ProblemRepository,
): Promise<StubMarked<ExtractionResult>> {
  const existing = await repo.list();
  try {
    return await extractAiAgent({
      sessionId,
      existingProblems: existing.map((p) => ({
        id: p.id,
        title: p.title,
        theme: p.theme,
        summary: p.summary,
        mentionCount: p.mentionCount,
        status: p.status,
      })),
      messages,
    });
  } catch (err) {
    if (err instanceof ExtractError) {
      console.error(`[${label}] failed kind=${err.kind}: ${err.message}`);
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
}

/** `materializeExtraction` が実際に行った書き込み 1 件分の実績 (#283)。 */
type MaterializedOutcome = { kind: "new" | "existing"; problemId: string };

/**
 * 「新規 N 件 / 既存に追加 N 件」を **実際の書き込み実績から Problem 単位で**数える (#283)。
 *
 * 2 つのズレをここで潰している:
 *
 * 1. **item 数ではなく problemId の異なり数**で数える。同じ Dump 内で同じ既存 Problem に
 *    複数の Mention が寄ることがあり (ai-agent の `extractor.py` は `running_count` で
 *    累積させる正規の経路)、item をそのまま数えると 1 件しか更新していないのに
 *    「既存に追加 2 件」と過大表示される。ai-agent 側も
 *    `updated_problem_count=len(updated_ids)` と集合で数えており、その意味論に合わせる
 * 2. **grouping の申告ではなく実績で数える**。preview 後・確定前に対象 Problem が
 *    dismiss 等で消えていると、`materializeExtraction` は「既存に追加」ではなく
 *    新規作成にフォールバックする。申告を数えるとレビュー画面に「新規 0 / 既存に追加 1」と
 *    返り、実際に起きたこと (新規作成) と食い違う
 */
function countProblems(outcomes: MaterializedOutcome[]): {
  newProblemCount: number;
  updatedProblemCount: number;
} {
  const ids = (kind: "new" | "existing") =>
    new Set(outcomes.filter((o) => o.kind === kind).map((o) => o.problemId)).size;
  return { newProblemCount: ids("new"), updatedProblemCount: ids("existing") };
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
    .input(z.object({ concern: z.string() }))
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
        stubbed: chatRes.stubbed,
      };
    }),

  // 会話の途中経過 → 読み取り専用の下書き抽出 (#283 / ADR 0039 D1)。
  // ai-agent `/extract` で抽出 + グルーピング候補を計算するが、**Problem リポジトリには
  // 一切書かない** — 右ペインの下書きは揮発し、確定 (extract) して初めて Problem が増える。
  // ここが書いてしまうと「確定していない下書き」が蓄積データを静かに汚す (ADR 0039 の核)。
  // 読み取り専用だが mutation にしているのは、会話全文を GET の URL クエリではなく
  // POST body で運ぶため (extract と同じ理由 — 長い会話で URL 長制限を踏まない)。
  preview: publicProcedure
    .input(
      z.object({
        sessionId: z.string().min(1),
        // 会話全文はクライアントが渡す (#183 と同じ理由 — ai-agent の履歴はプロセスメモリ)。
        messages: z.array(ConversationMessageSchema),
      }),
    )
    .output(ExtractionReplySchema)
    .mutation(async ({ input, ctx }) => {
      console.log(
        `[consultation.preview] sessionId=${input.sessionId} messages=${input.messages.length}`,
      );
      // 書かない: materializeExtraction を呼ばずそのまま返す。
      return await runExtraction(
        "consultation.preview",
        input.sessionId,
        input.messages,
        ctx.problemRepo,
      );
    }),

  // 吐き出し全文 → Mention 抽出 + 自動グルーピング（ADR 0007）。セッションからの唯一の出口。
  // BFF が既存 Problem 候補を渡し（ADR 0012）、結果を Problem リポジトリに反映する。
  extract: publicProcedure
    .input(
      z.object({
        sessionId: z.string().min(1),
        // 会話全文はクライアントが渡す (#183)。省略時は ai-agent 側の履歴に賭ける
        // (旧クライアント互換) が、その経路は 404 になりうる。
        messages: z.array(ConversationMessageSchema).default([]),
        // 確定時に再抽出しない commit 経路 (#283 / ADR 0039 D1/D3)。
        // 「この内容で確定」は**表示中の preview の結果 (items + グルーピング) をそのまま
        // 永続化する** — 抽出は非決定的なので、確定時に再抽出すると画面で確認した内容と
        // 違うものが書かれうる (PR #240 の Codex 指摘で設計を修正)。draft があれば
        // ai-agent を呼ばずこれを確定する。optional なのは後方互換 (プレビューを経ない
        // 旧クライアント / preview 未対応ビルドは従来どおり抽出してから確定)。
        draft: z.object({ items: z.array(ExtractedItemSchema) }).optional(),
      }),
    )
    .output(ExtractionReplySchema)
    .mutation(async ({ input, ctx }) => {
      console.log(
        `[consultation.extract] sessionId=${input.sessionId} messages=${input.messages.length}` +
          (input.draft ? ` draft=${input.draft.items.length}` : ""),
      );
      // 再抽出しない: draft があれば下書きをそのまま確定する (ADR 0039 D1/D3)。
      const extracted: StubMarked<ExtractionResult> = input.draft
        ? {
            sessionId: input.sessionId,
            items: input.draft.items,
            newProblemCount: 0,
            updatedProblemCount: 0,
            // draft 経路は ai-agent を呼ばないので応答由来の stub フラグを持てない。
            // **サーバ側の条件 (AI_AGENT_BASE_URL の有無) で判定する** (#283 / #146)。
            // クライアント申告を信じるとフラグを落とす偽装で「本物のふりをした stub」が
            // 復活するため、draft の内容には一切依存させない。
            ...(isStubMode() ? { stubbed: true as const } : {}),
          }
        : await runExtraction(
            "consultation.extract",
            input.sessionId,
            input.messages,
            ctx.problemRepo,
          );

      // **counts は書き込み実績から導出する** (#283) — 申告 (grouping.kind / ai-agent の
      // 数え) ではなく、materializeExtraction が実際に何をしたかを数える。preview 後・
      // 確定前に対象 Problem が消えていれば「既存に追加」は新規作成に化けるため。
      const outcomes = await materializeExtraction(extracted, ctx.problemRepo);
      return { ...extracted, ...countProblems(outcomes) };
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
  speech: speechRouter,
  consultation: consultationRouter,
  problem: problemRouter,
});

export type AppRouter = typeof appRouter;
