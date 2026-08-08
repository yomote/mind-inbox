import { expect, test } from "@playwright/test";

/**
 * [L4] ログイン経路のカナリア — デプロイ済み SWA から Entra のログイン画面まで。
 *
 * 資格情報なしで検証できる範囲: 「始める」→ MSAL の loginRedirect → Entra の
 * ログイン画面が **AADSTS エラーなしで表示される** こと。
 *
 * 検知できる実事故クラス: アプリ登録の破壊 (削除・リダイレクト URI 欠落 = AADSTS50011 /
 * SPA 種別違い)、認証無効ビルドの出荷 (VITE_ENTRA_* 空だとリダイレクトが起きない)。
 * 検知できないもの: サインイン後のトークン交換 (SP 不在 AADSTS500011 は認証後に出る)。
 * そこはシナリオテスト (実トークンで EasyAuth を通す) と人間の初回確認が補完する。
 */

const LIVE_APP_URL = process.env.LIVE_APP_URL ?? "";

test.skip(!LIVE_APP_URL, "LIVE_APP_URL が未設定 (実環境向けのみ実行)");

test("[L4] 始める → Entra ログイン画面がエラーなしで表示される", async ({ page }) => {
  await page.goto("/");

  // 認証ありビルドの初回はオンボーディング。「始める」で Entra へリダイレクトされる。
  // リダイレクトが起きない = 認証無効ビルド (VITE_ENTRA_* 空) の出荷なので落とす
  await page.getByRole("button", { name: "始める" }).click();
  await page.waitForURL(/login\.microsoftonline\.com/, { timeout: 60_000 });

  // AADSTS エラー画面 (アプリ登録破壊系) でなく、サインイン UI が出ていること
  const body = (await page.textContent("body")) ?? "";
  const aadsts = body.match(/AADSTS\d+/);
  expect(aadsts, `Entra がエラーを返した: ${aadsts?.[0] ?? ""}`).toBeNull();
  await expect(page.locator('input[type="email"], input[name="loginfmt"]').first()).toBeVisible({
    timeout: 30_000,
  });
});
