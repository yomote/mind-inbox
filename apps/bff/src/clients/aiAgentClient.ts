import { randomUUID } from "node:crypto";
import { config } from "../config";
import { serviceHeaders } from "./serviceToken";
import type { ExtractionResult } from "../trpc/domain";
import type {
  ApproveRequest,
  ApproveResponse,
  ChatRequest,
  ChatResponse,
  ExtractRequest,
  OrganizeRequest,
  OrganizeResponse,
  PlanRequest,
  PlanResponse,
} from "./aiAgentContracts";

// I/O 契約の真実は `aiAgentContracts.ts` の zod (L0 契約テストが機械比較する)。
// 従来この場所に素の TS type があったので、既存 import を壊さないよう型名を re-export する。
export type {
  ApproveRequest,
  ApproveResponse,
  ChatRequest,
  ChatResponse,
  ExistingProblemRef,
  ExtractRequest,
  OrganizeRequest,
  OrganizeResponse,
  PlanRequest,
  PlanResponse,
} from "./aiAgentContracts";

/**
 * ai-agent サービスへの HTTP クライアント。
 * AI_AGENT_BASE_URL 未設定時は stub レスポンスを返す。
 */
export async function sendChatMessage(req: ChatRequest): Promise<ChatResponse> {
  if (!config.aiAgentBaseUrl) {
    console.log("[aiAgentClient] AI_AGENT_BASE_URL not set — using stub response");
    return stubChatResponse(req);
  }

  const url = `${config.aiAgentBaseUrl}/chat`;
  console.log(`[aiAgentClient] POST ${url}`);

  const res = await fetch(url, {
    method: "POST",
    headers: await serviceHeaders(config.aiAgentAudience, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({ session_id: req.sessionId, message: req.message }),
  });

  if (!res.ok) {
    throw new Error(`aiAgentClient: POST /chat failed — ${res.status} ${res.statusText}`);
  }

  const json = (await res.json()) as {
    reply: string;
    requires_approval?: boolean;
    approval_request_id?: string | null;
    citations?: string[];
  };

  return {
    reply: json.reply,
    requiresApproval: Boolean(json.requires_approval),
    approvalRequestId: json.approval_request_id ?? null,
    citations: json.citations ?? [],
  };
}

export async function organize(req: OrganizeRequest): Promise<OrganizeResponse> {
  if (!config.aiAgentBaseUrl) {
    console.log("[aiAgentClient] AI_AGENT_BASE_URL not set — using stub /organize");
    return stubOrganizeResponse();
  }

  const url = `${config.aiAgentBaseUrl}/organize`;
  console.log(`[aiAgentClient] POST ${url}`);

  const res = await fetch(url, {
    method: "POST",
    headers: await serviceHeaders(config.aiAgentAudience, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({ session_id: req.sessionId }),
  });

  if (!res.ok) {
    throw new Error(`aiAgentClient: POST /organize failed — ${res.status} ${res.statusText}`);
  }

  const json = (await res.json()) as OrganizeResponse;
  return {
    summary: json.summary ?? "",
    emotions: json.emotions ?? [],
    priorities: json.priorities ?? [],
  };
}

/**
 * セッション全文を Mention[] に抽出 + 既存 Problem へグルーピング（ai-agent /extract）。
 * レスポンスは domain.ts の ExtractionResult（camelCase）と一致する。
 */
export async function extract(req: ExtractRequest): Promise<ExtractionResult> {
  if (!config.aiAgentBaseUrl) {
    console.log("[aiAgentClient] AI_AGENT_BASE_URL not set — using stub /extract");
    return stubExtractResponse(req);
  }

  const url = `${config.aiAgentBaseUrl}/extract`;
  console.log(`[aiAgentClient] POST ${url}`);

  const res = await fetch(url, {
    method: "POST",
    headers: await serviceHeaders(config.aiAgentAudience, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({
      session_id: req.sessionId,
      existing_problems: req.existingProblems.map((p) => ({
        id: p.id,
        title: p.title,
        theme: p.theme,
        summary: p.summary,
        mention_count: p.mentionCount,
        status: p.status,
      })),
    }),
  });

  if (!res.ok) {
    throw new Error(`aiAgentClient: POST /extract failed — ${res.status} ${res.statusText}`);
  }

  return (await res.json()) as ExtractionResult;
}

export async function createPlan(req: PlanRequest): Promise<PlanResponse> {
  if (!config.aiAgentBaseUrl) {
    console.log("[aiAgentClient] AI_AGENT_BASE_URL not set — using stub /plan");
    return stubPlanResponse();
  }

  const url = `${config.aiAgentBaseUrl}/plan`;
  console.log(`[aiAgentClient] POST ${url}`);

  const res = await fetch(url, {
    method: "POST",
    headers: await serviceHeaders(config.aiAgentAudience, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({
      summary: req.summary,
      emotions: req.emotions,
      priorities: req.priorities,
    }),
  });

  if (!res.ok) {
    throw new Error(`aiAgentClient: POST /plan failed — ${res.status} ${res.statusText}`);
  }

  const json = (await res.json()) as PlanResponse;
  return {
    title: json.title ?? "アクションプラン",
    steps: json.steps ?? [],
  };
}

export async function approve(req: ApproveRequest): Promise<ApproveResponse> {
  if (!config.aiAgentBaseUrl) {
    console.log("[aiAgentClient] AI_AGENT_BASE_URL not set — using stub /approve");
    return stubApproveResponse(req.approved);
  }

  const url = `${config.aiAgentBaseUrl}/approve`;
  console.log(`[aiAgentClient] POST ${url}`);

  const res = await fetch(url, {
    method: "POST",
    headers: await serviceHeaders(config.aiAgentAudience, {
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({
      approval_request_id: req.approvalRequestId,
      approved: req.approved,
    }),
  });

  if (!res.ok) {
    throw new Error(`aiAgentClient: POST /approve failed — ${res.status} ${res.statusText}`);
  }

  return (await res.json()) as ApproveResponse;
}

function stubChatResponse(req: ChatRequest): ChatResponse {
  return {
    reply: `[stub] received: "${req.message}"`,
    requiresApproval: false,
    approvalRequestId: null,
    citations: [],
  };
}

function stubOrganizeResponse(): OrganizeResponse {
  return {
    summary: "[stub] 会話の整理結果がここに表示されます。",
    emotions: ["[stub] 不安", "[stub] 期待"],
    priorities: ["[stub] 最優先タスクを決める", "[stub] 休息を確保する"],
  };
}

function stubExtractResponse(req: ExtractRequest): ExtractionResult {
  const problemId = `prob-stub-${randomUUID()}`;
  const now = new Date().toISOString();
  return {
    sessionId: req.sessionId,
    items: [
      {
        mention: {
          id: `men-stub-${randomUUID()}`,
          sessionId: req.sessionId,
          dumpId: req.sessionId,
          createdAt: now,
          statement: "[stub] 抽出された困りごとがここに表示されます。",
          excerpt: "[stub] …と話しました",
          affect: { label: "[stub] 不安", valence: "negative", intensity: 0.5 },
          proposedTheme: "未分類",
          proposedTags: ["[stub]"],
          problemId,
          groupingConfidence: null,
        },
        grouping: {
          kind: "new",
          problemId,
          problemTitle: "[stub] 困りごと",
          problemTheme: "未分類",
          isRecurrence: false,
          mentionCount: 1,
          reignited: false,
          groupingConfidence: null,
        },
      },
    ],
    newProblemCount: 1,
    updatedProblemCount: 0,
  };
}

function stubPlanResponse(): PlanResponse {
  return {
    title: "[stub] アクションプラン",
    steps: [
      "[stub] ステップ1: 取り組むタスクを 1 つ選ぶ",
      "[stub] ステップ2: 15 分だけ手を付ける",
      "[stub] ステップ3: 進捗を記録する",
    ],
  };
}

function stubApproveResponse(approved: boolean): ApproveResponse {
  return {
    reply: approved
      ? "[stub] 承認されました。操作を実行しました。"
      : "[stub] キャンセルされました。",
  };
}
