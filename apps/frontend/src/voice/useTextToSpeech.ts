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

/** VOICEVOX 未設定時に BFF が返す 204 を「合成できなかった」として扱うための番兵。 */
const TTS_STUB = "TTS_STUB";

/** 合成済み WAV の保持上限。超えたら古い順に捨てる。 */
const VOICE_CACHE_MAX = 30;

export type TextToSpeechOptions = {
  /** dev / mock ビルド: BFF が無いのでネットワークを叩かずブラウザ読み上げに直行する。 */
  standalone: boolean;
  /** VOICEVOX の話者 ID。 */
  speaker: number;
};

export type TextToSpeech = {
  /** 再生中か (VOICEVOX 再生 / ブラウザ読み上げの両方)。 */
  speaking: boolean;
  /** 読み上げが有効か。false の間は speak / speakOnce が何もしない。 */
  enabled: boolean;
  error: string | null;
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

  /** ブラウザ内蔵の音声合成で読み上げる (VOICEVOX が無い時のフォールバック / mock デモ用)。 */
  const speakWithBrowser = React.useCallback((text: string) => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setSpeaking(false);
      setError("このブラウザは音声読み上げに対応していません。");
      return;
    }

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

  const unlock = React.useCallback(() => {
    if (audioUnlockedRef.current) return;
    audioUnlockedRef.current = true;
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
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

      try {
        const cacheKey = `${speaker}:${text}`;
        const cache = voiceCacheRef.current;
        const audioBlob = cache.get(cacheKey) || (await synthesizeWithVoicevox(text));

        if (!cache.has(cacheKey)) {
          cache.set(cacheKey, audioBlob);
          if (cache.size > VOICE_CACHE_MAX) {
            const oldest = cache.keys().next().value;
            if (oldest) cache.delete(oldest);
          }
        }

        const objectUrl = URL.createObjectURL(audioBlob);
        activeAudioUrlRef.current = objectUrl;

        const audio = new Audio(objectUrl);
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
          setError("音声の再生に失敗しました。");
        };

        await audio.play();
      } catch {
        // VOICEVOX 失敗時 (stub 204 / 通信断 / 合成エラー) はブラウザ TTS にフォールバック。
        speakWithBrowser(text);
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
    lastSpokenIdRef.current = null;
    voiceCacheRef.current.clear();
  }, [stop]);

  React.useEffect(() => stop, [stop]);

  return {
    speaking,
    enabled,
    error,
    unlock,
    toggleEnabled,
    stop,
    speak,
    speakOnce,
    reset,
  };
}
