/**
 * [L1] tryStartAzureRecognition — 「想定内の不在」と「予期しない失敗」の分離 (#121 / ADR 0023)。
 *
 * L1 で書く理由: BFF 側 (speechTokenClient) は「MSI 取得失敗は握り潰さず throw」を
 * 設計意図としてテストで担保している。その意図がフロントの catch-all で消えると、
 * 本番で MI 権限剥がれが起きても「動くが精度が落ちている」だけになり誰も気づけない。
 * 分岐は純ロジックなのでここで固定する (SDK / マイクは L1 の対象外なので starter を DI)。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { tryStartAzureRecognition, type RecognitionHandlers } from "./azureSpeech";

const handlers: RecognitionHandlers = {
  onFinal: () => {},
  onInterim: () => {},
  onFatal: () => {},
};

afterEach(() => {
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
