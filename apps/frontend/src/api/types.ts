/**
 * frontend が使うドメイン型の唯一の置き場。
 *
 * - Problem / Mention 系の真実は BFF の zod schema (apps/bff/src/trpc/domain.ts)。
 *   ここでは型だけを re-export する (値は import しない — バンドル/テスト対象に BFF を混ぜない)。
 * - 相談セッション / 履歴系は BFF スキーマ確立前からの UI 契約としてここで定義する。
 * - mockApi.ts は「mock 実装」専業 (ADR 0004 の意図はデータと挙動の fixture)。
 *   型をここへ分離することで、screen 変更時に mock 実装ファイルを読む必要をなくす。
 */
import type { ProblemStatus, Theme, TriageAction } from "../../../bff/src/trpc/domain";

export type {
  Affect,
  ExtractionResult,
  Mention,
  Problem,
  ProblemStatus,
  Theme,
  TriageAction,
} from "../../../bff/src/trpc/domain";

export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  text: string;
  createdAt: string;
};

export type ConsultationSession = {
  id: string;
  title: string;
  messages: ChatMessage[];
};

export type ActionPlan = {
  title: string;
  steps: string[];
};

export type ProblemFilter = {
  theme?: Theme;
  status?: ProblemStatus;
};

/**
 * UI のフラットな triage 入力。BFF の discriminatedUnion への写像は
 * api/problems.ts の toBffTriageInput が担う。
 */
export type TriageInput = {
  action: TriageAction;
  problemId: string;
  /** editTheme 用 */
  theme?: Theme;
  /** editTitle 用 */
  title?: string;
  /** relink / merge 用（移動する Mention / 統合先 Problem） */
  mentionId?: string;
  targetProblemId?: string;
};
