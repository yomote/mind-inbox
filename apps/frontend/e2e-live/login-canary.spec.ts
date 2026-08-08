import { expect, test } from "@playwright/test";

/**
 * [L4] ログイン経路のカナリア — デプロイ済み SWA から Entra のログイン画面まで。
 *
 * 未認証で開くとオンボーディングが表示され、「はじめる」で Entra サインインへ
 * 誘導される (#112 / onboarding.mdx)。ここでは「開く → オンボーディング表示 →
 * はじめる → Entra のログイン画面が AADSTS エラーなしで表示される」ことを検証する。
 *
 * 検知できる実事故クラス: アプリ登録の破壊 (削除・リダイレクト URI 欠落 = AADSTS50011 /
 * SPA 種別違い)、認証無効ビルドの出荷 (VITE_ENTRA_* 空だと "/" が /home へ抜けて
 * 「はじめる」が出ない)、#112 の退行 (読み込みだけで Entra へ飛ばされ、
 * オンボーディングの表示待ちが Entra 画面上でタイムアウトする)。
 * 検知できないもの: サインイン後のトークン交換 (SP 不在 AADSTS500011 は認証後に出る)。
 * そこはシナリオテスト (実トークンで EasyAuth を通す) と人間の初回確認が補完する。
 */

const LIVE_APP_URL = process.env.LIVE_APP_URL ?? "";

test.skip(!LIVE_APP_URL, "LIVE_APP_URL が未設定 (実環境向けのみ実行)");

test("[L4] 未認証で開く → オンボーディング → はじめる → Entra ログイン画面がエラーなしで表示される", async ({
  page,
}) => {
  // 読み込みだけで Entra へ飛ばされる (#112 の退行) と、この表示待ちが
  // login.microsoftonline.com 上でタイムアウトして落ちる。
  // 認証無効ビルド (VITE_ENTRA_* 空) の出荷でも "/" は /home へ抜けるためここで落ちる。
  await page.goto("/");
  const startButton = page.getByRole("button", { name: "はじめる" });
  await expect(startButton).toBeVisible({ timeout: 60_000 });

  // 「はじめる」で初めてサインインへ誘導される (onboarding.mdx)
  await startButton.click();
  await page.waitForURL(/login\.microsoftonline\.com/, { timeout: 60_000 });

  // AADSTS エラー画面 (アプリ登録破壊系) でなく、サインイン UI が出ていること
  const body = (await page.textContent("body")) ?? "";
  const aadsts = body.match(/AADSTS\d+/);
  expect(aadsts, `Entra がエラーを返した: ${aadsts?.[0] ?? ""}`).toBeNull();
  await expect(page.locator('input[type="email"], input[name="loginfmt"]').first()).toBeVisible({
    timeout: 30_000,
  });
});
