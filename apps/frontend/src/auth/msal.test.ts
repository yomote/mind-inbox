import { describe, expect, it, vi, afterEach } from "vitest";

/**
 * 認証の有効/無効は env だけで決まる (#69)。
 *
 * 無いと何が静かに通るか:
 *   VITE_ENTRA_* が欠けたまま公開ビルドを出すと、フロントは Authorization を一切付けない。
 *   Functions 側 EasyAuth が有効なら全リクエストが 401 になり、無効なら **認証なしで公開された
 *   API がそのまま使える**。どちらもビルドは緑のまま進むため、この判定条件を固定して守る。
 */
async function loadAuthModule() {
  // env を読むのは import 時なので、毎回モジュールキャッシュを捨てて読み直す。
  vi.resetModules();
  return import("./msal");
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("[L1] auth env gating", () => {
  it("client ID と tenant ID が揃ったときだけ認証が有効になる", async () => {
    vi.stubEnv("VITE_ENTRA_CLIENT_ID", "11111111-2222-3333-4444-555555555555");
    vi.stubEnv("VITE_ENTRA_TENANT_ID", "66666666-7777-8888-9999-000000000000");

    const { authEnabled } = await loadAuthModule();

    expect(authEnabled).toBe(true);
  });

  it("env が欠けていれば認証は無効 (ローカル開発)", async () => {
    vi.stubEnv("VITE_ENTRA_CLIENT_ID", "");
    vi.stubEnv("VITE_ENTRA_TENANT_ID", "");

    const { authEnabled, getAccount } = await loadAuthModule();

    expect(authEnabled).toBe(false);
    // 無効時は MSAL を作らないので、アカウント参照が例外にならないこと。
    expect(getAccount()).toBeNull();
  });

  it("片方だけでは有効にならない (中途半端な設定で公開しない)", async () => {
    vi.stubEnv("VITE_ENTRA_CLIENT_ID", "11111111-2222-3333-4444-555555555555");
    vi.stubEnv("VITE_ENTRA_TENANT_ID", "");

    const { authEnabled } = await loadAuthModule();

    expect(authEnabled).toBe(false);
  });

  it("認証無効ならトークンは常に null (Authorization を付けない)", async () => {
    vi.stubEnv("VITE_ENTRA_CLIENT_ID", "");
    vi.stubEnv("VITE_ENTRA_TENANT_ID", "");

    const { getAccessToken } = await loadAuthModule();

    await expect(getAccessToken()).resolves.toBeNull();
  });
});
