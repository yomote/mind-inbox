/**
 * Azure Speech によるサーバー STT (#121 / ADR 0023)。
 *
 * - トークンは BFF (`speech.issueToken`) が Managed Identity で発行する
 *   `aad#{resourceId}#{entraToken}` 形式。SPA にキーは渡らない
 * - BFF が available:false を返したら null を返す = 呼び出し側 (useVoiceInput) が
 *   Web Speech へフォールバックする
 * - SDK (~数百 KB) は動的 import で分離し、Azure を使う時だけロードする
 */

import { trpc } from "../trpc/client";

export type RecognitionHandlers = {
  onFinal: (text: string) => void;
  onInterim: (text: string) => void;
  /** 継続不能なエラー (ネットワーク断・トークン失効・無料枠停止など)。 */
  onFatal: (message: string) => void;
};

export type RunningRecognition = {
  stop: () => void;
};

/**
 * Azure Speech の連続認識を開始する。
 *
 * @returns 開始できたら stop ハンドル。BFF がトークンを発行できない環境
 *          (ローカル / 未プロビジョニング) では null (= Web Speech へフォールバック)。
 * @throws トークンはあるのに SDK の初期化・マイク取得・接続に失敗した場合。
 */
export async function startAzureRecognition(
  handlers: RecognitionHandlers,
  lang = "ja-JP",
): Promise<RunningRecognition | null> {
  const token = await trpc.speech.issueToken.query();
  if (!token.available) return null;

  const sdk = await import("microsoft-cognitiveservices-speech-sdk");

  const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(token.authToken, token.region);
  speechConfig.speechRecognitionLanguage = lang;

  const audioConfig = sdk.AudioConfig.fromDefaultMicrophoneInput();
  const recognizer = new sdk.SpeechRecognizer(speechConfig, audioConfig);

  recognizer.recognizing = (_sender, event) => {
    handlers.onInterim(event.result.text ?? "");
  };

  recognizer.recognized = (_sender, event) => {
    if (event.result.reason === sdk.ResultReason.RecognizedSpeech) {
      handlers.onFinal(event.result.text ?? "");
    }
  };

  recognizer.canceled = (_sender, event) => {
    if (event.reason === sdk.CancellationReason.Error) {
      handlers.onFatal(event.errorDetails || "音声認識サービスとの接続が中断されました。");
    }
  };

  await new Promise<void>((resolve, reject) => {
    recognizer.startContinuousRecognitionAsync(resolve, (err) =>
      reject(new Error(err || "Azure Speech の認識開始に失敗しました")),
    );
  });

  return {
    stop: () => {
      recognizer.stopContinuousRecognitionAsync(
        () => recognizer.close(),
        () => recognizer.close(),
      );
    },
  };
}

/**
 * 開始の結果。**想定内の不在 (unavailable) と予期しない失敗 (failed) を区別する**のが要点。
 *
 * BFF 側 (speechTokenClient) は「MSI 取得失敗は握り潰さず throw する」を設計意図としている
 * (未設定・ローカルだけが available:false)。フロントで両者を同じ catch に落とすと、
 * 本番で MI の権限剥がれが起きても「動くが精度が落ちている」だけになり誰も気づけない。
 * ここで分離し、failed は必ずログに残して UI にも劣化として出す。
 */
export type AzureStartOutcome =
  | { kind: "started"; recognition: RunningRecognition }
  /** 想定内: BFF が available:false (未プロビジョニング / ローカル)。静かに Web Speech へ。 */
  | { kind: "unavailable" }
  /** 予期しない失敗: トークン取得エラー・SDK 初期化・マイク取得・接続失敗。 */
  | { kind: "failed"; reason: string };

/**
 * `startAzureRecognition` を outcome 型に包む。
 *
 * @param starter DI 用 (テストから差し替える)。既定は実装本体。
 */
export async function tryStartAzureRecognition(
  handlers: RecognitionHandlers,
  starter: (h: RecognitionHandlers) => Promise<RunningRecognition | null> = startAzureRecognition,
): Promise<AzureStartOutcome> {
  try {
    const recognition = await starter(handlers);
    if (!recognition) return { kind: "unavailable" };
    return { kind: "started", recognition };
  } catch (e) {
    const reason = e instanceof Error ? e.message : String(e);
    // 静かな品質劣化にしない (BFF 側の「握り潰さない」意図をフロントでも保つ)。
    console.error(
      `[voice] Azure Speech の開始に失敗したためブラウザ認識へフォールバックします: ${reason}`,
    );
    return { kind: "failed", reason };
  }
}
