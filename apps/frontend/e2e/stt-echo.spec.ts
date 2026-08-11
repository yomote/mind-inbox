import type { Page } from "@playwright/test";
import { expect, gotoHome, test } from "./fixtures";

/**
 * L3 — TTS 再生中のエコー破棄 (#228) を production バンドルの実配線で通す。
 *
 * ここが無いと静かに通るもの: gate (echoGate) と hook (useVoiceInput) の単体は緑でも、
 * **SessionComposer → useVoiceInput への ttsPlaying の配線**が切れる (props の渡し忘れ /
 * ttsStatus の条件変更) と、再生中のずんだもんの声がまた入力欄に入る。認識も読み上げも
 * 個々には正常なので、実画面で組み合わせない限り検知できない (2026-08-10 PO 実使用で発生)。
 *
 * 実ブラウザの Web Speech / speechSynthesis はヘッドレスでは音を出せないため、
 * どちらも決定的な偽物に差し替え、**認識イベントと再生終了をテストから起こす**。
 */

declare global {
  interface Window {
    __speech: {
      utterances: Array<{ text: string; onend: (() => void) | null }>;
      recognitions: Array<{
        onresult: ((e: unknown) => void) | null;
      }>;
    };
  }
}

/** Web Speech (認識) と speechSynthesis (読み上げ) の決定的な偽物を仕込む。 */
async function installFakeSpeech(page: Page) {
  await page.addInitScript(() => {
    const store: Window["__speech"] = { utterances: [], recognitions: [] };
    window.__speech = store;

    class FakeUtterance {
      text: string;
      lang = "";
      rate = 1;
      volume = 1;
      voice: unknown = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(text: string) {
        this.text = text;
      }
    }

    const synth = {
      speak: (u: FakeUtterance) => {
        store.utterances.push(u);
      },
      cancel: () => {},
      getVoices: () => [],
    };
    Object.defineProperty(window, "speechSynthesis", { value: synth, configurable: true });
    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      value: FakeUtterance,
      configurable: true,
    });

    class FakeRecognition {
      lang = "";
      continuous = false;
      interimResults = false;
      maxAlternatives = 0;
      onstart: (() => void) | null = null;
      onresult: ((e: unknown) => void) | null = null;
      onerror: ((e: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      constructor() {
        store.recognitions.push(this);
      }
      start() {
        this.onstart?.();
      }
      stop() {
        this.onend?.();
      }
      abort() {
        this.onend?.();
      }
    }
    // Chromium はネイティブの SpeechRecognition を持つことがあるため両方を差し替える。
    Object.defineProperty(window, "SpeechRecognition", {
      value: FakeRecognition,
      configurable: true,
    });
    Object.defineProperty(window, "webkitSpeechRecognition", {
      value: FakeRecognition,
      configurable: true,
    });
  });
}

/** 認識エンジンが確定 (final) 結果を出したことにする。 */
function fireFinal(page: Page, text: string) {
  return page.evaluate((t) => {
    const recognition = window.__speech.recognitions.at(-1);
    recognition?.onresult?.({
      resultIndex: 0,
      results: [{ 0: { transcript: t }, isFinal: true, length: 1 }],
    });
  }, text);
}

/** 再生中の (最新の) 読み上げを終わらせる。 */
function endPlayback(page: Page) {
  return page.evaluate(() => {
    const playing = window.__speech.utterances.filter((u) => u.text.trim().length > 0).at(-1);
    playing?.onend?.();
  });
}

test.describe("TTS 再生中のエコー破棄 (#228)", () => {
  test("[L3] 読み上げ中の認識結果は入力欄に入らず、再生後は自動で再開する", async ({ page }) => {
    await installFakeSpeech(page);
    await gotoHome(page);
    await page.getByRole("button", { name: "新しい相談を始める" }).click();

    // セッション開始と同時にガイドの挨拶が自動読み上げされる (standalone = ブラウザ読み上げ)。
    await expect(page.getByTestId("tts-status")).toHaveText(/読み上げ中/);

    // mock ビルドは Web Speech 直行 (Azure のトークン照会をしない)。
    // 読み上げ中にマイクを入れた場合も、認識が止まっていることが表示される。
    await page.getByRole("button", { name: "音声入力開始" }).click();
    await expect(page.getByTestId("stt-muted")).toBeVisible();
    await expect(page.getByText(/聞いています/)).toHaveCount(0);

    // 挨拶の再生が終わる → 表示が「聞いています」に自動で戻る。
    await endPlayback(page);
    await expect(page.getByText(/聞いています/)).toBeVisible();

    // 終了直後 (猶予内) の final はエコーの尻尾でありうるため破棄される。
    // interim を経ず final だけ届く経路が両エンジンにあるための無条件防御
    // (PR #231 Codex P1)。
    const composer = page.getByPlaceholder("ここに入力 / 話して入力");
    await fireFinal(page, "おわりかけのずんだもんの声");
    await expect(composer).toHaveValue("");

    // 猶予 (1.5s) が明けてからの認識結果は入力欄へ入る。
    await page.waitForTimeout(1700);
    await fireFinal(page, "最近ねむれない");
    await expect(composer).toHaveValue("最近ねむれない");

    // 送信 → mock の AI 応答が自動読み上げされる。
    await page.getByRole("button", { name: "送信" }).click();
    await expect(page.getByTestId("tts-status")).toHaveText(/読み上げ中/);
    // 認識が止まっていることが表示される (「聞いています」のままにしない)。
    await expect(page.getByTestId("stt-muted")).toBeVisible();
    await expect(page.getByText(/聞いています/)).toHaveCount(0);

    // 再生中に届いた認識結果 = スピーカーから出たずんだもんの声。入力欄に入れない。
    await fireFinal(page, "ぼくはずんだもんなのだ");
    await expect(composer).toHaveValue("");

    // 再生終了 → 表示が「聞いています」に自動で戻る (ユーザーの再操作なし)。
    await endPlayback(page);
    await expect(page.getByTestId("stt-muted")).toHaveCount(0);
    await expect(page.getByText(/聞いています/)).toBeVisible();

    // 猶予明け後の発話は入力欄へ入る。エコーが漏れていればここが
    // 「ぼくはずんだもんなのだ\nつづきの発話」になり検知される。
    await page.waitForTimeout(1700);
    await fireFinal(page, "つづきの発話");
    await expect(composer).toHaveValue("つづきの発話");
  });
});
