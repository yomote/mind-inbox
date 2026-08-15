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
  ThinkingMap,
  ThinkingNode,
  ThinkingNodeKind,
  ThinkingNodeStatus,
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

/**
 * 副作用ツールの実行前に人間の承認を求める要求 (G1 / dialogue-session.mdx §5.9)。
 *
 * 契約の真実は BFF の `ChatReplySchema` (`requiresApproval` / `approvalRequestId`)。
 * **承認の要否をフロントで判定しない** — 判定源は ai-agent のツールメタデータ
 * (`approval_mode`) で、こちらで条件を書くと判定が 2 箇所に分かれ、片方だけ変わっても
 * 気づけない (= 承認が要るツールが黙って承認なしで実行される)。
 */
export type ApprovalRequest = {
  /** BFF `consultation.approve` に渡す ID。 */
  id: string;
  /** 何を実行しようとしているか (ai-agent が返す確認文)。 */
  description: string;
};

/**
 * AI 応答 1 往復分。承認要求はメッセージと同じ応答に載って来るので一緒に返す。
 *
 * メッセージだけを返す形にしないのは、承認要求を落としても「返事は出ている」ため
 * 画面上は正常に見えてしまうから (= G1 が静かに無効化される)。
 */
export type AssistantReply = {
  message: ChatMessage;
  /** 承認が要らない応答では null。 */
  approval: ApprovalRequest | null;
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
