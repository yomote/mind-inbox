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
 * - AI 応答の生成中 (loading = ai-thinking / ai-streaming) or 音声合成中 → preparing
 * - 読み上げ再生中 → speaking
 * - それ以外 → idle
 *
 * **loading (応答待ち) を再生表現 (playing) より優先する**: 前の応答の読み上げ中に
 * 次のメッセージを送ることができ、そのとき speaking を返すと SessionMessages の
 * 「考え中」プレースホルダ (preparing のときだけ出す) が現れず、送信が無反応に見える。
 * 「新しい問いへの応答待ちの可視化 > 前の応答の再生表現」で倒す。
 */
export function deriveMascotState(loading: boolean, ttsStatus: TtsStatus | undefined): MascotState {
  if (loading || ttsStatus === "synthesizing") return "preparing";
  if (ttsStatus === "playing") return "speaking";
  return "idle";
}
