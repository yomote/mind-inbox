/**
 * [L2] HTTP ハンドラの **status / ヘッダ表**を固定する。
 *
 * なぜこれが要るか: BFF には入口が 2 つある (Azure Functions / local-server) ため、
 * `handlers.ts` に status の決定を集約した。**が、集約しただけでは status のズレは止まらない。**
 *
 * 実際 2026-08-08 に「TTS のプリフェッチが 202 を返すようになったのに L4 が 200 を
 * 期待し続けて落ちる」事故が起きている ([ADR 0032](../../../../docs/adr/0032-use-case-acceptance-tests-against-real-wiring.md) の動機)。
 * L3-real は VOICEVOX 未設定 (204) の経路しか通らないので、**200 / 202 / 502 の分岐は
 * どのテストも踏んでいなかった** (PR #170 のレビュー指摘)。ここがその穴を塞ぐ。
 *
 * ここで test しないこと:
 *   - 下流 (VOICEVOX / ai-agent) との実 HTTP — それぞれの client / service の範疇
 *   - tRPC の手続きの中身 — router.test.ts
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../chat/chatStream", () => ({ openChatStream: vi.fn() }));
vi.mock("../tts/ttsService", () => ({ prefetchTts: vi.fn(), synthesizeTts: vi.fn() }));
vi.mock("../warmup/warmupService", () => ({ warmupDownstreams: vi.fn() }));

import { openChatStream } from "../chat/chatStream";
import { prefetchTts, synthesizeTts } from "../tts/ttsService";
import { warmupDownstreams } from "../warmup/warmupService";
import { handleChatStream, handleTrpc, handleTts, handleWarmup } from "./handlers";

/** ログを飲み込む (テスト出力を汚さない)。 */
const silent = { log: () => {}, error: () => {} };

const post = (url: string, body: string) =>
  new Request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body });

const postJson = (url: string, body: unknown) => post(url, JSON.stringify(body));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("[L2] handleTts — status 表", () => {
  it("returns 200 audio/wav when VOICEVOX synthesizes", async () => {
    // 無いと: 合成が成功しても 204 を返す等の退行が、L3-real (VOICEVOX 未設定) をすり抜ける
    vi.mocked(synthesizeTts).mockResolvedValue(new Uint8Array([1, 2, 3, 4]).buffer);

    const res = await handleTts(postJson("http://x/api/tts", { text: "あ" }), silent);

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("audio/wav");
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3, 4]));
  });

  it("returns 204 (no body) when VOICEVOX is unconfigured", async () => {
    // 無いと: 未構成時に 200 + 空ボディを返し、フロントがブラウザ読み上げへ縮退できない
    vi.mocked(synthesizeTts).mockResolvedValue(null);

    const res = await handleTts(postJson("http://x/api/tts", { text: "あ" }), silent);

    expect(res.status).toBe(204);
    expect(res.headers.get("content-type")).toBeNull();
  });

  it.each([
    { status: "cached" as const, expected: 202 },
    { status: "pending" as const, expected: 202 },
    { status: "stub" as const, expected: 204 },
  ])("prefetch $status → $expected (音声は返さない)", async ({ status, expected }) => {
    // 無いと: **2026-08-08 の事故そのもの** — プリフェッチの status が変わっても誰も気づかない
    vi.mocked(prefetchTts).mockResolvedValue({ status, sentences: 1 });

    const res = await handleTts(
      postJson("http://x/api/tts", { text: "あ。", prefetch: true }),
      silent,
    );

    expect(res.status).toBe(expected);
    expect(res.body).toBeNull();
    expect(synthesizeTts).not.toHaveBeenCalled();
  });

  it("returns 502 when synthesis throws", async () => {
    // 無いと: 下流障害が 200 + 壊れたバイト列として返り、ブラウザ側で謎の再生失敗になる
    vi.mocked(synthesizeTts).mockRejectedValue(new Error("engine down"));

    const res = await handleTts(postJson("http://x/api/tts", { text: "あ" }), silent);

    expect(res.status).toBe(502);
  });

  it("distinguishes a malformed body (400 Invalid JSON body) from a schema violation", async () => {
    // 無いと: 「body が壊れている」と「フィールドが足りない」が同じ文言になり、切り分けが消える
    const broken = await handleTts(post("http://x/api/tts", "{not json"), silent);
    expect(broken.status).toBe(400);
    expect(await broken.text()).toBe("Invalid JSON body");

    const invalid = await handleTts(postJson("http://x/api/tts", { text: "" }), silent);
    expect(invalid.status).toBe(400);
    expect(await invalid.text()).toContain("Invalid request");
  });
});

describe("[L2] handleChatStream — status 表", () => {
  it("returns 200 text/event-stream and passes the upstream stream through", async () => {
    // 無いと: Content-Type が落ちて逐次配信にならない / body を握り潰す退行が通る
    vi.mocked(openChatStream).mockResolvedValue(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: {"type":"delta","text":"あ"}\n\n'));
          controller.close();
        },
      }),
    );

    const res = await handleChatStream(
      postJson("http://x/api/chat/stream", { sessionId: "s1", message: "hi" }),
      silent,
    );

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("cache-control")).toBe("no-cache");
    expect(await res.text()).toContain('"type":"delta"');
  });

  it("returns 502 when the upstream cannot be opened", async () => {
    // 無いと: フロントが SSE 失敗を検知できず、tRPC フォールバックに切り替わらない
    vi.mocked(openChatStream).mockRejectedValue(new Error("upstream down"));

    const res = await handleChatStream(
      postJson("http://x/api/chat/stream", { sessionId: "s1", message: "hi" }),
      silent,
    );

    expect(res.status).toBe(502);
  });

  it("distinguishes a malformed body from a schema violation", async () => {
    const broken = await handleChatStream(post("http://x/api/chat/stream", "{"), silent);
    expect(broken.status).toBe(400);
    expect(await broken.text()).toBe("Invalid JSON body");

    const invalid = await handleChatStream(
      postJson("http://x/api/chat/stream", { sessionId: "s1", message: "" }),
      silent,
    );
    expect(invalid.status).toBe(400);
    expect(await invalid.text()).toContain("Invalid request");
    expect(openChatStream).not.toHaveBeenCalled();
  });
});

describe("[L2] handleWarmup / handleTrpc", () => {
  it("warmup returns 200 with the per-target measurements as JSON", async () => {
    const result = {
      aiAgent: { status: "ok" as const, ms: 120, httpStatus: 200 },
      voicevox: { status: "stub" as const, ms: null, httpStatus: null },
    };
    vi.mocked(warmupDownstreams).mockResolvedValue(result);

    const res = await handleWarmup(silent);

    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual(result);
  });

  it("trpc routes to the app router under /api/trpc", async () => {
    // 無いと: endpoint の綴りがずれて全手続きが 404 になる (入口の配線そのものの退行)
    const res = await handleTrpc(new Request("http://x/api/trpc/health.ping"), silent);

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ result: { data: { ok: true } } });
  });
});
