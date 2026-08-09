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

// ---- 事前準備 (#186) --------------------------------------------------------
// iOS Safari はマイク (getUserMedia / AudioContext) の open が**ユーザージェスチャの
// 同期処理の中**で起きないと、許可済みでも音を拾わない。従来はタップ後に
// 「トークン取得 → SDK の動的 import → マイク取得」と await を重ねていたため、
// 実際に開く頃にはジェスチャが切れており、押しても無反応に見えていた。
//
// 対策: トークンと SDK を**タップより前に**用意しておき、押下時は同期で開始する。

type SpeechSdk = typeof import("microsoft-cognitiveservices-speech-sdk");

export type AzurePreparation =
  | { kind: "ready"; sdk: SpeechSdk; authToken: string; region: string; expiresAt: number }
  /** 想定内: BFF が available:false (未プロビジョニング / ローカル)。 */
  | { kind: "unavailable" }
  /** 予期しない失敗: トークン取得エラー / SDK ロード失敗。 */
  | { kind: "failed"; reason: string };

/** Azure の認可トークンは 10 分で失効する。手前で切って取り直す。 */
const TOKEN_LIFETIME_MS = 8 * 60_000;

let preparation: AzurePreparation | null = null;
let preparing: Promise<AzurePreparation> | null = null;

function isFresh(p: AzurePreparation | null): boolean {
  return p !== null && (p.kind !== "ready" || p.expiresAt > Date.now());
}

/**
 * トークンと SDK を先に取得しておく。**セッション画面を開いた時点で呼ぶ**ことで、
 * マイクボタンのタップを同期処理だけで済ませられるようにする。
 *
 * 同時呼び出しは 1 本にまとめ、成功した準備は失効まで使い回す。
 */
export async function prepareAzureRecognition(): Promise<AzurePreparation> {
  if (isFresh(preparation)) return preparation as AzurePreparation;
  if (preparing) return preparing;

  preparing = (async (): Promise<AzurePreparation> => {
    try {
      const token = await trpc.speech.issueToken.query();
      if (!token.available) return { kind: "unavailable" };

      const sdk = await import("microsoft-cognitiveservices-speech-sdk");
      return {
        kind: "ready",
        sdk,
        authToken: token.authToken,
        region: token.region,
        expiresAt: Date.now() + TOKEN_LIFETIME_MS,
      };
    } catch (e) {
      const reason = e instanceof Error ? e.message : String(e);
      // 静かな品質劣化にしない (BFF 側の「握り潰さない」意図をフロントでも保つ)。
      console.error(`[voice] Azure Speech の事前準備に失敗しました: ${reason}`);
      return { kind: "failed", reason };
    }
  })();

  try {
    preparation = await preparing;
    return preparation;
  } finally {
    preparing = null;
  }
}

/** 準備状態を **await せずに** 見る。タップ時の分岐に使う (await した時点で負け)。 */
export function peekAzurePreparation(): AzurePreparation | null {
  return isFresh(preparation) ? preparation : null;
}

/**
 * 準備済みのトークン + SDK で連続認識を**同期的に**開始する。
 *
 * `startContinuousRecognitionAsync` の完了は待たない — 待つとその時点で
 * ジェスチャが切れ、iOS でマイクが開かない。開始失敗は onFatal で拾う。
 *
 * @throws 準備が無効 / SDK の構築に失敗した場合。
 */
export function startPreparedAzureRecognition(
  prepared: AzurePreparation,
  handlers: RecognitionHandlers,
  lang = "ja-JP",
): RunningRecognition {
  if (prepared.kind !== "ready") {
    throw new Error("Azure Speech の準備ができていません");
  }

  const { sdk } = prepared;
  const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(
    prepared.authToken,
    prepared.region,
  );
  speechConfig.speechRecognitionLanguage = lang;

  const audioConfig = sdk.AudioConfig.fromDefaultMicrophoneInput();
  const recognizer = new sdk.SpeechRecognizer(speechConfig, audioConfig);

  recognizer.recognizing = (_sender, event) => handlers.onInterim(event.result.text ?? "");
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

  recognizer.startContinuousRecognitionAsync(
    () => {},
    (err) => handlers.onFatal(err || "Azure Speech の認識開始に失敗しました"),
  );

  return {
    stop: () => {
      recognizer.stopContinuousRecognitionAsync(
        () => recognizer.close(),
        () => recognizer.close(),
      );
    },
  };
}

/** テスト用: モジュールに残った準備状態を捨てる。 */
export function resetAzurePreparationForTest(): void {
  preparation = null;
  preparing = null;
}

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
