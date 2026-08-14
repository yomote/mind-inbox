/**
 * [L2] HTTP ハンドラの **status / ヘッダ表**を固定する。
 *
 * なぜこれが要るか: BFF には入口が 2 つある (Azure Functions / local-server) ため、
 * `handlers.ts` に status の決定を集約した。**が、集約しただけでは status のズレは止まらない。**
 *
 * 実際 2026-08-08 に「TTS のプリフェッチが 202 を返すようになったのに L4 が 200 を
 * 期待し続けて落ちる」事故が起きている ([ADR 0032](../../../../docs/adr/archive/operations/use-case-acceptance-tests-against-real-wiring.md) の動機)。
 * L3-real は VOICEVOX 未設定 (204) の経路しか通らないので、**200 / 202 / 502 の分岐は
 * どのテストも踏んでいなかった** (PR #170 のレビュー指摘)。ここがその穴を塞ぐ。
 *
 * ここで test しないこと:
 *   - 下流 (VOICEVOX / ai-agent) との実 HTTP — それぞれの client / service の範疇
 *   - tRPC の手続きの中身 — router.test.ts
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../chat/chatStream", () => ({ openChatStream: vi.fn() }));
vi.mock("../tts/ttsService", () => ({
  planTts: vi.fn(),
  prefetchTts: vi.fn(),
  synthesizeTts: vi.fn(),
}));
vi.mock("../warmup/warmupService", () => ({ warmupDownstreams: vi.fn() }));

import { openChatStream } from "../chat/chatStream";
import { planTts, prefetchTts, synthesizeTts } from "../tts/ttsService";
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

  it("plan → 200 で文の並びだけを返し、合成には行かない", async () => {
    // 無いと: #185 の逐次再生が「全文一括」に戻っても status は 200 のままなので気づけない。
    // plan で合成まで走ると VOICEVOX を二重に叩く (音は鳴るので他の手段では見えない)。
    vi.mocked(planTts).mockReturnValue({ status: "ok", sentences: ["あ。", "い。"] });

    const res = await handleTts(postJson("http://x/api/tts", { text: "あ。い。", plan: true }), silent);

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ sentences: ["あ。", "い。"] });
    expect(synthesizeTts).not.toHaveBeenCalled();
    expect(prefetchTts).not.toHaveBeenCalled();
  });

  it("plan stub → 204 (VOICEVOX 未構成)", async () => {
    // 無いと: 未構成でも 200 + 空の並びが返り、フロントがブラウザ読み上げへ縮退できない
    vi.mocked(planTts).mockReturnValue({ status: "stub" });

    const res = await handleTts(postJson("http://x/api/tts", { text: "あ。", plan: true }), silent);

    expect(res.status).toBe(204);
    expect(res.body).toBeNull();
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

/**
 * [単体] tRPC の失敗をテレメトリに落とすときの**値の落とし方**を固定する。
 *
 * `telemetry.ts` の許可リストは**名前しか見ない**ので、`errorMessage` という
 * 許可された名前に入力値入りの文面を入れれば防壁は素通りする。ここはその
 * 「許可された名前から漏れる」経路を塞いだことを、入口 (`handleTrpc`) を
 * 実際に叩いて確かめる。
 */
describe("[単体] handleTrpc — 失敗ログに入力値を載せない", () => {
  /** 相談の本文に相当する値。これがログ行に現れたら事故。 */
  const SECRET = "会社を辞めたいと誰にも言えていない";

  function recorder() {
    const errors: string[] = [];
    return { errors, log: () => {}, error: (m: string) => errors.push(m) };
  }

  it("アプリが組み立てた文面 (Problem not found: <入力>) を出さない", async () => {
    // 無いと何が静かに通るか: `problem.get` の id は自由文字列なので、
    // 相談の本文をそのまま入れると `Problem not found: <本文>` が
    // errorMessage として Application Insights に 30 日残る。
    const log = recorder();

    const res = await handleTrpc(
      new Request(
        `http://x/api/trpc/problem.get?input=${encodeURIComponent(JSON.stringify({ id: SECRET }))}`,
      ),
      log,
    );

    expect(res.status).toBe(404);
    expect(log.errors).toHaveLength(1);
    expect(log.errors[0]).not.toContain(SECRET);
    expect(log.errors[0]).toContain("event=trpc.error");
    expect(log.errors[0]).toContain("route=problem.get");
    expect(log.errors[0]).toContain("errorType=NOT_FOUND");
  });

  it("zod の検証失敗も受信値ではなく path と種別だけを出す", async () => {
    // 無いと何が静かに通るか: TRPCError.message は ZodError の JSON で、
    // `invalid_enum_value` などは**受け取った値そのもの**を含む。
    const log = recorder();

    const res = await handleTrpc(
      new Request(
        `http://x/api/trpc/problem.list?input=${encodeURIComponent(
          JSON.stringify({ theme: SECRET }),
        )}`,
      ),
      log,
    );

    expect(res.status).toBe(400);
    expect(log.errors).toHaveLength(1);
    expect(log.errors[0]).not.toContain(SECRET);
    expect(log.errors[0]).toContain("errorType=BAD_REQUEST");
    // 場所と壊れ方は残る (残らないと切り分けができない)
    expect(log.errors[0]).toContain("theme");
  });
});
