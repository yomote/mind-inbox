/**
 * [L1] Cosmos SDK に渡す MI トークン shim。
 *
 * 無いと何が静かに通るか:
 *   - scope (`.../.default`) をそのまま MSI の `resource` に渡してしまい、本番でだけ
 *     トークン取得が失敗する (ローカルは Cosmos を使わないので気づけない)
 *   - MSI が使えないローカルで null トークンのまま Cosmos を叩き、原因の分かりにくい
 *     401 になる (disableLocalAuth なのでキーでも通らない)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../clients/serviceToken", () => ({ getServiceToken: vi.fn() }));

import { getServiceToken } from "../clients/serviceToken";
import {
  COSMOS_FALLBACK_RESOURCE,
  ManagedIdentityCosmosCredential,
  readJwtExpiryMs,
  scopeToResource,
} from "./cosmosCredential";

const getServiceTokenMock = vi.mocked(getServiceToken);

/** 署名は使わないので payload だけ本物らしく作る。 */
function fakeJwt(expSeconds: number): string {
  const payload = Buffer.from(JSON.stringify({ exp: expSeconds }), "utf8").toString("base64url");
  return `header.${payload}.signature`;
}

beforeEach(() => {
  getServiceTokenMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("[L1] scopeToResource", () => {
  it("末尾の /.default を落として MSI の resource にする", () => {
    expect(scopeToResource("https://acct.documents.azure.com/.default")).toBe(
      "https://acct.documents.azure.com",
    );
    expect(scopeToResource("https://cosmos.azure.com/.default")).toBe("https://cosmos.azure.com");
  });

  it("/.default が無い値はそのまま通す", () => {
    expect(scopeToResource("https://cosmos.azure.com")).toBe("https://cosmos.azure.com");
  });
});

describe("[L1] readJwtExpiryMs", () => {
  it("exp (epoch 秒) をミリ秒に直す", () => {
    expect(readJwtExpiryMs(fakeJwt(1767225600))).toBe(1767225600_000);
  });

  it("JWT として読めない値は null", () => {
    expect(readJwtExpiryMs("not-a-jwt")).toBeNull();
    expect(readJwtExpiryMs("a.!!!.c")).toBeNull();
  });
});

describe("[L1] ManagedIdentityCosmosCredential", () => {
  it("scope を resource に直して MSI を叩き、exp を expiresOnTimestamp にする", async () => {
    getServiceTokenMock.mockResolvedValue(fakeJwt(1767225600));

    const token = await new ManagedIdentityCosmosCredential().getToken([
      "https://acct.documents.azure.com/.default",
    ]);

    expect(getServiceTokenMock).toHaveBeenCalledWith("https://acct.documents.azure.com");
    expect(token.expiresOnTimestamp).toBe(1767225600_000);
  });

  it("account 個別リソースが引けなければ cosmos.azure.com にフォールバックする", async () => {
    getServiceTokenMock
      .mockRejectedValueOnce(new Error("AADSTS500011"))
      .mockResolvedValueOnce(fakeJwt(1767225600));

    const token = await new ManagedIdentityCosmosCredential().getToken(
      "https://acct.documents.azure.com/.default",
    );

    expect(getServiceTokenMock).toHaveBeenNthCalledWith(2, COSMOS_FALLBACK_RESOURCE);
    expect(token.token).toBeTruthy();
  });

  it("フォールバック先も失敗したら握り潰さない", async () => {
    getServiceTokenMock.mockRejectedValue(new Error("boom"));

    await expect(
      new ManagedIdentityCosmosCredential().getToken(COSMOS_FALLBACK_RESOURCE + "/.default"),
    ).rejects.toThrow("boom");
  });

  it("MI が無い環境 (getServiceToken が null) では原因の分かる例外にする", async () => {
    getServiceTokenMock.mockResolvedValue(null);

    await expect(
      new ManagedIdentityCosmosCredential().getToken(["https://acct.documents.azure.com/.default"]),
    ).rejects.toThrow(/COSMOS_ENDPOINT/);
  });

  it("exp が読めないトークンでも有限の寿命を返す (キャッシュし続けない)", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-09T00:00:00.000Z"));
    getServiceTokenMock.mockResolvedValue("opaque-token");

    const token = await new ManagedIdentityCosmosCredential().getToken(
      "https://acct.documents.azure.com/.default",
    );

    expect(token.expiresOnTimestamp).toBe(Date.now() + 5 * 60 * 1000);
  });
});
