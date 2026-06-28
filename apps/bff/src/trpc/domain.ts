/**
 * Phase D — ドメイン型定義（Mention / Problem / Theme）の真実の所在。
 *
 * basic_design §8.3 に従い、型のソースは BFF 側（ここ）に置き、フロントは type-only で
 * 参照する（`apps/frontend/src/trpc/client.ts` が `AppRouter` を import するのと同じ流儀）。
 *
 * 対象: docs/design/domain_model.md §2 の 2層モデル（Mention → Problem）。
 * - Mention: 1回のセッションで表明された「独立した困りごと1件」の観測記録（不変・追記専用）
 * - Problem: 似た Mention を束ねた、状態とライフサイクルを持つ追跡単位
 * - Theme:   主テーマ（固定7分類 + 未分類）
 *
 * NOTE(Phase D): まだ router からは参照しない（mock 先行）。tRPC 手続きへの結線は Phase B。
 */
import { z } from "zod";
import { ActionPlanSchema } from "../repositories/historyRepository";

// ---- Theme（主テーマ: ライフホイール由来の固定7分類 + 未分類）-------------------
// domain_model.md §2.4。`未分類` はテーマというより「未確定状態 / 自動分類の逃げ場」。

export const THEMES = [
  "仕事・キャリア",
  "お金",
  "心と体",
  "家族・パートナー",
  "人間関係",
  "自己理解・生き方",
  "日常・生活",
  "未分類",
] as const;

export const ThemeSchema = z.enum(THEMES);
export type Theme = z.infer<typeof ThemeSchema>;

// ---- Affect（感情 / 切迫感）-------------------------------------------------
// domain_model.md §2.2: affect は occurrence ごとに変わるので Problem ではなく Mention に乗る。
// Problem の「感情の推移」は Mention を時系列に並べて得る。

export const AffectValenceSchema = z.enum(["negative", "neutral", "positive"]);
export type AffectValence = z.infer<typeof AffectValenceSchema>;

export const AffectSchema = z.object({
  /** 感情ラベル（例: 不安 / 焦り / 安堵） */
  label: z.string(),
  /** 感情の方向（時系列の推移を色で表すため） */
  valence: AffectValenceSchema,
  /** 切迫感 0..1（その時どれだけ差し迫っていたか） */
  intensity: z.number().min(0).max(1),
});
export type Affect = z.infer<typeof AffectSchema>;

// ---- Mention（観測 / = PagerDuty Alert）------------------------------------
// domain_model.md §2.1。不変・追記専用。

export const MentionSchema = z.object({
  id: z.string(),
  /** どの吐き出しから出たか（来歴） */
  sessionId: z.string(),
  dumpId: z.string().nullable(),
  /** 語られた日時 */
  createdAt: z.string(),
  /** 抽出された困りごと本文（正規化済み） */
  statement: z.string(),
  /** 元発話からの引用（来歴・信頼性。「あなたがこう言ったやつ」） */
  excerpt: z.string(),
  /** その時の感情 / 切迫感 */
  affect: AffectSchema,
  /** 抽出時の暫定テーマ・タグ */
  proposedTheme: ThemeSchema,
  proposedTags: z.array(z.string()),
  /** 束ね先 Problem（未グルーピングなら null） */
  problemId: z.string().nullable(),
  /** 自動グルーピング時の類似スコア 0..1（手動リンクなら null） */
  groupingConfidence: z.number().min(0).max(1).nullable(),
});
export type Mention = z.infer<typeof MentionSchema>;

// ---- Problem（集約・追跡対象 / = PagerDuty Incident）------------------------
// domain_model.md §2.2 / §4.2。

export const ProblemStatusSchema = z.enum(["open", "resolved", "shelved"]);
export type ProblemStatus = z.infer<typeof ProblemStatusSchema>;

export const ProblemSchema = z.object({
  id: z.string(),
  /** 短い見出し（AI 生成 + ユーザー編集可） */
  title: z.string(),
  /** 現在の理解（Mention 群から要約） */
  summary: z.string(),
  /** 主テーマ（1つ） */
  theme: ThemeSchema,
  /** 下位の自由タグ */
  tags: z.array(z.string()),
  /** ライフサイクル状態 */
  status: ProblemStatusSchema,
  /** 束ねられた Mention 群（最低1。最初が"種"、以降が再出現） */
  mentions: z.array(MentionSchema).min(1),
  /** 言及回数（= mentions の派生。繰り返し重点の指標） */
  mentionCount: z.number().int().nonnegative(),
  /** 紐づく ActionPlan（派生物。状態に影響しない） */
  plans: z.array(ActionPlanSchema),
  createdAt: z.string(),
  /** 最終言及（休眠判定に使う派生ビューの基点） */
  lastMentionedAt: z.string(),
  resolvedAt: z.string().nullable(),
  shelvedAt: z.string().nullable(),
});
export type Problem = z.infer<typeof ProblemSchema>;

// ---- 抽出結果（consultation.extract の戻り）--------------------------------
// 1 Dump（= 1セッション全文）→ 0..N Mention + 自動グルーピング結果。
// 抽出結果レビュー画面（extract-review.mdx）が消費する。

/** ある Mention が新規 Problem を起こしたか / 既存に寄ったか + 再出現の気づき */
export const GroupingOutcomeSchema = z.object({
  /** 🆕 新規 Problem / 🔁 既存に追加 */
  kind: z.enum(["new", "existing"]),
  problemId: z.string(),
  problemTitle: z.string(),
  problemTheme: ThemeSchema,
  /** 既存 Problem への再出現か（UC-03 の気づき） */
  isRecurrence: z.boolean(),
  /** 「今月N回目」相当の言及回数（new なら 1） */
  mentionCount: z.number().int().positive(),
  /** resolved / shelved だった Problem が再点火したか */
  reignited: z.boolean(),
  /** 自動グルーピングの類似スコア 0..1（new なら null） */
  groupingConfidence: z.number().min(0).max(1).nullable(),
});
export type GroupingOutcome = z.infer<typeof GroupingOutcomeSchema>;

export const ExtractedItemSchema = z.object({
  mention: MentionSchema,
  grouping: GroupingOutcomeSchema,
});
export type ExtractedItem = z.infer<typeof ExtractedItemSchema>;

export const ExtractionResultSchema = z.object({
  sessionId: z.string(),
  items: z.array(ExtractedItemSchema),
  /** 起こした新規 Problem 数 */
  newProblemCount: z.number().int().nonnegative(),
  /** 既存に追加した（更新した）Problem 数 */
  updatedProblemCount: z.number().int().nonnegative(),
});
export type ExtractionResult = z.infer<typeof ExtractionResultSchema>;

// ---- トリアージ（事後編集の操作種別）---------------------------------------
// domain_model.md §4.2 / implementation_plan_v1.md B3。分割は v1 では後回し。

export const TriageActionSchema = z.enum([
  "relink", // 再リンク（Mention を別 Problem に移す / 新規切り出し）
  "merge", // 統合（2つの Problem を1つに）
  "dismiss", // 却下（誤抽出を退ける）
  "editTheme", // 主テーマ付け替え
  "editTitle", // タイトル編集
  "resolve", // 解決（open → resolved）
  "shelve", // もう気にしない（open → shelved）
  "reopen", // 掘り起こし / 再燃（resolved|shelved → open）
]);
export type TriageAction = z.infer<typeof TriageActionSchema>;
