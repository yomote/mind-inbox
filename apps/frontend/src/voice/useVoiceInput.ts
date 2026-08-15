/**
 * 音声入力 hook (#121 / ADR 0023)。session 画面の入力部が所有する。
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
import {
  peekAzurePreparation,
  prepareAzureRecognition,
  startPreparedAzureRecognition,
  tryStartAzureRecognition,
  type RecognitionHandlers,
  type RunningRecognition,
} from "./azureSpeech";
import { PlaybackEchoGate } from "./echoGate";
import { TranscriptStitcher } from "./stitcher";

export type VoiceEngineKind = "azure" | "browser";

/**
 * 音声入力の進行状態 (#186)。
 *
 * `preparing` を分けているのは、押してからマイクが開くまでの区間に喋られた内容が
 * 落ちるため。「押した = もう聞いている」ではないことを画面に出す必要がある。
 */
export type VoiceInputPhase = "idle" | "preparing" | "listening";

export type VoiceInput = {
  /** このブラウザ・環境で音声入力が使えるか (false ならボタンを disabled にする)。 */
  supported: boolean;
  listening: boolean;
  /** 停止中 / マイク準備中 / 聞いている。 */
  phase: VoiceInputPhase;
  /** 認識中のエンジン (listening 中のみ non-null)。 */
  engine: VoiceEngineKind | null;
  /**
   * 高精度認識 (Azure) を使うはずが**予期しない失敗**でブラウザ認識に落ちた状態。
   * 想定内の未プロビジョニング (available:false) では立たない — 静かな品質劣化を
   * 見えるようにするためのフラグ。
   */
  degraded: boolean;
  /**
   * TTS 再生中で認識結果を破棄している状態 (#228)。エンジンは動き続けているが、
   * この間の発話 (合成音声もユーザーも) は入力欄に入らない。UI はこれを見せる。
   */
  muted: boolean;
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

export type VoiceInputOptions = {
  /**
   * TTS (ずんだもん / ブラウザ読み上げ) が実際に音を出しているか (#228)。
   * true の間に届いた認識結果は破棄する — スピーカーの合成音声をユーザー発話として
   * 拾ってしまうエコーを防ぐ。false に戻ると自動で認識結果が流れ始める。
   */
  ttsPlaying?: boolean;
};

/**
 * @param appendText 確定した認識テキストを受け取り入力欄へ追記するコールバック。
 *                   identity が変わっても認識は継続する (ref 経由で常に最新を呼ぶ)。
 */
export function useVoiceInput(
  appendText: (text: string) => void,
  { ttsPlaying = false }: VoiceInputOptions = {},
): VoiceInput {
  const [listening, setListening] = React.useState(false);
  const [phase, setPhase] = React.useState<VoiceInputPhase>("idle");
  const [azureReady, setAzureReady] = React.useState(false);
  const [engine, setEngine] = React.useState<VoiceEngineKind | null>(null);
  const [degraded, setDegraded] = React.useState(false);
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

  // 縫い合わせ器は 1 つを hook の寿命ぶん使い回す。**生成は render 外 (mount effect)**
  // で行う — render 中に ref を触る形にすると、認識イベント用の口 (appendRef) を
  // コンストラクタに渡すことになり react-hooks/refs に弾かれる。
  const stitcherRef = React.useRef<TranscriptStitcher | null>(null);
  React.useEffect(() => {
    stitcherRef.current ??= new TranscriptStitcher({
      onCommit: (text) => appendRef.current(text),
      onInterim: (text) => setInterimTranscript(text),
    });
  }, []);

  // TTS 再生中のエコーを堰き止める門 (#228)。stitcher と同じく mount effect で生成する。
  const gateRef = React.useRef<PlaybackEchoGate | null>(null);
  React.useEffect(() => {
    gateRef.current ??= new PlaybackEchoGate();
  }, []);

  // TTS の再生開始 / 終了を門へ伝える。再生開始時点で残っている未確定 interim は
  // **ユーザー発話** (再生前に聞いたもの) なので、破棄せず確定させてから門を閉じる。
  React.useEffect(() => {
    const gate = (gateRef.current ??= new PlaybackEchoGate());
    if (ttsPlaying) stitcherRef.current?.flush();
    gate.setPlaying(ttsPlaying, Date.now());
  }, [ttsPlaying]);

  /**
   * 認識結果の配達口。**両エンジン (Azure / Web Speech) がここを通る**ことで、
   * TTS 再生中の結果破棄 (#228) がどの経路でも同じに効く。
   * 空 interim は「表示クリア」なので門に関わらず通す (汚染扱いにしない)。
   */
  const deliverFinal = React.useCallback((text: string) => {
    if (gateRef.current?.acceptFinal(Date.now()) === false) return;
    stitcherRef.current?.handleFinal(text);
  }, []);
  const deliverInterim = React.useCallback((text: string) => {
    if (text.trim() && gateRef.current?.acceptInterim(Date.now()) === false) return;
    stitcherRef.current?.handleInterim(text);
  }, []);

  // 「BFF があるなら使えるはず」ではなく**実際に開始できるエンジンがあるか**で決める。
  // 旧判定 (`|| !useMock`) は Web Speech 非対応ブラウザでもボタンを有効にしてしまい、
  // 押して初めて「対応していません」と出る作りだった (#186)。
  const supported = Boolean(speechRecognitionCtor()) || azureReady;

  // タップより前にトークンと SDK を用意しておく (#186)。これが済んでいると
  // start() が同期で完結し、iOS Safari でもマイクがジェスチャ内で開く。
  React.useEffect(() => {
    if (useMock) return;
    let active = true;
    void prepareAzureRecognition().then((p) => {
      if (active) setAzureReady(p.kind === "ready");
    });
    return () => {
      active = false;
    };
  }, []);

  // 録音状態の可視化: listening 中は経過秒を刻む。
  // 0 へのリセットは effect 本体ではなく cleanup で行う (録音停止 = 依存変化の瞬間に走る)。
  // effect 本体の同期 setState は react-hooks 7.1 の set-state-in-effect が error にする。
  React.useEffect(() => {
    if (!listening) return;
    const timer = window.setInterval(() => {
      const startedAt = startedAtRef.current;
      if (startedAt !== null) {
        setElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
      }
    }, 500);
    return () => {
      window.clearInterval(timer);
      setElapsedSec(0);
    };
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
    // エンジンを止めた = 追跡中の (汚染) セグメントごと消えた (#228)。
    gateRef.current?.resetSegment();
    // ユーザー停止時点の未確定 interim も捨てない (冪等なので onend との重複は無害)。
    stitcherRef.current?.flush();
    setListening(false);
    setPhase("idle");
    setEngine(null);
  }, [stopEngines]);

  const startBrowserEngine = React.useCallback(() => {
    const ctor = speechRecognitionCtor();
    if (!ctor) {
      shouldListenRef.current = false;
      setListening(false);
      setPhase("idle");
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
          deliverFinal(finalText);
        }
        deliverInterim(interimText);
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
        stitcherRef.current?.flush();
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
        setPhase("idle");
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
    setPhase("listening");
  }, [deliverFinal, deliverInterim]);

  /** Azure 認識のイベント配線。同期経路 / 非同期経路のどちらからも同じものを使う。 */
  const azureHandlers = React.useMemo<RecognitionHandlers>(
    () => ({
      onFinal: (text) => deliverFinal(text),
      onInterim: (text) => deliverInterim(text),
      onFatal: (message) => {
        // Azure が途中で継続不能になったら (ネットワーク断・無料枠停止 等)、
        // 発話を失わないよう Web Speech に切り替えて続行を試みる。
        // これも「予期しない失敗」なので劣化として記録・表示する。
        console.error(`[voice] Azure Speech の認識が中断されました: ${message}`);
        if (azureRef.current) {
          azureRef.current.stop();
          azureRef.current = null;
        }
        stitcherRef.current?.flush();
        if (shouldListenRef.current && speechRecognitionCtor()) {
          setDegraded(true);
          startBrowserEngine();
        } else if (shouldListenRef.current) {
          shouldListenRef.current = false;
          setListening(false);
          setPhase("idle");
          setEngine(null);
          setError(message);
        }
      },
    }),
    [deliverFinal, deliverInterim, startBrowserEngine],
  );

  const start = React.useCallback(() => {
    if (shouldListenRef.current) return;
    shouldListenRef.current = true;
    const seq = (sessionSeqRef.current += 1);
    setError(null);
    setDegraded(false);
    startedAtRef.current = Date.now();
    setElapsedSec(0);
    setListening(true);
    setPhase("preparing");

    // mock デモは BFF が無いのでトークン照会せず Web Speech へ直行。
    if (useMock) {
      startBrowserEngine();
      return;
    }

    // --- 同期経路 (#186) -----------------------------------------------------
    // ここから下で await を挟むとユーザージェスチャが切れる。iOS Safari は
    // ジェスチャ外の getUserMedia / AudioContext を許可済みでも実質無効にするため、
    // **準備済みのものだけを使って同期でマイクを開く**のがこの分岐の目的。

    const prepared = peekAzurePreparation();
    if (prepared?.kind === "ready") {
      try {
        azureRef.current = startPreparedAzureRecognition(prepared, azureHandlers);
        setEngine("azure");
        setPhase("listening");
        return;
      } catch (err) {
        // 準備は済んでいたのに開始できなかった = 予期しない失敗。劣化として見せる。
        console.error(
          `[voice] 準備済み Azure Speech の開始に失敗しました: ${(err as Error).message}`,
        );
        setDegraded(true);
      }
    } else if (prepared?.kind === "failed") {
      setDegraded(true);
    }

    // Azure が準備できていない / 使えない → その場でブラウザ認識を同期開始する。
    // 「待たせて何も起きない」より「今すぐ拾い始める」を優先する。
    if (speechRecognitionCtor()) {
      startBrowserEngine();
      // 次のタップで高精度認識に乗れるよう、裏で準備を進めておく
      // (この認識セッションの途中でエンジンを差し替えると発話を取りこぼす)。
      void prepareAzureRecognition().then((p) => setAzureReady(p.kind === "ready"));
      return;
    }

    // --- 非同期経路 ----------------------------------------------------------
    // ブラウザ認識が無く Azure しか道が無い環境。ジェスチャは失うが、
    // 何も開始しないよりは接続を試みる (デスクトップ系はジェスチャ要件が緩い)。
    void (async () => {
      const outcome = await tryStartAzureRecognition(azureHandlers);

      // stop() 済み / 別世代なら破棄。
      if (!shouldListenRef.current || sessionSeqRef.current !== seq) {
        if (outcome.kind === "started") outcome.recognition.stop();
        return;
      }

      if (outcome.kind === "started") {
        azureRef.current = outcome.recognition;
        setEngine("azure");
        setPhase("listening");
        return;
      }

      // 予期しない失敗 (トークン取得エラー / SDK 初期化 / マイク取得) は
      // ログ済み (tryStartAzureRecognition) の上で劣化として UI に出す。
      // 想定内の unavailable (未プロビジョニング / ローカル) は静かにフォールバック。
      if (outcome.kind === "failed") setDegraded(true);
      startBrowserEngine();
    })();
  }, [azureHandlers, startBrowserEngine]);

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
    phase,
    engine,
    degraded,
    muted: listening && ttsPlaying,
    interimTranscript,
    elapsedSec,
    error,
    toggle,
    stop,
  };
}
