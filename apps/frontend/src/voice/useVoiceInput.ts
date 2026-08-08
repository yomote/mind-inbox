/**
 * 音声入力 hook (#121 / ADR 0022)。session 画面の入力部が所有する。
 *
 * エンジン選択 (ユーザーには自動):
 *   1. Azure Speech — BFF がトークンを発行できる環境で優先 (精度・長時間対応の根本改善)
 *   2. Web Speech API — mock モード / トークン発行不可 / Azure 障害時のフォールバック
 *
 * Web Speech は無音・時間制限で勝手に止まる (数十秒で途切れる #121 の症状) ため、
 * ユーザーが停止するまで `onend` で自動再開する。再開境界で final 化されなかった
 * interim は TranscriptStitcher が縫い込み、発話の末尾欠落を防ぐ。
 */

import * as React from "react";
import { startAzureRecognition, type RunningRecognition } from "./azureSpeech";
import { TranscriptStitcher } from "./stitcher";

export type VoiceEngineKind = "azure" | "browser";

export type VoiceInput = {
  /** このブラウザ・環境で音声入力が使えるか (false ならボタンを disabled にする)。 */
  supported: boolean;
  listening: boolean;
  /** 認識中のエンジン (listening 中のみ non-null)。 */
  engine: VoiceEngineKind | null;
  interimTranscript: string;
  /** 認識開始からの経過秒。録音状態の可視化に使う。 */
  elapsedSec: number;
  error: string | null;
  toggle: () => void;
  stop: () => void;
};

// mock モード (VITE_USE_MOCK) は BFF の無い自己完結デモなのでトークン照会をしない。
const useMock = import.meta.env.VITE_USE_MOCK === "true";

function speechRecognitionCtor(): SpeechRecognitionConstructor | undefined {
  if (typeof window === "undefined") return undefined;
  return window.SpeechRecognition || window.webkitSpeechRecognition;
}

/**
 * @param appendText 確定した認識テキストを受け取り入力欄へ追記するコールバック。
 *                   identity が変わっても認識は継続する (ref 経由で常に最新を呼ぶ)。
 */
export function useVoiceInput(appendText: (text: string) => void): VoiceInput {
  const [listening, setListening] = React.useState(false);
  const [engine, setEngine] = React.useState<VoiceEngineKind | null>(null);
  const [interimTranscript, setInterimTranscript] = React.useState("");
  const [elapsedSec, setElapsedSec] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);

  const appendRef = React.useRef(appendText);
  React.useEffect(() => {
    appendRef.current = appendText;
  });

  // ユーザーが「聞き続けてほしい」状態か (勝手に止まったら再開する判定に使う)。
  const shouldListenRef = React.useRef(false);
  // start/stop の世代番号。stop 後に解決した非同期 start (Azure) を無効化する。
  const sessionSeqRef = React.useRef(0);
  const browserRecognitionRef = React.useRef<SpeechRecognition | null>(null);
  // 認識エンジンが実際に走っているか (二重 start による InvalidStateError を防ぐ)。
  const browserRunningRef = React.useRef(false);
  const azureRef = React.useRef<RunningRecognition | null>(null);
  const startedAtRef = React.useRef<number | null>(null);

  const stitcherRef = React.useRef<TranscriptStitcher | null>(null);
  if (!stitcherRef.current) {
    stitcherRef.current = new TranscriptStitcher({
      onCommit: (text) => appendRef.current(text),
      onInterim: (text) => setInterimTranscript(text),
    });
  }
  const stitcher = stitcherRef.current;

  const supported = Boolean(speechRecognitionCtor()) || !useMock;

  // 録音状態の可視化: listening 中は経過秒を刻む。
  React.useEffect(() => {
    if (!listening) {
      setElapsedSec(0);
      return;
    }
    const timer = window.setInterval(() => {
      const startedAt = startedAtRef.current;
      if (startedAt !== null) {
        setElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
      }
    }, 500);
    return () => window.clearInterval(timer);
  }, [listening]);

  const stopEngines = React.useCallback(() => {
    if (azureRef.current) {
      azureRef.current.stop();
      azureRef.current = null;
    }
    const recognition = browserRecognitionRef.current;
    if (recognition) {
      try {
        recognition.stop();
      } catch {
        // 既に停止済みなどは無視。
      }
    }
  }, []);

  const stop = React.useCallback(() => {
    // 意図を先に落とす → onend が再開しないようにしてから停止。
    shouldListenRef.current = false;
    sessionSeqRef.current += 1;
    stopEngines();
    // ユーザー停止時点の未確定 interim も捨てない (冪等なので onend との重複は無害)。
    stitcher.flush();
    setListening(false);
    setEngine(null);
  }, [stitcher, stopEngines]);

  const startBrowserEngine = React.useCallback(() => {
    const ctor = speechRecognitionCtor();
    if (!ctor) {
      shouldListenRef.current = false;
      setListening(false);
      setEngine(null);
      setError("このブラウザは音声入力に対応していません。");
      return;
    }

    if (!browserRecognitionRef.current) {
      const recognition = new ctor();
      recognition.lang = "ja-JP";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        browserRunningRef.current = true;
      };

      recognition.onresult = (event: SpeechRecognitionEvent) => {
        let finalText = "";
        let interimText = "";
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i];
          const transcript = result[0]?.transcript ?? "";
          if (result.isFinal) {
            finalText += transcript;
          } else {
            interimText += transcript;
          }
        }
        if (finalText.trim()) {
          stitcher.handleFinal(finalText);
        }
        stitcher.handleInterim(interimText);
      };

      recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
        const err = event.error;
        // continuous モードでは沈黙や中断で頻繁に出る。無害なので握り潰し、
        // 継続は onend → 自動再開に任せる (「エラー表示で固まった」体験を防ぐ)。
        if (err === "no-speech" || err === "aborted") return;
        // マイク権限・デバイス起因は復帰不能なので意図を落として明示する。
        if (err === "not-allowed" || err === "service-not-allowed" || err === "audio-capture") {
          shouldListenRef.current = false;
          setError("マイクを使えませんでした。ブラウザのマイク許可を確認してください。");
          return;
        }
        setError(`音声認識エラー: ${err}`);
      };

      recognition.onend = () => {
        browserRunningRef.current = false;
        // 再開境界の縫い合わせ: final 化されなかった interim をここで確定させる (#121)。
        stitcher.flush();
        // 継続意図があるのに止まった (無音タイムアウト等) → 自動再開。
        // sync 再開は InvalidStateError を起こしやすいので次 tick で。
        if (shouldListenRef.current) {
          window.setTimeout(() => {
            if (!shouldListenRef.current || browserRunningRef.current) return;
            try {
              recognition.start();
            } catch {
              // まだ停止しきっていない場合などは次の onend で再試行される。
            }
          }, 150);
          return;
        }
        setListening(false);
        setEngine(null);
      };

      browserRecognitionRef.current = recognition;
    }

    if (!browserRunningRef.current) {
      try {
        browserRecognitionRef.current.start();
      } catch {
        // 既に開始中なら無視。
      }
    }
    setEngine("browser");
  }, [stitcher]);

  const start = React.useCallback(() => {
    if (shouldListenRef.current) return;
    shouldListenRef.current = true;
    const seq = (sessionSeqRef.current += 1);
    setError(null);
    startedAtRef.current = Date.now();
    setElapsedSec(0);
    setListening(true);

    // mock デモは BFF が無いのでトークン照会せず Web Speech へ直行。
    if (useMock) {
      startBrowserEngine();
      return;
    }

    void (async () => {
      try {
        const azure = await startAzureRecognition({
          onFinal: (text) => stitcher.handleFinal(text),
          onInterim: (text) => stitcher.handleInterim(text),
          onFatal: (message) => {
            // Azure が途中で継続不能になったら (ネットワーク断・無料枠停止 等)、
            // 発話を失わないよう Web Speech に切り替えて続行を試みる。
            if (azureRef.current) {
              azureRef.current.stop();
              azureRef.current = null;
            }
            stitcher.flush();
            if (shouldListenRef.current && speechRecognitionCtor()) {
              startBrowserEngine();
            } else if (shouldListenRef.current) {
              shouldListenRef.current = false;
              setListening(false);
              setEngine(null);
              setError(message);
            }
          },
        });

        // stop() 済み / 別世代なら破棄。
        if (!shouldListenRef.current || sessionSeqRef.current !== seq) {
          azure?.stop();
          return;
        }

        if (azure) {
          azureRef.current = azure;
          setEngine("azure");
          return;
        }
        // トークン発行不可 (available:false) → Web Speech へフォールバック。
        startBrowserEngine();
      } catch {
        // BFF 未起動・SDK 初期化失敗など。音声入力自体は失わない。
        if (!shouldListenRef.current || sessionSeqRef.current !== seq) return;
        startBrowserEngine();
      }
    })();
  }, [startBrowserEngine, stitcher]);

  const toggle = React.useCallback(() => {
    if (shouldListenRef.current) {
      stop();
      return;
    }
    start();
  }, [start, stop]);

  // 画面遷移 (unmount) で認識を止める。
  React.useEffect(() => {
    return () => {
      shouldListenRef.current = false;
      sessionSeqRef.current += 1;
      stopEngines();
    };
  }, [stopEngines]);

  return {
    supported,
    listening,
    engine,
    interimTranscript,
    elapsedSec,
    error,
    toggle,
    stop,
  };
}
