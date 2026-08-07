import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getServiceToken, resetServiceTokenCache, serviceHeaders } from "./serviceToken";

const ORIGINAL_ENV = { ...process.env };

function setMsiEnv() {
  process.env["IDENTITY_ENDPOINT"] = "http://127.0.0.1:42/msi/token";
  process.env["IDENTITY_HEADER"] = "secret-header";
}

function clearMsiEnv() {
  delete process.env["IDENTITY_ENDPOINT"];
  delete process.env["IDENTITY_HEADER"];
}

beforeEach(() => {
  resetServiceTokenCache();
  clearMsiEnv();
});

afterEach(() => {
  vi.restoreAllMocks();
  process.env = { ...ORIGINAL_ENV };
});

describe("serviceToken", () => {
  it("[L1] MSI が無い環境ではトークンを取りにいかず null を返す", async () => {
    // 無いと何が静かに通るか: ローカル開発で MSI エンドポイントへ fetch しにいき、
    // 接続エラーで BFF 全体が落ちる (ADR 0017 の「ローカル用バイパス」が消える)。
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(getServiceToken("api://ai-agent")).resolves.toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("[L1] 取得したトークンを期限内はキャッシュして再取得しない", async () => {
    // 無いと何が静かに通るか: リクエストのたびに MSI を叩き、レイテンシと
    // スロットリングのリスクが増える (壊れないので気づけない)。
    setMsiEnv();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: "tok-1",
          expires_on: String(Math.floor(Date.now() / 1000) + 3600),
        }),
        { status: 200 },
      ),
    );

    await expect(getServiceToken("api://ai-agent")).resolves.toBe("tok-1");
    await expect(getServiceToken("api://ai-agent")).resolves.toBe("tok-1");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("[L1] audience が違えばキャッシュを共有しない", async () => {
    // 無いと何が静かに通るか: ai-agent 用のトークンを voicevox に送り、
    // audience 不一致で 401。しかも「トークンは付いている」ので原因が見えにくい。
    setMsiEnv();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input);
        const who = url.includes("voicevox") ? "tok-vv" : "tok-ai";
        return new Response(
          JSON.stringify({
            access_token: who,
            expires_on: String(Math.floor(Date.now() / 1000) + 3600),
          }),
          { status: 200 },
        );
      });

    await expect(getServiceToken("api://ai-agent")).resolves.toBe("tok-ai");
    await expect(getServiceToken("api://voicevox")).resolves.toBe("tok-vv");
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("[L1] 期限切れのキャッシュは使わず取り直す", async () => {
    setMsiEnv();
    let issued = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      issued += 1;
      return new Response(
        JSON.stringify({
          access_token: `tok-${issued}`,
          // マージン (300s) より内側 = 期限切れ扱い。
          expires_on: String(Math.floor(Date.now() / 1000) + 10),
        }),
        { status: 200 },
      );
    });

    await expect(getServiceToken("api://ai-agent")).resolves.toBe("tok-1");
    await expect(getServiceToken("api://ai-agent")).resolves.toBe("tok-2");
  });

  it("[L1] MSI があるのに取得失敗したら例外にする (無認可で下流を叩かない)", async () => {
    // 無いと何が静かに通るか: トークン取得に失敗しても Authorization 無しで
    // リクエストを送り、下流の 401 だけが見える。「門の手前で失敗した」のか
    // 「門に拒否された」のかが切り分けられなくなる。
    setMsiEnv();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("no", { status: 500, statusText: "Internal Server Error" }),
    );

    await expect(getServiceToken("api://ai-agent")).rejects.toThrow(
      /Managed Identity のトークン取得に失敗/,
    );
  });

  it("[L1] audience 未設定なら Authorization を付けない", async () => {
    // 無いと何が静かに通るか: 門を立てていない環境で空の Bearer を送り、
    // 下流の実装によっては 401 になる。
    setMsiEnv();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await expect(
      serviceHeaders(undefined, { "Content-Type": "application/json" }),
    ).resolves.toEqual({ "Content-Type": "application/json" });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("[L1] audience 設定済みなら Bearer を base ヘッダに足す", async () => {
    setMsiEnv();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          access_token: "tok-1",
          expires_on: String(Math.floor(Date.now() / 1000) + 3600),
        }),
        { status: 200 },
      ),
    );

    await expect(
      serviceHeaders("api://ai-agent", { "Content-Type": "application/json" }),
    ).resolves.toEqual({
      "Content-Type": "application/json",
      Authorization: "Bearer tok-1",
    });
  });
});
