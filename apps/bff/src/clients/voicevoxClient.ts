import { config } from "../config";

export type SynthesizeRequest = {
  text: string;
  speakerId?: number;
};

/**
 * voicevox-wrapper への HTTP クライアント。
 *
 * Wrapper は audio/wav のバイナリを直接返すため、戻り値は ArrayBuffer。
 * VOICEVOX_BASE_URL 未設定時は null を返し、呼び出し元が 204 などで処理する。
 */
export async function synthesize(req: SynthesizeRequest): Promise<ArrayBuffer | null> {
  if (!config.voicevoxBaseUrl) {
    console.log("[voicevoxClient] VOICEVOX_BASE_URL not set — returning null (stub)");
    return null;
  }

  const url = `${config.voicevoxBaseUrl}/synthesize`;
  console.log(`[voicevoxClient] POST ${url}`);

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: req.text,
      speaker: req.speakerId ?? 3,
    }),
  });

  if (!res.ok) {
    throw new Error(`voicevoxClient: POST /synthesize failed — ${res.status} ${res.statusText}`);
  }

  return await res.arrayBuffer();
}
