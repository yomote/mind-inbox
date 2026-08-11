/**
 * [L2] チャットストリーミング BFF サービスの service-level test。
 *
 * 無いと何が静かに通るか: stub / フォールバック / 素通しの 3 モードのどれが壊れても
 * フロントは「tRPC フォールバックで全文が出る」ため、逐次表示が丸ごと死んだ退行
 * (#120 の主目的の喪失) が緑のまま通る。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockConfig = vi.hoisted(() => ({}) as { aiAgentBaseUrl?: string; aiAgentAudience?: string });

vi.mock("../config", () => ({ config: mockConfig }));
vi.mock("../clients/aiAgentClient", () => ({
  sendChatMessage: vi.fn(),
}));

import { sendChatMessage } from "../clients/aiAgentClient";
import { encodeSseEvent, openChatStream } from "./chatStream";

async function readAll(stream: ReadableStream<Uint8Array>): Promise<string> {
  return await new Response(stream).text();
}

/** SSE テキストから data 行の JSON を順に取り出す (テスト用の簡易パース)。 */
function parseEvents(sse: string): Array<Record<string, unknown>> {
  return sse
    .split("\n\n")
    .filter((block) => block.startsWith("data: "))
    .map((block) => JSON.parse(block.slice("data: ".length)) as Record<string, unknown>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  delete mockConfig.aiAgentBaseUrl;
  delete mockConfig.aiAgentAudience;
});

describe("[L2] openChatStream", () => {
  it("AI_AGENT_BASE_URL 未設定: stub が delta…done を逐次配信し、全文が組み立てられる", async () => {
    const events = parseEvents(
      await readAll(await openChatStream({ sessionId: "s1", message: "こんにちは" })),
    );

    const deltas = events.filter((e) => e.type === "delta");
    const done = events.at(-1) as {
      type: string;
      response: { reply: string; stubbed?: boolean };
    };
    expect(deltas.length).toBeGreaterThan(1); // 逐次配信されている (1 発全文なら stub の意味がない)
    expect(done.type).toBe("done");
    expect(deltas.map((d) => d.text).join("")).toBe(done.response.reply);
    expect(done.response.reply).toContain("こんにちは");
    // stub 判別フラグ (#146)。無いと stub ストリームが本物のふりをしてバナーが出ない退行が静かに通る
    // (実応答にフラグが立たないことは下のフォールバック test の toEqual 完全一致が守る)。
    expect(done.response.stubbed).toBe(true);
  });

  it("upstream 200 + body あり: SSE バイトを素通しする", async () => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
    const upstreamBody = new Response(
      new Uint8Array([...encodeSseEvent({ type: "delta", text: "や" })]),
    ).body as ReadableStream<Uint8Array>;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(upstreamBody, { status: 200 })),
    );

    const stream = await openChatStream({ sessionId: "s1", message: "m" });
    const events = parseEvents(await readAll(stream));
    expect(events).toEqual([{ type: "delta", text: "や" }]);
    expect(sendChatMessage).not.toHaveBeenCalled();
  });

  it("upstream 404 (旧版 ai-agent): 非ストリーミング /chat にフォールバックし合成 SSE を返す", async () => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("not found", { status: 404 })),
    );
    vi.mocked(sendChatMessage).mockResolvedValue({
      reply: "全文応答",
      requiresApproval: true,
      approvalRequestId: "appr-1",
      citations: ["doc-a"],
    });

    const events = parseEvents(
      await readAll(await openChatStream({ sessionId: "s1", message: "m" })),
    );

    expect(events).toEqual([
      { type: "delta", text: "全文応答" },
      {
        type: "done",
        response: {
          reply: "全文応答",
          requires_approval: true,
          approval_request_id: "appr-1",
          citations: ["doc-a"],
        },
      },
    ]);
  });

  it("upstream fetch 例外: 同じく非ストリーミングへフォールバックする", async () => {
    mockConfig.aiAgentBaseUrl = "http://ai-agent.example";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }),
    );
    vi.mocked(sendChatMessage).mockResolvedValue({
      reply: "ok",
      requiresApproval: false,
      approvalRequestId: null,
      citations: [],
    });

    const events = parseEvents(
      await readAll(await openChatStream({ sessionId: "s1", message: "m" })),
    );
    expect(events.at(-1)?.type).toBe("done");
    expect(sendChatMessage).toHaveBeenCalledTimes(1);
  });
});
