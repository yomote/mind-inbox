/**
 * 読み上げ (TTS) hook。session 画面の読み上げを所有する。
 *
 * 経路は 2 本:
 *   1. VOICEVOX — BFF `/api/tts` 経由 (`api/http.ts` の `ttsFetch`)。合成済み WAV をキャッシュして再生
 *   2. ブラウザ内蔵 `speechSynthesis` — standalone (mock デモ) / VOICEVOX 未設定 (204) / 合成失敗時
 *
 * 分離の理由 (#141): 元は Layout.tsx が state 3 + ref 5 とこの分岐を抱えており、
 * 相談フローを触るたびに読み上げの回帰を考える必要があった。音声の変更は voice/ に閉じる。
 *
 * iOS は最初のユーザージェスチャ内で一度発話しておかないと以降の自動読み上げが無音になるため、
 * タップ起点で `unlock()` を呼ぶ (呼ばなくても機能はするが iOS で鳴らない)。
 */

import * as React from "react";
import { ttsFetch, ttsPlanFetch } from "../api/http";
import { acquireAudioElement, unlockAudioElement } from "./unlockedAudio";

/** 1 文ぶんの再生がどう決着したか (#185 の逐次再生キューが次の一手を決めるのに使う)。 */
type PlaybackOutcome = "ended" | "blocked" | "error" | "stopped";

/**
 * 同時に投げる合成リクエストの上限 (PR #190 レビュー指摘)。
 *
 * 長い応答をそのまま並行に出すと文の数だけ同時接続が開き、BFF の process 内キャッシュが
 * ミスしたときに VOICEVOX へ一斉に流れ込む。再生は順番待ちなので、先頭数文が焼けていれば
 * 音は途切れない — 先頭を最速で出す目的は上限を設けても損なわれない。
 */
const SYNTH_CONCURRENCY = 3;

/**
 * 順序を保ったまま並行数を制限して map する。**結果の Promise 配列を即座に返す**ので、
 * 呼び出し側は先頭から順に await して、焼けた文から再生できる。
 */
function mapWithLimit<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R>[] {
  const settlers: Array<{ resolve: (r: R) => void; reject: (e: unknown) => void }> = [];
  const results = items.map(
    (_, i) =>
      new Promise<R>((resolve, reject) => {
        settlers[i] = { resolve, reject };
      }),
  );

  let next = 0;
  const worker = async () => {
    for (;;) {
      const i = next++;
      if (i >= items.length) return;
      try {
        settlers[i].resolve(await fn(items[i]));
      } catch (err) {
        settlers[i].reject(err);
      }
    }
  };
  for (let w = 0; w < Math.min(limit, items.length); w += 1) void worker();

  return results;
}

/** VOICEVOX 未設定時に BFF が返す 204 を「合成できなかった」として扱うための番兵。 */
const TTS_STUB = "TTS_STUB";

/** 合成済み WAV の保持上限。超えたら古い順に捨てる。 */
const VOICE_CACHE_MAX = 30;

/** 劣化時のメッセージ。無言で別の声に置き換えない (dialogue-session.mdx §5.5)。 */
const MSG_SYNTH_STUB = "音声合成が未設定のため、ブラウザの読み上げで代用しています。";
const MSG_SYNTH_FAILED =
  "ずんだもんの音声を合成できませんでした。ブラウザの読み上げに切り替えています。";
const MSG_PLAYBACK_BLOCKED =
  "ブラウザに音声再生をブロックされました。画面をタップすると、ずんだもんの声に戻ります。";

export type TextToSpeechOptions = {
  /** dev / mock ビルド: BFF が無いのでネットワークを叩かずブラウザ読み上げに直行する。 */
  standalone: boolean;
  /** VOICEVOX の話者 ID。 */
  speaker: number;
};

/**
 * 実際に音を出した経路 (dialogue-session.mdx §5.5)。
 * `voicevox` 以外は「ずんだもん以外の声」であり劣化している。
 */
export type VoiceOutputMode = "idle" | "voicevox" | "browser-fallback";

/**
 * 読み上げの進行状態 (#185)。
 *
 * **合成中と再生中を分けるのが要点**。以前は両方 `speaking` 一本で、合成にかかる数秒〜
 * 十数秒のあいだ画面が何も変わらず、「待てば鳴るのか、もう終わったのか」が判別できなかった
 * (PO 報告 2026-08-09)。無音失敗の禁止 (ADR 0018) は「待たせていること」にも効く。
 */
export type TtsStatus = "idle" | "synthesizing" | "playing";

export type TextToSpeech = {
  /** 合成中か再生中 (= 何か進行中)。個別の区別は `status`。 */
  speaking: boolean;
  /** 合成中 / 再生中 / 停止中。UI はこれを出して待ち時間を可視化する。 */
  status: TtsStatus;
  /** 読み上げが有効か。false の間は speak / speakOnce が何もしない。 */
  enabled: boolean;
  error: string | null;
  /**
   * 直近に実際に音を出した経路。UI は `data-voice-output` として公開し、
   * L4 live E2E が「WAV は 200 なのに実際は別の声だった」を検知する (#150)。
   */
  outputMode: VoiceOutputMode;
  /** ユーザー操作 (タップ) の中で 1 度だけ呼ぶ。iOS の自動再生ブロックを解錠する。 */
  unlock: () => void;
  toggleEnabled: () => void;
  stop: () => void;
  speak: (text: string) => Promise<void>;
  /** 同じ id は二度読み上げない (assistant メッセージの自動読み上げ用)。 */
  speakOnce: (id: string, text: string) => void;
  /** 読み上げ済み id とキャッシュを捨てる (ログアウト時)。 */
  reset: () => void;
};

export function useTextToSpeech({ standalone, speaker }: TextToSpeechOptions): TextToSpeech {
  const [status, setStatus] = React.useState<TtsStatus>("idle");
  const speaking = status !== "idle";
  const [enabled, setEnabled] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [outputMode, setOutputMode] = React.useState<VoiceOutputMode>("idle");

  const activeAudioRef = React.useRef<HTMLAudioElement | null>(null);
  const activeAudioUrlRef = React.useRef<string | null>(null);
  const lastSpokenIdRef = React.useRef<string | null>(null);
  const voiceCacheRef = React.useRef<Map<string, Blob>>(new Map());
  const audioUnlockedRef = React.useRef(false);
  // speak は enabled を読むが、identity を安定させたいので ref 経由で最新値を見る
  // (speakOnce を effect から呼ぶ側が依存配列でハマらないように)。useVoiceInput と同じ流儀。
  const enabledRef = React.useRef(enabled);
  React.useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);
  // 逐次再生の世代番号 (#185)。stop / 次の speak でインクリメントし、進行中のキュー
  // (合成待ちの文が残っている) を「もう自分の番ではない」と判定させて打ち切る。
  const playbackGenRef = React.useRef(0);
  // 再生中の 1 文を stop() 側から決着させるためのハンドル (playBlob が差し込む)。
  const activePlaybackSettleRef = React.useRef<((outcome: PlaybackOutcome) => void) | null>(null);

  const stop = React.useCallback(() => {
    playbackGenRef.current += 1;
    if (activeAudioRef.current) {
      activeAudioRef.current.pause();
      activeAudioRef.current.currentTime = 0;
      activeAudioRef.current = null;
    }

    if (activeAudioUrlRef.current) {
      URL.revokeObjectURL(activeAudioUrlRef.current);
      activeAudioUrlRef.current = null;
    }

    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    // 音を止めたあとにキューを決着させる (先に決着させると onended 相当の後片付けで
    // activeAudioRef が null になり、上の pause に届かず鳴りっぱなしになる)。
    activePlaybackSettleRef.current?.("stopped");

    setStatus("idle");
  }, []);

  const synthesizeWithVoicevox = React.useCallback(
    async (text: string): Promise<Blob> => {
      // BFF 直叩き + Authorization は api/http.ts に集約 (#69)。相対 /api/tts のままだと
      // SWA に投げて必ず失敗し、ブラウザ読み上げへ静かにフォールバックしていた
      // (2026-08-08 実環境で発覚)。
      const res = await ttsFetch(text, speaker);

      if (res.status === 204) {
        // VOICEVOX_BASE_URL 未設定時の stub。フォールバックをトリガーする。
        throw new Error(TTS_STUB);
      }

      if (!res.ok) {
        throw new Error(`TTS synthesis failed: ${res.status}`);
      }

      return await res.blob();
    },
    [speaker],
  );

  /**
   * ブラウザ内蔵の音声合成で読み上げる (VOICEVOX が無い時のフォールバック / mock デモ用)。
   *
   * degradedReason を渡した場合は「ずんだもん以外の声になった」ことを画面に出す
   * (dialogue-session.mdx §5.5: 無言で別の声に置き換えない)。standalone は想定内なので
   * 理由なしで呼ぶ = 警告を出さない。
   */
  const speakWithBrowser = React.useCallback((text: string, degradedReason?: string) => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setStatus("idle");
      setOutputMode("idle");
      setError(degradedReason ?? "このブラウザは音声読み上げに対応していません。");
      return;
    }

    setOutputMode("browser-fallback");
    if (degradedReason) setError(degradedReason);

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "ja-JP";
    // 日本語ボイスがあれば優先 (無ければ既定ボイス + lang ヒント)。
    const jaVoice = window.speechSynthesis
      .getVoices()
      .find((v) => v.lang?.toLowerCase().startsWith("ja"));
    if (jaVoice) utterance.voice = jaVoice;
    utterance.rate = 1;
    setStatus("playing");
    utterance.onend = () => setStatus("idle");
    utterance.onerror = () => setStatus("idle");
    window.speechSynthesis.speak(utterance);
  }, []);

  // **2 系統をどちらも解錠する** (#150): ブラウザ読み上げ (speechSynthesis) と
  // ずんだもんの再生 (HTMLAudioElement) は別物で、前者だけ解錠しても後者は弾かれる。
  const unlock = React.useCallback(() => {
    if (audioUnlockedRef.current) return;
    audioUnlockedRef.current = true;
    if (typeof window === "undefined") return;

    unlockAudioElement();

    if (!("speechSynthesis" in window)) return;
    try {
      const u = new SpeechSynthesisUtterance(" ");
      u.volume = 0;
      window.speechSynthesis.speak(u);
    } catch {
      // 解錠失敗は致命ではない。
    }
  }, []);

  /** 合成 1 文ぶん。フロント側キャッシュ (同じ文の読み直しで往復しない) を通す。 */
  const synthesizeCached = React.useCallback(
    async (text: string): Promise<Blob> => {
      const cacheKey = `${speaker}:${text}`;
      const cache = voiceCacheRef.current;
      const cached = cache.get(cacheKey);
      if (cached) return cached;

      const blob = await synthesizeWithVoicevox(text);
      cache.set(cacheKey, blob);
      if (cache.size > VOICE_CACHE_MAX) {
        const oldest = cache.keys().next().value;
        if (oldest) cache.delete(oldest);
      }
      return blob;
    },
    [speaker, synthesizeWithVoicevox],
  );

  /**
   * WAV を 1 本鳴らし、鳴り終わる (or 中断される) まで待つ。
   *
   * ジェスチャ内で解錠した要素を使い回す。都度 new Audio すると解錠が引き継がれず、
   * 合成待ちを挟むぶん自動再生ポリシーに弾かれやすい (#150)。
   */
  const playBlob = React.useCallback((blob: Blob): Promise<PlaybackOutcome> => {
    return new Promise<PlaybackOutcome>((resolve) => {
      const objectUrl = URL.createObjectURL(blob);
      activeAudioUrlRef.current = objectUrl;

      const audio = acquireAudioElement(objectUrl);
      activeAudioRef.current = audio;

      // stop() からも決着させられるようにしておく。無いと停止時にキューが
      // await したまま解決せず、次の読み上げまでハンドラが残り続ける。
      let settled = false;
      const settle = (outcome: PlaybackOutcome) => {
        if (settled) return;
        settled = true;
        audio.onended = null;
        audio.onerror = null;
        if (activePlaybackSettleRef.current === settle) {
          activePlaybackSettleRef.current = null;
        }
        if (activeAudioUrlRef.current === objectUrl) {
          URL.revokeObjectURL(objectUrl);
          activeAudioUrlRef.current = null;
        }
        if (activeAudioRef.current === audio) activeAudioRef.current = null;
        resolve(outcome);
      };
      activePlaybackSettleRef.current = settle;

      audio.onended = () => settle("ended");
      audio.onerror = () => settle("error");

      void audio.play().then(
        () => setOutputMode("voicevox"),
        () => {
          // ブラウザに自動再生を止められた。無言のままブラウザ読み上げに置き換えると
          // 「ずんだもんが黙った」ようにしか見えない (#150 の実事象)。
          settle("blocked");
        },
      );
    });
  }, []);

  const speak = React.useCallback(
    async (text: string) => {
      if (!enabledRef.current || !text.trim()) return;

      setError(null);
      stop();
      const gen = (playbackGenRef.current += 1);
      const isStale = () => playbackGenRef.current !== gen;

      // standalone (mock デモ) は BFF/VOICEVOX が無いので、ネットワークを叩かず
      // ブラウザ内蔵 TTS で直接読み上げる (/api/tts の 404 待ちで詰まらせない)。
      if (standalone) {
        speakWithBrowser(text);
        return;
      }

      setStatus("synthesizing");

      // ① 読み上げ単位の文を BFF に割ってもらう (#185)。分割の真実は BFF 側 (ADR 0024)
      //    なので、ここでは返ってきた文字列をそのまま投げ返すだけで自前では切らない。
      //    取れなくても致命ではない — 従来どおり全文 1 本で合成する。
      let sentences: string[] = [text];
      try {
        const res = await ttsPlanFetch(text, speaker);
        if (res.status === 204) {
          // VOICEVOX 未構成。合成しても無駄なのでここでフォールバックする。
          speakWithBrowser(text, MSG_SYNTH_STUB);
          return;
        }
        if (res.ok) {
          const body = (await res.json()) as { sentences?: unknown };
          if (Array.isArray(body.sentences)) {
            const usable = body.sentences.filter(
              (s): s is string => typeof s === "string" && s.trim().length > 0,
            );
            if (usable.length > 0) sentences = usable;
          }
        }
      } catch (err) {
        console.warn("[tts] 文分割を取得できませんでした — 全文一括で合成します", err);
      }
      if (isStale()) return;

      // ② 全文をまとめて合成に出し、**1 文目が焼けた時点で鳴らし始める**。
      //    以前は全文を結合してから返る 1 リクエストだったので、最初の音が出るまで
      //    「最後の 1 文の合成完了」を待っていた (PO 報告の約 10 秒 / #185)。
      const pending = mapWithLimit(sentences, SYNTH_CONCURRENCY, synthesizeCached);
      // 打ち切られたキューが unhandled rejection にならないようにする。
      pending.forEach((p) => void p.catch(() => {}));

      for (let i = 0; i < pending.length; i += 1) {
        let audioBlob: Blob;
        try {
          audioBlob = await pending[i];
        } catch (err) {
          if (isStale()) return;
          const stub = err instanceof Error && err.message === TTS_STUB;
          // 途中まで鳴らしていたら残りをブラウザ読み上げで継ぐ。無言で切らない。
          speakWithBrowser(sentences.slice(i).join(""), stub ? MSG_SYNTH_STUB : MSG_SYNTH_FAILED);
          return;
        }
        if (isStale()) return;

        setStatus("playing");
        const outcome = await playBlob(audioBlob);
        if (isStale() || outcome === "stopped") return;

        if (outcome === "blocked") {
          // 次のタップで解錠をやり直せるよう、解錠済みフラグを戻す。
          audioUnlockedRef.current = false;
          speakWithBrowser(sentences.slice(i).join(""), MSG_PLAYBACK_BLOCKED);
          return;
        }
        if (outcome === "error") {
          setStatus("idle");
          setOutputMode("idle");
          setError("音声の再生に失敗しました。");
          return;
        }
      }

      setStatus("idle");
    },
    [playBlob, speakWithBrowser, standalone, stop, synthesizeCached, speaker],
  );

  const speakOnce = React.useCallback(
    (id: string, text: string) => {
      // OFF の間に届いたメッセージは「既読」にしない。ここで消費してしまうと、
      // ユーザーが読み上げを ON に戻してもその 1 件だけ永久に読まれない (旧実装は
      // ttsEnabled の判定が呼び出し側の effect にあり id を消費しなかった)。
      if (!enabledRef.current) return;
      if (lastSpokenIdRef.current === id) return;
      lastSpokenIdRef.current = id;
      void speak(text);
    },
    [speak],
  );

  const toggleEnabled = React.useCallback(() => {
    setEnabled((prev) => {
      const next = !prev;
      if (!next) stop();
      return next;
    });
  }, [stop]);

  const reset = React.useCallback(() => {
    stop();
    setEnabled(true);
    setError(null);
    setOutputMode("idle");
    lastSpokenIdRef.current = null;
    voiceCacheRef.current.clear();
  }, [stop]);

  React.useEffect(() => stop, [stop]);

  return {
    speaking,
    status,
    enabled,
    error,
    outputMode,
    unlock,
    toggleEnabled,
    stop,
    speak,
    speakOnce,
    reset,
  };
}
