import { config } from "../config";
import { logEvent, trackDependency } from "../observability/telemetry";
import { serviceHeaders } from "./serviceToken";

export type SynthesizeRequest = {
  text: string;
  speakerId?: number;
  /** 読み上げ速度 (#242)。未指定なら wrapper 側で VOICEVOX の既定 (等倍) のまま。 */
  speedScale?: number;
};

/** VOICEVOX が結線されているか。合成せずに stub 判定だけしたい呼び出し元が使う。 */
export function isConfigured(): boolean {
  return Boolean(config.voicevoxBaseUrl);
}

/**
 * voicevox-wrapper への HTTP クライアント。
 *
 * Wrapper は audio/wav のバイナリを直接返すため、戻り値は ArrayBuffer。
 * VOICEVOX_BASE_URL 未設定時は null を返し、呼び出し元が 204 などで処理する。
 */
export async function synthesize(req: SynthesizeRequest): Promise<ArrayBuffer | null> {
  if (!config.voicevoxBaseUrl) {
    logEvent("dependency.skipped", { target: "voicevox", reason: "base-url-unset" });
    return null;
  }

  const url = `${config.voicevoxBaseUrl}/synthesize`;

  // 開始と終了を対で残す (#307)。**text は送るがログには出さない** — 読み上げ対象は
  // AI の応答本文そのもの。残すのは長さと所要時間と結果だけ。
  return await trackDependency(
    { target: "voicevox", operation: "POST /synthesize", url },
    async () => {
      const res = await fetch(url, {
        method: "POST",
        headers: await serviceHeaders(config.voicevoxAudience, {
          "Content-Type": "application/json",
        }),
        body: JSON.stringify({
          text: req.text,
          speaker: req.speakerId ?? 3,
          // **wrapper のフィールド名は snake_case** (pydantic `SynthesizeRequest`)。
          // `speedScale` で送ると pydantic が黙って無視し、等倍のまま鳴る (#242)。
          // 未指定は送らない — null を送ると wrapper 側で速度指定なしと同義だが、
          // 「送っていない」と「速度を null にした」が区別できなくなる。
          ...(req.speedScale === undefined ? {} : { speed_scale: req.speedScale }),
        }),
      });

      if (!res.ok) {
        throw new Error(
          `voicevoxClient: POST /synthesize failed — ${res.status} ${res.statusText}`,
        );
      }

      return await res.arrayBuffer();
    },
    (audio) => ({ bytes: audio.byteLength }),
  );
}
