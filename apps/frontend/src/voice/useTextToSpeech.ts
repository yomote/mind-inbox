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
import { ttsFetch } from "../api/http";
import { acquireAudioElement, unlockAudioElement } from "./unlockedAudio";

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

export type TextToSpeech = {
  /** 再生中か (VOICEVOX 再生 / ブラウザ読み上げの両方)。 */
  speaking: boolean;
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
  const [speaking, setSpeaking] = React.useState(false);
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

  const stop = React.useCallback(() => {
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

    setSpeaking(false);
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
      setSpeaking(false);
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
    setSpeaking(true);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
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

  const speak = React.useCallback(
    async (text: string) => {
      if (!enabledRef.current || !text.trim()) return;

      setError(null);
      stop();

      // standalone (mock デモ) は BFF/VOICEVOX が無いので、ネットワークを叩かず
      // ブラウザ内蔵 TTS で直接読み上げる (/api/tts の 404 待ちで詰まらせない)。
      if (standalone) {
        speakWithBrowser(text);
        return;
      }

      setSpeaking(true);

      // ① 合成 (VOICEVOX)。「音が届いていない」であり、再生の失敗とは別物として扱う。
      let audioBlob: Blob;
      try {
        const cacheKey = `${speaker}:${text}`;
        const cache = voiceCacheRef.current;
        const cached = cache.get(cacheKey);
        audioBlob = cached ?? (await synthesizeWithVoicevox(text));

        if (!cached) {
          cache.set(cacheKey, audioBlob);
          if (cache.size > VOICE_CACHE_MAX) {
            const oldest = cache.keys().next().value;
            if (oldest) cache.delete(oldest);
          }
        }
      } catch (err) {
        const stub = err instanceof Error && err.message === TTS_STUB;
        speakWithBrowser(text, stub ? MSG_SYNTH_STUB : MSG_SYNTH_FAILED);
        return;
      }

      // ② 再生。ジェスチャ内で解錠した要素を使い回す。都度 new Audio すると解錠が
      // 引き継がれず、合成待ち (数秒) を挟むぶん自動再生ポリシーに弾かれやすい。
      // 厳しさはブラウザ差があり desktop Chromium では再現しないが、解錠の穴自体は
      // 実在するため塞ぐ (#150 の原因は未確定 — 確定は再発時の表示を待つ)。
      const objectUrl = URL.createObjectURL(audioBlob);
      activeAudioUrlRef.current = objectUrl;

      const audio = acquireAudioElement(objectUrl);
      activeAudioRef.current = audio;
      audio.onended = () => {
        setSpeaking(false);
        if (activeAudioUrlRef.current) {
          URL.revokeObjectURL(activeAudioUrlRef.current);
          activeAudioUrlRef.current = null;
        }
        activeAudioRef.current = null;
      };
      audio.onerror = () => {
        setSpeaking(false);
        setOutputMode("idle");
        setError("音声の再生に失敗しました。");
      };

      try {
        await audio.play();
        setOutputMode("voicevox");
      } catch {
        // ブラウザに自動再生を止められた。ここで無言のままブラウザ読み上げに
        // 置き換えると「ずんだもんが黙った」ようにしか見えない (#150 の実事象)。
        // 次のタップで解錠をやり直せるよう、解錠済みフラグを戻す。
        audioUnlockedRef.current = false;
        speakWithBrowser(text, MSG_PLAYBACK_BLOCKED);
      }
    },
    [speakWithBrowser, standalone, stop, synthesizeWithVoicevox, speaker],
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
