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
import { MAX_MESSAGE_LENGTH } from "../limits";
import { ActionPlanSchema, ProblemStatusSchema, ThemeSchema } from "../trpc/domain";

// ── /chat ────────────────────────────────────────────────────────────────────

export const ChatRequestSchema = z.object({
  sessionId: z.string(),
  message: z.string(),
});
export type ChatRequest = z.infer<typeof ChatRequestSchema>;

/**
 * 1 ターン分の応答 (`/chat` と `/chat/stream` の done が同じ形を運ぶ)。
 *
 * `choices` は ai-agent の `offer_choices` ツールが提示した選択肢 (#432-b)。
 * **空配列 = 選択肢なし**が既定で、`LLM_EXPOSED_TOOLS` が空 (既定) の構成では常に空。
 *
 * **`requiresApproval` とは別フィールドであること自体が仕様**: 承認カードは
 * 「承認するまで、この操作は行われません」と言う画面で、会話の分岐 (押さなくても
 * 進める) をそこに混ぜると嘘になる。相乗りさせるとフロントは両者を区別できない。
 */
export const ChatResponseSchema = z.object({
  reply: z.string(),
  requiresApproval: z.boolean(),
  approvalRequestId: z.string().nullable(),
  citations: z.array(z.string()),
  choices: z.array(z.string()),
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

/**
 * `/approve` が「承認レコードがもう無い」ときに返す 404 の `detail` (#82 / PR #416)。
 *
 * schema ではなくエラー本文の契約なので pydantic との機械比較には乗らないが、
 * **BFF の分岐がこの文字列に依存している**ので契約の真実はここに置く。実文字列の
 * 出どころは `apps/services/ai-agent/app/workflow.py` の `resume_after_approval` が
 * 投げる `ValueError` で、`app/main.py` が `detail=str(exc)` にそのまま載せる:
 *
 *   - `Approval not found: '<id>'`             — 未知 ID / TTL 1h 失効 / 再起動で消えた
 *   - `Approval checkpoint not found: '<id>'`  — checkpoint が消えている
 *   - `Approval already processed: '<status>'` — **旧 ai-agent** が返す消費済み
 *
 * 現行の ai-agent は消費済みを 409 + 現在状態 (`ApprovalConflictSchema`) で返す
 * (#82 / PO 裁定 2026-08-15 B 案)。それでも `already processed` をこの 404 判別に
 * **残してある**のは、**配備スキューの窓**があるため: BFF だけ先に新しくなると、
 * 旧 ai-agent は消費済みを 404 で返し続ける。パターンから外すと、その窓では
 * 二重送信が「承認レコード由来ではない 404」= 上流障害に化け、**カードが閉じられず
 * 会話が詰む** (404 デッドロックの再発)。残しておけば、旧新どちらの組み合わせでも
 * 二重送信はカード閉鎖に落ちる (新しい組では 409 側が結果まで運ぶ)。
 *
 * **409 の判別と競合しない**: 409 は HTTP status と body の形で判別しており、
 * この detail パターンは 404 のときしか見ない。
 *
 * **これ以外の 404 は承認レコードの状態について何も言っていない** (FastAPI 既定の
 * `Not Found` / プロキシの HTML / ベース URL 違い)。区別せず全部を「もう受け付け
 * られない」に写すと、生きている checkpoint を持つ承認カードまで閉じてしまう。
 *
 * 文字列一致なので ai-agent 側の文言変更で静かに切れる — 切れたことに気づけるよう、
 * `npm run test:contract` が ai-agent のソース中の実文字列と突き合わせる。
 */
export const APPROVAL_GONE_DETAIL =
  /^Approval (not found|already processed|checkpoint not found)\b/;

/**
 * `/approve` の **409** body (#82 / PO 裁定 2026-08-15 B 案 / ai-agent の
 * `ApprovalConflictResponse`)。同じ承認 ID への 2 回目が来たときの現在状態。
 *
 * **status は「どちらの決定を受け付けたか」であって実行の完了ではない**
 * (PR #430 Codex P1)。ai-agent は遷移を checkpoint 再開の**前**に書くので、
 * `approved` の記録後・副作用の実行前に落ちれば「approved なのに未実行」が残る。
 *
 *   - `approved` … 実行してよいと受け付けた。**実行された保証はない**
 *   - `rejected` … 実行しないと受け付けた。この経路でツールは呼ばれない = **未実行と言える**
 *
 * それでも 404 と分ける価値があるのは、**「もう解決済み」だと分かること自体**が
 * 404 (レコードが消えた) からは得られないため — 却下済みなら未実行と言い切れ、
 * 承認済みなら「実行されたかは会話を続けて確認」と正確に案内できる。
 *
 * `processedAt` は **受け付けた時刻** (完了時刻ではない) で、**nullable** — この項目より
 * 前に書かれた ai-agent の `ApprovalRecord` には時刻が無い。**時刻の欠落を「未処理」と
 * 読まないこと** (判定は status だけで行う)。
 *
 * schema にして zod で検証しているのは、**承認と無関係な 409 を「処理済み」に
 * 化けさせない**ため (404 側で detail を選り分けているのと同じ規律)。プロキシや
 * 別レイヤの 409 はこの形にならないので、パースに失敗したものは上流障害として扱う。
 */
export const ApprovalConflictSchema = z.object({
  detail: z.string(),
  status: z.enum(["approved", "rejected"]),
  processedAt: z.string().nullable(),
});
export type ApprovalConflict = z.infer<typeof ApprovalConflictSchema>;

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

/**
 * 抽出対象の会話 1 発話 (#183)。
 *
 * **会話は呼び出し側が渡す**。ai-agent のセッション履歴はプロセスメモリで、
 * scale-to-zero・スケールアウト・リビジョン差し替えのいずれでも消えるため、
 * それに依存すると「対話はできたのに抽出だけ 404」が起きる。
 *
 * `text` の上限は `../limits.ts` (#313 C-1)。このスキーマは `consultation.extract` の
 * **入力**としてそのまま使われる = クライアントから来る文字列なので、ここで締めないと
 * 1 通に巨大な文字列を詰めてプロンプトに流し込める。契約テストは min/max を表層差として
 * 無視するので (`schemaDiff.ts` / strategy.md §2.1)、pydantic 側との差分にはならない。
 */
export const ConversationMessageSchema = z.object({
  role: z.enum(["user", "assistant"]),
  text: z.string().max(MAX_MESSAGE_LENGTH),
});
export type ConversationMessage = z.infer<typeof ConversationMessageSchema>;

export const ExtractRequestSchema = z.object({
  sessionId: z.string(),
  existingProblems: z.array(ExistingProblemRefSchema),
  messages: z.array(ConversationMessageSchema),
});
export type ExtractRequest = z.infer<typeof ExtractRequestSchema>;
