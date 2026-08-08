import { app, HttpRequest, HttpResponseInit, InvocationContext } from "@azure/functions";
import { warmupDownstreams } from "../warmup/warmupService";

/**
 * GET /api/warmup — scale-to-zero の下流 (ai-agent / vv-wrap) を温める (#120)。
 *
 * フロントがホーム表示時に fire-and-forget で呼ぶ。応答 JSON には per-target の
 * 実測 ms が入る (コールドスタートの観測データとして #120 の計測にも使う)。
 * EasyAuth の門の内側 (フロントは Authorization 付きで呼ぶ / ADR 0017)。
 */
async function warmupHandler(
  _req: HttpRequest,
  context: InvocationContext,
): Promise<HttpResponseInit> {
  const result = await warmupDownstreams();
  context.log(
    `[warmup] aiAgent=${result.aiAgent.status}(${result.aiAgent.ms}ms) ` +
      `voicevox=${result.voicevox.status}(${result.voicevox.ms}ms)`,
  );
  return { status: 200, jsonBody: result };
}

app.http("warmup", {
  methods: ["GET"],
  authLevel: "anonymous",
  route: "warmup",
  handler: warmupHandler,
});
