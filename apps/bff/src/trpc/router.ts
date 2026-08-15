import { randomUUID } from "node:crypto";
import { initTRPC, TRPCError } from "@trpc/server";
import { ConversationMessageSchema } from "../clients/aiAgentContracts";
import type { ConversationMessage } from "../clients/aiAgentContracts";
import { z } from "zod";
import type { TrpcContext } from "./context";
import { APPROVAL_NOT_FOUND_TOKEN, encodeApprovalAlreadyProcessed } from "./errorTokens";
import {
  ApprovalAlreadyProcessedError,
  ApprovalNotFoundError,
  approve as approveAiAgent,
  createPlan as createPlanAiAgent,
  ExtractError,
  extract as extractAiAgent,
  isStubMode,
  sendChatMessage,
  type StubMarked,
} from "../clients/aiAgentClient";
import { issueSpeechAuthToken } from "../clients/speechTokenClient";
import { logErrorEvent, logEvent } from "../observability/telemetry";
import {
  MAX_AFFECT_LABEL_LENGTH,
  MAX_CONVERSATION_MESSAGES,
  MAX_CONVERSATION_TOTAL_CHARS,
  MAX_DRAFT_TOTAL_CHARS,
  MAX_EXTRACTED_ITEMS,
  MAX_EXTRACTED_TEXT_LENGTH,
  MAX_ID_LENGTH,
  MAX_MESSAGE_LENGTH,
  MAX_TAG_LENGTH,
  MAX_TAGS_PER_ITEM,
  MAX_TIMESTAMP_LENGTH,
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
  AffectSchema,
  ExtractionResultSchema,
  GroupingOutcomeSchema,
  MentionSchema,
  ProblemSchema,
  ProblemStatusSchema,
  ThemeSchema,
  type ExtractedItem,
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

/**
 * クライアントが送る会話全文。**件数と合計文字数の両方で締める** (#313 C-1)。
 *
 * ここが 1 リクエストで LLM に流し込める入力量の天井 = 1 リクエストあたりの課金の
 * 天井になる。件数だけだと 1 通が長い場合を、文字数だけだと短文の大量送信を止められない。
 *
 * `preview` と `extract` の両方が同じ上限を持つ (どちらも ai-agent 経由で LLM を叩く)。
 * 片方だけに置くと、上限のない方が抜け道になる。
 */
const ConversationMessagesInputSchema = z
  .array(ConversationMessageSchema)
  .max(MAX_CONVERSATION_MESSAGES)
  .refine(
    (messages) =>
      messages.reduce((total, m) => total + m.text.length, 0) <= MAX_CONVERSATION_TOTAL_CHARS,
    { message: `会話全文が長すぎます (最大 ${MAX_CONVERSATION_TOTAL_CHARS} 文字)` },
  );

/**
 * `consultation.extract` の `draft.items` として**クライアントから来る**抽出結果。
 *
 * ドメイン側の `ExtractedItemSchema` を再利用**しない** (PR #324 Codex 指摘 P2)。
 * 再利用すると「件数だけ 200 に締めたが、1 item の中身は青天井」という穴が残る —
 * draft 経路は ai-agent を呼ばないので抽出側の上限では守れず、巨大な `statement` /
 * `excerpt` や大量のタグをそのまま Cosmos へ書ける。
 *
 * 独立定義ではなく `.extend()` で派生させているのは、**形をドメインと分岐させない**ため
 * (手で書き写すと、ドメイン側のフィールド追加・削除に気づかず契約がずれる)。上書きするのは
 * 「長さの制約」だけで、`domain.ts` 側には上限を足さない (`../limits.ts` の方針 =
 * 入力側にだけ置く。既存ドキュメントを読めなくしないため)。
 *
 * ただし **`.extend()` は新フィールドに上限を自動では付けない** — ドメインに文字列
 * フィールドが増えたらここにも 1 行足す必要がある。忘れたことは `limits.test.ts` の
 * 「どの文字列フィールドを巨大にしても拒否する」(item の全 string leaf を走査する)
 * が落として教える。
 */
const DraftMentionInputSchema = MentionSchema.extend({
  id: IdInputSchema,
  sessionId: IdInputSchema,
  dumpId: IdInputSchema.nullable(),
  createdAt: z.string().max(MAX_TIMESTAMP_LENGTH),
  statement: z.string().max(MAX_EXTRACTED_TEXT_LENGTH),
  excerpt: z.string().max(MAX_EXTRACTED_TEXT_LENGTH),
  affect: AffectSchema.extend({ label: z.string().max(MAX_AFFECT_LABEL_LENGTH) }),
  proposedTags: z.array(z.string().max(MAX_TAG_LENGTH)).max(MAX_TAGS_PER_ITEM),
  problemId: IdInputSchema.nullable(),
});

const DraftGroupingInputSchema = GroupingOutcomeSchema.extend({
  problemId: IdInputSchema,
  problemTitle: z.string().max(MAX_TITLE_LENGTH),
});

const DraftItemInputSchema = z.object({
  mention: DraftMentionInputSchema,
  grouping: DraftGroupingInputSchema,
});

/** 1 item の文字列の合計。合計上限 (`MAX_DRAFT_TOTAL_CHARS`) の数え方を 1 箇所に置く。 */
function draftItemChars(item: z.infer<typeof DraftItemInputSchema>): number {
  const { mention, grouping } = item;
  return (
    mention.id.length +
    mention.sessionId.length +
    (mention.dumpId?.length ?? 0) +
    mention.createdAt.length +
    mention.statement.length +
    mention.excerpt.length +
    mention.affect.label.length +
    mention.proposedTags.reduce((total, tag) => total + tag.length, 0) +
    (mention.problemId?.length ?? 0) +
    grouping.problemId.length +
    grouping.problemTitle.length
  );
}

/**
 * draft 全体。**件数・1 フィールドの長さ・合計文字数の 3 つで締める**。
 *
 * 合計を別に見るのは、件数 × 1 件の上限の積 (200 × 約 4,000 字) が 1 リクエストの
 * 書き込み量として大きすぎるため — 会話全文で既に採った考え方と同じ。
 */
const DraftInputSchema = z
  .object({ items: z.array(DraftItemInputSchema).max(MAX_EXTRACTED_ITEMS) })
  .refine(
    (draft) =>
      draft.items.reduce((total, item) => total + draftItemChars(item), 0) <= MAX_DRAFT_TOTAL_CHARS,
    { message: `下書き全体が長すぎます (最大 ${MAX_DRAFT_TOTAL_CHARS} 文字)` },
  );

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
 * 戻り値は **実際の書き込み実績に正規化した items** (#283)。申告 (下書きの grouping) を
 * そのまま返すと、「既存に追加」のつもりが新規作成になったケースで件数もカードのバッジも
 * 実態と食い違う。`mockApi.commitPreview` (ADR 0004 の真実) と同じ正規化をここで行い、
 * 件数はその正規化済み items から数える。
 */
async function materializeExtraction(
  result: ExtractionResult,
  repo: ProblemRepository,
): Promise<ExtractedItem[]> {
  // **この確定操作に含まれる Mention の集合**。「その Problem をこの確定が起こしたか」を
  // 追加の状態ではなく保存済みデータから復元するために使う (下の seededByThisCommit)。
  const commitMentionIds = new Set(result.items.map((item) => item.mention.id));
  const materialized: ExtractedItem[] = [];

  for (const { mention, grouping } of result.items) {
    // **kind を問わず problemId で引く** (mockApi.commitPreview と同じ意味論): 同じ id の
    // 下書きが複数あっても Problem は 1 つで、2 件目以降は Mention の追記になる。
    const target = await repo.get(grouping.problemId);

    if (target) {
      // **冪等**: 同じ draft を再送しても蓄積データを変えない (#283)。確定応答が失われて
      // ユーザーが「この内容で確定」を押し直すのは普通に起きるが、Mention は不変・追記専用
      // (domain_model.md §2.1) なので、同じ Mention ID が既に入っていれば書くことは何も無い。
      // 無いと同じ Mention が二重に入り mentionCount まで増える (静かなデータ破損)。
      const committed = target.mentions.find((m) => m.id === mention.id);
      const alreadyCommitted = committed !== undefined;

      // **応答も冪等にする**: この Problem の "種" (最初の Mention) が**この確定操作のどれか**
      // なら、その Problem はこの確定が起こしたもの = 実績は "new" だった。**この確定の
      // Mention 全体で見る**のが要点 — 種の 1 件だけで見ると、同じ Problem へ 2 件以上寄る
      // draft の再送で 2 件目以降が "existing" に化け、初回の「新規 1 / 既存 0」が
      // 「新規 1 / 既存 1」に変わる (同じ problemId の二重計上)。**追加の状態を持たず
      // 保存済みデータから復元する** — 種は appendMention でも mergeProblems でも先頭に
      // 残るため (relink で種を剥がした場合だけ崩れるが、その時は Problem の来歴自体が
      // 変わっている)。
      const seededByThisCommit = commitMentionIds.has(target.mentions[0]?.id ?? "");
      const isNew = grouping.kind === "new" || seededByThisCommit;
      // 再燃したか = **この確定で** 棚卸し済みを open に戻したか (appendMention の事後条件)。
      // 再送では「今の状態」から判定できない (Mention は保存済み・Problem はもう open) ので、
      // 初回に appendMention が Mention へ残した来歴 (`reopenedProblem`) を読み戻す。
      // 無いと同じ確定操作なのに 2 回目だけ「再燃」バッジが消える (#283)。
      const reignited = alreadyCommitted
        ? committed.reopenedProblem === true
        : target.status !== "open";

      const after = alreadyCommitted ? target : appendMention(target, mention);
      if (!alreadyCommitted) await repo.upsert(after);

      // **「何回目の言及か」は保存済みの並び順から取る** — Problem の最終件数ではない。
      // 同じ Problem に複数の Mention が寄る draft では、初回は 1 件ずつ追記しながら
      // 処理するので item ごとに 2, 3, ... と増えるが、再送では全部が保存済みなので
      // 最終件数 (3) が全 item に返り、レビュー画面の「N 回目」が初回と食い違う (#283)。
      // 追記専用 (domain_model §2.1) なので位置は不変で、初回・再送で同じ値になる。
      const positionInProblem = after.mentions.findIndex((m) => m.id === mention.id) + 1;

      materialized.push({
        mention,
        grouping: {
          ...grouping,
          // このバッチで起こした Problem への追記は "new" のまま (二重計上を防ぐ)
          kind: isNew ? "new" : "existing",
          // **表示値は下書き時点ではなく確定時点の Problem から取る** — preview 後に別タブで
          // タイトル/テーマを編集したり棚卸ししたりできる。書き込みは最新の target に対して
          // 行うのに、返却だけ下書き時点のままだとレビュー画面が古い姿を見せる。
          problemTitle: after.title,
          problemTheme: after.theme,
          mentionCount: positionInProblem > 0 ? positionInProblem : after.mentionCount,
          isRecurrence: !isNew,
          reignited,
        },
      });
      continue;
    }

    // 対象が居ない = 新規、**または existing だが確定までの間に消された** (候補集合との
    // 齟齬 / preview 後に dismiss・merge された)。取りこぼさず新規として作り、
    // **grouping も実績に正規化する** — 申告のままだと件数もバッジも「既存に追加」と出る。
    const created = problemFromMention(mention, grouping);
    await repo.upsert(created);
    // ここで起こした Problem の "種" はこの Mention なので、後続の item も再送時も
    // seededByThisCommit で "new" と復元できる (覚えておく必要は無い)。
    materialized.push({
      mention,
      grouping: {
        ...grouping,
        kind: "new",
        mentionCount: 1,
        isRecurrence: false,
        // 何も再オープンしていない (寄せ先はもう無い)
        reignited: false,
      },
    });
  }
  return materialized;
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
      // message は `aiAgentClient.extract` が組み立てた文面 (応答本文を含まない)。
      logErrorEvent("extraction.failed", {
        route: label,
        kind: err.kind,
        errorType: err.name,
        errorMessage: err.message,
      });
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

/**
 * 「新規 N 件 / 既存に追加 N 件」を **実際の書き込み実績から Problem 単位で**数える (#283)。
 * 入力は `materializeExtraction` が正規化済みの items (= 実績そのもの)。
 * `mockApi.ts` の同名関数と同じ数え方 (ADR 0004 — mock が真実)。
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
function countProblems(items: ExtractedItem[]): {
  newProblemCount: number;
  updatedProblemCount: number;
} {
  const ids = (kind: "new" | "existing") =>
    new Set(items.filter((i) => i.grouping.kind === kind).map((i) => i.grouping.problemId)).size;
  return { newProblemCount: ids("new"), updatedProblemCount: ids("existing") };
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
      // concern は相談の書き出しそのもの。**長さだけ**残す (#307)。
      logEvent("procedure", {
        route: "consultation.start",
        sessionId,
        chars: input.concern.length,
      });

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
      logEvent("procedure", {
        route: "consultation.sendMessage",
        sessionId: input.sessionId,
        chars: input.message.length,
      });

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
        sessionId: IdInputSchema,
        // 会話全文はクライアントが渡す (#183 と同じ理由 — ai-agent の履歴はプロセスメモリ)。
        messages: ConversationMessagesInputSchema,
      }),
    )
    .output(ExtractionReplySchema)
    .mutation(async ({ input, ctx }) => {
      logEvent("procedure", {
        route: "consultation.preview",
        sessionId: input.sessionId,
        count: input.messages.length,
      });
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
        sessionId: IdInputSchema,
        // 会話全文はクライアントが渡す (#183)。省略時は ai-agent 側の履歴に賭ける
        // (旧クライアント互換) が、その経路は 404 になりうる。
        messages: ConversationMessagesInputSchema.default([]),
        // 確定時に再抽出しない commit 経路 (#283 / ADR 0039 D1/D3)。
        // 「この内容で確定」は**表示中の preview の結果 (items + グルーピング) をそのまま
        // 永続化する** — 抽出は非決定的なので、確定時に再抽出すると画面で確認した内容と
        // 違うものが書かれうる (PR #240 の Codex 指摘で設計を修正)。draft があれば
        // ai-agent を呼ばずこれを確定する。optional なのは後方互換 (プレビューを経ない
        // 旧クライアント / preview 未対応ビルドは従来どおり抽出してから確定)。
        //
        // 上限を入れているのは、この配列が**そのまま Cosmos への書き込み**になるため
        // (#313 C-1)。ai-agent を経由しない経路なので、抽出側の上限では守れない。
        // 件数だけでなく 1 item の中身と合計文字数も締める (DraftInputSchema / PR #324)。
        draft: DraftInputSchema.optional(),
      }),
    )
    .output(ExtractionReplySchema)
    .mutation(async ({ input, ctx }) => {
      logEvent("procedure", {
        route: "consultation.extract",
        sessionId: input.sessionId,
        count: input.messages.length,
        // draft 経路 (再抽出しない / ADR 0039 D1) を通ったかは後から効く区別。
        kind: input.draft ? `draft:${input.draft.items.length}` : "re-extract",
      });
      // 再抽出しない: draft があれば下書きをそのまま確定する (ADR 0039 D1/D3)。
      const extracted: StubMarked<ExtractionResult> = input.draft
        ? {
            sessionId: input.sessionId,
            items: input.draft.items,
            newProblemCount: 0,
            updatedProblemCount: 0,
            // 確定経路は整理マップを返さない (#433 / 下の return も同じ)。
            thinkingMap: null,
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

      // **items も counts も書き込み実績で返す** (#283) — 申告 (grouping.kind / ai-agent の
      // 数え) ではなく、materializeExtraction が実際に何をしたかに正規化する。preview 後・
      // 確定前に対象 Problem が消えていれば「既存に追加」は新規作成に化けるため、
      // 件数だけ直してもカードのバッジが「既存に追加」のままだと画面の中で食い違う。
      const items = await materializeExtraction(extracted, ctx.problemRepo);
      // **整理マップは preview だけが返す** (#433)。確定は「保存された結果」を返す面で、
      // マップはどこにも保存されない対話中の作業机 — ここで返すとレビュー画面が
      // 保存物として地図を受け取り、「保存されている」という誤解が UI に生まれる。
      return { ...extracted, items, ...countProblems(items), thinkingMap: null };
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
      logEvent("procedure", {
        route: "consultation.approve",
        // 承認は副作用の門 (apps/bff/CLAUDE.md)。「誰が何を承認したか」ではなく
        // 「承認が通ったか」を残す — 承認対象の中身は ai-agent 側の記録。
        kind: input.approved ? "approved" : "rejected",
      });
      try {
        return await approveAiAgent({
          approvalRequestId: input.approvalRequestId,
          approved: input.approved,
        });
      } catch (err) {
        // 二重送信 (#82 / PO 裁定 2026-08-15 B 案)。**「もう無い」(NOT_FOUND) と分ける** —
        // 混ぜていた頃はフロントが「実行されたか分かりません」としか言えず、送信済みの
        // メールをユーザーがもう一度送る判断をしうる状態だった。結果 (approved /
        // rejected) を token に載せることで、UI は実行の有無まで言い切れる。
        if (err instanceof ApprovalAlreadyProcessedError) {
          logErrorEvent("approve.already-processed", {
            route: "consultation.approve",
            kind: input.approved ? "approved" : "rejected",
            // どちらで解決済みだったか。**承認対象の中身や時刻は載せない**
            // (ALLOWED_FIELDS の外なので出口で落ちる) — 運用が知りたいのは
            // 「二重送信がどれだけ起きているか」だけ。
            reason: err.status,
          });
          throw new TRPCError({
            code: "CONFLICT",
            message: encodeApprovalAlreadyProcessed(err.status),
          });
        }
        // 承認レコードがもう無い (TTL 1h 失効 / ai-agent 再起動) は
        // **回復不能な失敗として区別する** (#82 / PR #416 judge major-1)。汎用エラーで
        // 返すとフロントは「再試行してください」を出し続け、承認カードが閉じられない
        // まま会話が永久に詰む (再試行は決して成功しない)。
        if (err instanceof ApprovalNotFoundError) {
          logErrorEvent("approve.record-gone", {
            route: "consultation.approve",
            kind: input.approved ? "approved" : "rejected",
          });
          // message は機械可読な token に固定する。**フロントは code だけでは判定できない**
          // (tRPC は procedure 未配備でも NOT_FOUND を返す / Codex 4 巡目 P2) ので、
          // この token がフロントとの唯一の合図になる。リテラルは errorTokens.ts が
          // 1 個だけ持ち、フロントも同じものを import する (二重定義にしない)。
          throw new TRPCError({ code: "NOT_FOUND", message: APPROVAL_NOT_FOUND_TOKEN });
        }
        throw err;
      }
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
      logEvent("procedure", { route: "problem.triage", action: input.action });
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
