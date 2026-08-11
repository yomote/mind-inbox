import type { TtsStatus } from "../../voice/useTextToSpeech";

/**
 * マスコットの状態 3 種 (dialogue-session.mdx §5.6 / ADR 0039 D5)。
 * - idle:      待機。応答も読み上げも進行していない
 * - preparing: AI 応答の生成中 or 音声合成中 (「ずんだもんが準備中…」の区間)
 * - speaking:  読み上げの再生中
 */
export type MascotState = "idle" | "preparing" | "speaking";

/**
 * 既存の進行状態をマスコットの状態 3 種に写す (dialogue-session.mdx §5.6)。
 * - 読み上げ再生中 → speaking
 * - AI 応答の生成中 (loading = ai-thinking / ai-streaming) or 音声合成中 → preparing
 * - それ以外 → idle
 */
export function deriveMascotState(loading: boolean, ttsStatus: TtsStatus | undefined): MascotState {
  if (ttsStatus === "playing") return "speaking";
  if (ttsStatus === "synthesizing" || loading) return "preparing";
  return "idle";
}
