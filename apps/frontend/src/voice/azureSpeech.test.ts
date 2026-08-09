/**
 * [L1] tryStartAzureRecognition — 「想定内の不在」と「予期しない失敗」の分離 (#121 / ADR 0023)。
 *
 * L1 で書く理由: BFF 側 (speechTokenClient) は「MSI 取得失敗は握り潰さず throw」を
 * 設計意図としてテストで担保している。その意図がフロントの catch-all で消えると、
 * 本番で MI 権限剥がれが起きても「動くが精度が落ちている」だけになり誰も気づけない。
 * 分岐は純ロジックなのでここで固定する (SDK / マイクは L1 の対象外なので starter を DI)。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../trpc/client", () => ({
  trpc: { speech: { issueToken: { query: vi.fn() } } },
}));

import { trpc } from "../trpc/client";
import {
  peekAzurePreparation,
  prepareAzureRecognition,
  resetAzurePreparationForTest,
  startPreparedAzureRecognition,
  tryStartAzureRecognition,
  type AzurePreparation,
  type RecognitionHandlers,
} from "./azureSpeech";

const handlers: RecognitionHandlers = {
  onFinal: () => {},
  onInterim: () => {},
  onFatal: () => {},
};

beforeEach(() => {
  // 準備はモジュールスコープのキャッシュなので、テスト間で必ず捨てる
  // (残すと「2 回目は照会しない」系のテストが順序依存で壊れる)。
  resetAzurePreparationForTest();
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("[L1] tryStartAzureRecognition", () => {
  it("開始できたら started を返す", () => {
    const recognition = { stop: vi.fn() };
    return expect(tryStartAzureRecognition(handlers, async () => recognition)).resolves.toEqual({
      kind: "started",
      recognition,
    });
  });

  it("想定内の不在 (available:false) は unavailable を返し、ログを汚さない", async () => {
    // 無いと: 未プロビジョニング環境やローカルの通常運用でエラーログが出続け、
    // 本物の失敗ログが埋もれる退行が静かに通る
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    await expect(tryStartAzureRecognition(handlers, async () => null)).resolves.toEqual({
      kind: "unavailable",
    });
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("予期しない例外は failed として理由つきで返し、必ずログに残す", async () => {
    // 無いと: MI の権限剥がれ等でトークン取得が失敗しても catch-all で握り潰され、
    // 「動くが精度が落ちている」状態に誰も気づけない (BFF 側の
    // "propagates MSI failures instead of degrading silently" の意図がフロントで消える)
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const outcome = await tryStartAzureRecognition(handlers, async () => {
      throw new Error("issueToken 500");
    });

    expect(outcome).toEqual({ kind: "failed", reason: "issueToken 500" });
    expect(errorSpy).toHaveBeenCalledOnce();
    expect(errorSpy.mock.calls[0]?.[0]).toContain("issueToken 500");
  });
});

describe("[L1] prepareAzureRecognition — タップ前の準備とキャッシュ (#186)", () => {
  it("トークンが取れたら ready を返し、peek で await せずに取り出せる", async () => {
    // 無いと: タップ時に同期でマイクを開けるという #186 の前提 (準備済みが同期で覗ける)
    //         が崩れても気づけない。await が 1 つ入った時点で iOS はマイクを開かない。
    vi.mocked(trpc.speech.issueToken.query).mockResolvedValue({
      available: true,
      authToken: "aad#res#tok",
      region: "japaneast",
    });

    const prepared = await prepareAzureRecognition();

    expect(prepared.kind).toBe("ready");
    expect(peekAzurePreparation()).toBe(prepared);
  });

  it("2 回目はトークンを再照会しない (準備を使い回す)", async () => {
    // 無いと: タップのたびにトークンを取り直し、準備の意味 (同期で開ける) が消える
    vi.mocked(trpc.speech.issueToken.query).mockResolvedValue({
      available: true,
      authToken: "aad#res#tok",
      region: "japaneast",
    });

    await prepareAzureRecognition();
    await prepareAzureRecognition();

    expect(trpc.speech.issueToken.query).toHaveBeenCalledTimes(1);
  });

  it("**予期しない失敗はキャッシュせず、次の呼び出しで必ず取り直す**", async () => {
    // 無いと: 一過性のネットワーク瞬断でトークン取得が一度こけただけで、そのページ
    //         セッションのあいだずっと高精度認識が復帰しなくなる (リロードするまで直らない)。
    //         音声入力自体はブラウザ認識で動き続けるので、**誰も気づけない品質劣化**になる。
    //         PR #190 のレビューで実際に見つかった退行なので機械的に止める。
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(trpc.speech.issueToken.query)
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValue({ available: true, authToken: "aad#res#tok", region: "japaneast" });

    const first = await prepareAzureRecognition();
    expect(first).toEqual({ kind: "failed", reason: "network down" });
    // 失敗は「使い回してよい準備」ではないので peek にも出さない
    expect(peekAzurePreparation()).toBeNull();

    const second = await prepareAzureRecognition();
    expect(second.kind).toBe("ready");
    expect(trpc.speech.issueToken.query).toHaveBeenCalledTimes(2);
  });

  it("想定内の不在 (available:false) は使い回すが、失効したら聞き直す", async () => {
    // 無いと: 未プロビジョニング環境でタップのたびに BFF を叩く / 逆に、セッション中に
    //         プロビジョニングされても永久に拾えない、のどちらかに倒れる
    vi.mocked(trpc.speech.issueToken.query).mockResolvedValue({
      available: false,
      authToken: null,
      region: null,
    });

    expect((await prepareAzureRecognition()).kind).toBe("unavailable");
    await prepareAzureRecognition();
    expect(trpc.speech.issueToken.query).toHaveBeenCalledTimes(1);

    // トークン寿命 (8 分) を跨いだら聞き直す
    vi.setSystemTime(Date.now() + 9 * 60_000);
    await prepareAzureRecognition();
    expect(trpc.speech.issueToken.query).toHaveBeenCalledTimes(2);
  });
});

describe("[L1] startPreparedAzureRecognition — ジェスチャ内で開く (#186)", () => {
  /** 準備済みオブジェクトの最小の偽物。SDK 実体はロードしない。 */
  function preparedWithFakeSdk() {
    const recognizer: Record<string, unknown> = {
      startContinuousRecognitionAsync: vi.fn(),
      stopContinuousRecognitionAsync: vi.fn(),
      close: vi.fn(),
    };
    const sdk = {
      SpeechConfig: { fromAuthorizationToken: () => ({}) },
      AudioConfig: { fromDefaultMicrophoneInput: vi.fn(() => ({})) },
      SpeechRecognizer: function () {
        return recognizer;
      },
      ResultReason: { RecognizedSpeech: "RecognizedSpeech" },
      CancellationReason: { Error: "Error" },
    };
    const prepared = {
      kind: "ready",
      sdk,
      authToken: "aad#res#tok",
      region: "japaneast",
      expiresAt: Date.now() + 60_000,
    } as unknown as AzurePreparation;
    return { prepared, recognizer, sdk };
  }

  it("マイクの open も認識開始も同期で行い、Promise を返さない", () => {
    // 無いと: 開始を await する実装に戻っても「デスクトップでは動く」ので緑のまま通る。
    //         iOS Safari はユーザージェスチャの同期処理の外で開いたマイクを、許可済みでも
    //         実質無効にするため、await が 1 つ入った瞬間に #186 が再発する。
    const { prepared, recognizer, sdk } = preparedWithFakeSdk();

    const running = startPreparedAzureRecognition(prepared, handlers);

    // 呼び出しから戻った時点で、既にマイクと認識が開いていること
    expect(sdk.AudioConfig.fromDefaultMicrophoneInput).toHaveBeenCalledOnce();
    expect(recognizer.startContinuousRecognitionAsync).toHaveBeenCalledOnce();
    expect(running).not.toBeInstanceOf(Promise);
    expect(typeof running.stop).toBe("function");
  });

  it("認識結果を onFinal / onInterim に流す", () => {
    // 無いと: 開始はできるが認識テキストが入力欄に届かない、という壊れ方を拾えない
    const { prepared, recognizer, sdk } = preparedWithFakeSdk();
    const onFinal = vi.fn();
    const onInterim = vi.fn();

    startPreparedAzureRecognition(prepared, { onFinal, onInterim, onFatal: vi.fn() });

    (recognizer.recognizing as (s: unknown, e: unknown) => void)(null, {
      result: { text: "とちゅう" },
    });
    (recognizer.recognized as (s: unknown, e: unknown) => void)(null, {
      result: { text: "かくてい", reason: sdk.ResultReason.RecognizedSpeech },
    });

    expect(onInterim).toHaveBeenCalledWith("とちゅう");
    expect(onFinal).toHaveBeenCalledWith("かくてい");
  });

  it("開始に失敗したら onFatal で知らせる (無言でマイクが死なない)", () => {
    // 無いと: 押したのに何も起きない状態に戻り、原因も出ない (#186 の元症状)
    const { prepared, recognizer } = preparedWithFakeSdk();
    const onFatal = vi.fn();
    vi.mocked(recognizer.startContinuousRecognitionAsync as ReturnType<typeof vi.fn>).mockImplementation(
      (_ok: () => void, err: (m: string) => void) => err("mic busy"),
    );

    startPreparedAzureRecognition(prepared, { ...handlers, onFatal });

    expect(onFatal).toHaveBeenCalledWith("mic busy");
  });
});
