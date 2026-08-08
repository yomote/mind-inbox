import { test } from "@playwright/test";
import { fakeEntraLogin, liveEnvMissing } from "./entra-login";

/**
 * [L4] 音声再生プローブ — **実際に音が鳴ったか**を実環境で観測する (#150)。
 *
 * 既存の検証 (golden-path hop5 / consultation-scenario / ux-probe) はいずれも
 * 「`/api/tts` が 200 + audio/wav を返したか」までしか見ていない。2026-08-08 の
 * 実障害は「WAV は 200 で返っているのに実機ではずんだもんではない声で喋る」で、
 * つまり**合成の先 (再生) が壊れても全緑になる**。
 *
 * このプローブは判定せず (assert しない)、次の事実だけを記録する:
 *   - `HTMLMediaElement.play()` が成功したか / 拒否されたか (拒否理由の名前)
 *   - ブラウザ内蔵読み上げ (speechSynthesis) が使われたか = ずんだもん以外の声
 *
 * assert しない理由: **未修正のバンドルが配信されている状態でも実行して、
 * 原因の切り分けに使う**ため (マージ前に ref 指定で dispatch する運用)。
 * 合否判定は consultation-scenario の `data-voice-output` 検証が担当する。
 */

test.skip(liveEnvMissing, "LIVE_* env が未設定 (実環境向けのみ実行)");

declare global {
  interface Window {
    __voiceProbe?: {
      playOk: number;
      playRejected: string[];
      spoken: string[];
    };
  }
}

test("[L4] 音声再生プローブ: play() の成否とブラウザ読み上げ使用を記録する", async ({ page }) => {
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log(`[browser:error] ${msg.text()}`);
  });

  // ページの JS より先に注入し、再生 API を包んで結果を記録する。
  await page.addInitScript(() => {
    window.__voiceProbe = { playOk: 0, playRejected: [], spoken: [] };
    const probe = window.__voiceProbe;

    const origPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (...args) {
      const result = origPlay.apply(this, args);
      return Promise.resolve(result).then(
        (value) => {
          probe.playOk += 1;
          return value;
        },
        (err: unknown) => {
          probe.playRejected.push(err instanceof Error ? err.name : String(err));
          throw err;
        },
      );
    };

    if (window.speechSynthesis) {
      const origSpeak = window.speechSynthesis.speak.bind(window.speechSynthesis);
      window.speechSynthesis.speak = (u: SpeechSynthesisUtterance) => {
        probe.spoken.push(String(u.text).slice(0, 24));
        return origSpeak(u);
      };
    }
  });

  await fakeEntraLogin(page);
  await page.goto("/");

  // ホームのボタン (= ユーザージェスチャ) から開始する。実ユーザーと同じ導線で
  // 解錠が効くかを見たいので、goto 直後の自動再生ではなくタップ起点にする。
  const startButton = page.getByRole("button", { name: "新しい相談を始める" });
  await startButton.waitFor({ state: "visible", timeout: 60_000 });
  await startButton.click();

  // 開始時の問いかけ → TTS 合成 (実測 5〜9 秒) → 再生 までを待つ。
  await page.waitForTimeout(30_000);

  const probe = await page.evaluate(() => window.__voiceProbe);
  // ずんだもん以外の声で喋ったか = 無音の解錠発話 (" ") 以外が speechSynthesis に渡ったか
  const spokenAloud = (probe?.spoken ?? []).filter((t) => t.trim().length > 0);

  console.log(
    `[voice-probe] ${JSON.stringify({
      playOk: probe?.playOk ?? 0,
      playRejected: probe?.playRejected ?? [],
      browserSpokenCount: spokenAloud.length,
      browserSpokenSample: spokenAloud.slice(0, 2),
    })}`,
  );
  console.log(
    spokenAloud.length > 0
      ? "[voice-probe] 判定材料: ブラウザ読み上げが使われた = ずんだもん以外の声になっている"
      : "[voice-probe] 判定材料: ブラウザ読み上げは使われていない",
  );
});
